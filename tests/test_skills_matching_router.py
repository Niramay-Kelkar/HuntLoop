"""
Network-free unit tests for huntloop.skills_matching_router - the
Groq-primary / Gemini-fallback dispatcher wired into
scripts/backfill_skills_matching.py (Step K, 2026-08-29).

Both real backends are replaced with in-memory fakes (installed into
router._LOADED) so nothing hits Groq or Gemini. The router's own logic -
per-run exhaustion state, per-batch vs whole-run failover, batch-limit
switching, AllProvidersExhausted - is what's under test.

Dummy API keys are set before import: the router loads its first backend
(skills_matching / Groq) at import, which fails fast without a key.
"""
import os

os.environ.setdefault("GROQ_API_KEY", "test-dummy-key")
os.environ.setdefault("GEMINI_API_KEY", "test-dummy-key")

import pytest  # noqa: E402

from huntloop import skills_matching_router as router  # noqa: E402
from huntloop.skills_matching_errors import DailyQuotaExhausted, ProviderResponseInvalid  # noqa: E402


class FakeBackend:
    """Configurable stand-in for a skills-matching backend module."""

    def __init__(self, name, *, max_batch_size=5, max_batch_tokens=7000, target_tpm=6000, max_rpm=30):
        self.MODEL_NAME = f"fake-{name}"
        self._BATCH_SYSTEM_PROMPT = "PROMPT"
        self.MAX_BATCH_SIZE = max_batch_size
        self.MAX_BATCH_ESTIMATED_TOKENS = max_batch_tokens
        self.TARGET_TPM = target_tpm
        self.MAX_RPM = max_rpm
        self.calls = []
        self._script = []  # list of ("ok"|"quota"|"invalid"|"none") per call

    def program(self, *outcomes):
        self._script = list(outcomes)
        return self

    def match_skills_batch(self, resume_text, job_descriptions):
        n = len(job_descriptions)
        self.calls.append(n)
        outcome = self._script.pop(0) if self._script else "ok"
        if outcome == "quota":
            raise DailyQuotaExhausted(f"{self.MODEL_NAME}: daily quota gone")
        if outcome == "invalid":
            raise ProviderResponseInvalid(f"{self.MODEL_NAME}: json_validate_failed")
        if outcome == "none":
            return [None] * n
        return [{"matched_skills": [self.MODEL_NAME], "missing_skills": []} for _ in range(n)]


@pytest.fixture
def fakes(monkeypatch):
    groq = FakeBackend("groq", max_batch_tokens=7000, target_tpm=6000, max_rpm=30)
    gemini = FakeBackend("gemini", max_batch_tokens=16000, target_tpm=200000, max_rpm=14)
    monkeypatch.setattr(router, "PROVIDER_CHAIN", ["groq", "gemini"])
    monkeypatch.setitem(router._LOADED, "groq", groq)
    monkeypatch.setitem(router._LOADED, "gemini", gemini)
    return groq, gemini


def test_happy_path_stays_on_primary(fakes):
    groq, gemini = fakes
    state = router.make_run_state()
    out = router.match_skills_batch("r", ["a", "b"], state)
    assert [o["matched_skills"] for o in out] == [["fake-groq"], ["fake-groq"]]
    assert groq.calls == [2] and gemini.calls == []
    assert state["jobs_by"]["groq"] == 2 and state["batches_by"]["groq"] == 1


def test_daily_quota_fails_over_this_batch_and_all_later_ones(fakes):
    groq, gemini = fakes
    groq.program("quota")  # first (and only) groq call raises
    state = router.make_run_state()

    out1 = router.match_skills_batch("r", ["a", "b"], state)
    assert [o["matched_skills"] for o in out1] == [["fake-gemini"], ["fake-gemini"]]
    assert "groq" in state["exhausted"]
    assert state["daily_quota_hits"]["groq"] == 1

    # subsequent batch: groq is skipped entirely, no further groq call
    out2 = router.match_skills_batch("r", ["c"], state)
    assert out2[0]["matched_skills"] == ["fake-gemini"]
    assert groq.calls == [2]  # never called again
    assert gemini.calls == [2, 1]


def test_json_validate_failure_fails_over_only_that_batch(fakes):
    groq, gemini = fakes
    groq.program("invalid", "ok")  # 1st batch structural-fails, 2nd is fine
    state = router.make_run_state()

    out1 = router.match_skills_batch("r", ["a", "b"], state)
    assert [o["matched_skills"] for o in out1] == [["fake-gemini"], ["fake-gemini"]]
    assert state["batch_failover"]["groq"] == 1
    assert "groq" not in state["exhausted"]  # NOT marked down

    out2 = router.match_skills_batch("r", ["c"], state)
    assert out2[0]["matched_skills"] == ["fake-groq"]  # groq still primary
    assert groq.calls == [2, 1]


def test_all_providers_exhausted_raises(fakes):
    groq, gemini = fakes
    groq.program("quota")
    gemini.program("quota")
    state = router.make_run_state()
    with pytest.raises(router.AllProvidersExhausted):
        router.match_skills_batch("r", ["a"], state)
    assert state["exhausted"] == {"groq", "gemini"}


def test_all_providers_structural_fail_leaves_null(fakes):
    groq, gemini = fakes
    groq.program("invalid")
    gemini.program("invalid")
    state = router.make_run_state()
    out = router.match_skills_batch("r", ["a", "b"], state)
    assert out == [None, None]
    assert state["batch_giveups"] == 1
    assert state["exhausted"] == set()  # neither marked down


def test_batch_limits_switch_when_primary_exhausted(fakes):
    state = router.make_run_state()
    assert router.batch_limits(state) == (5, 7000, 6000, 30)
    state["exhausted"].add("groq")
    assert router.batch_limits(state) == (5, 16000, 200000, 14)
    assert router.active_provider(state) == "gemini"


def test_transient_none_is_passed_through_not_failed_over(fakes):
    groq, gemini = fakes
    groq.program("none")
    state = router.make_run_state()
    out = router.match_skills_batch("r", ["a", "b"], state)
    assert out == [None, None]
    assert gemini.calls == []  # a plain None is not a failover trigger
    assert state["batch_failover"] == {} and state["exhausted"] == set()


def test_three_provider_chain_fails_over_groq_then_gemini_then_mistral(monkeypatch):
    groq = FakeBackend("groq")
    gemini = FakeBackend("gemini", max_batch_tokens=16000, target_tpm=200000, max_rpm=14)
    mistral = FakeBackend("mistral", max_batch_tokens=30000, target_tpm=400000, max_rpm=55)
    monkeypatch.setattr(router, "PROVIDER_CHAIN", ["groq", "gemini", "mistral"])
    monkeypatch.setitem(router._LOADED, "groq", groq)
    monkeypatch.setitem(router._LOADED, "gemini", gemini)
    monkeypatch.setitem(router._LOADED, "mistral", mistral)

    groq.program("quota")
    gemini.program("quota")
    state = router.make_run_state()

    out = router.match_skills_batch("r", ["a", "b"], state)
    assert [o["matched_skills"] for o in out] == [["fake-mistral"], ["fake-mistral"]]
    assert state["exhausted"] == {"groq", "gemini"}
    assert router.active_provider(state) == "mistral"
    assert router.batch_limits(state) == (5, 30000, 400000, 55)

    # gemini also gone now -> straight to mistral, no wasted calls
    out2 = router.match_skills_batch("r", ["c"], state)
    assert out2[0]["matched_skills"] == ["fake-mistral"]
    assert groq.calls == [2] and gemini.calls == [2]

    mistral.program("quota")
    with pytest.raises(router.AllProvidersExhausted):
        router.match_skills_batch("r", ["d"], state)


def test_mistral_is_a_known_provider(monkeypatch):
    import importlib
    monkeypatch.setenv("SKILLS_MATCHING_PROVIDERS", "groq,gemini,mistral")
    mod = importlib.reload(router)
    try:
        assert mod.PROVIDER_CHAIN == ["groq", "gemini", "mistral"]
    finally:
        monkeypatch.setenv("SKILLS_MATCHING_PROVIDERS", "groq,gemini")
        importlib.reload(router)


def test_provider_chain_parsing(monkeypatch):
    import importlib
    monkeypatch.setenv("SKILLS_MATCHING_PROVIDERS", "groq")
    mod = importlib.reload(router)
    try:
        assert mod.PROVIDER_CHAIN == ["groq"]
    finally:
        monkeypatch.setenv("SKILLS_MATCHING_PROVIDERS", "groq,gemini")
        importlib.reload(router)


def test_unknown_provider_rejected(monkeypatch):
    import importlib
    monkeypatch.setenv("SKILLS_MATCHING_PROVIDERS", "groq,bogus")
    with pytest.raises(RuntimeError, match="unknown provider"):
        importlib.reload(router)
    monkeypatch.setenv("SKILLS_MATCHING_PROVIDERS", "groq,gemini")
    importlib.reload(router)

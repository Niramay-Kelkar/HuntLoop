"""
Network-free unit tests for huntloop.skills_matching_groq_120b - the
same-account Groq capacity stage targeting openai/gpt-oss-120b
(2026-09-04, see SESSIONS.md).

The live API path (and the real 11-job side-by-side validation against
the openai/gpt-oss-20b baseline) is covered by actually running
scripts/validate_groq_120b_skills_match.py, not here. These tests cover
the pure response-shaping / quota-classification logic that must stay
correct for the routing design to work - mirrors
tests/test_skills_matching_gemini.py's / test_skills_matching_mistral.py's
coverage shape, adapted to this backend's OpenAI-compatible (Groq SDK)
response objects instead of raw HTTP JSON.

Uses the same GROQ_API_KEY dummy default every other Groq-touching test
file already sets - this module deliberately reuses that single env var
(same account/key as the primary 20b backend), not a new one.
"""
import os

os.environ.setdefault("GROQ_API_KEY", "test-dummy-key")

import pytest  # noqa: E402
from groq import GroqError  # noqa: E402

from huntloop import skills_matching_groq_120b as g120  # noqa: E402


def test_contract_surface_matches_20b_backend():
    from huntloop import skills_matching as groq20b
    for attr in ("match_skills", "match_skills_batch", "MODEL_NAME",
                 "MAX_PLAUSIBLE_MATCHED_SKILLS", "DailyQuotaExhausted",
                 "ProviderResponseInvalid", "_SYSTEM_PROMPT", "_BATCH_SYSTEM_PROMPT"):
        assert hasattr(g120, attr), f"groq_120b backend missing {attr}"
    assert g120.MAX_PLAUSIBLE_MATCHED_SKILLS == groq20b.MAX_PLAUSIBLE_MATCHED_SKILLS


def test_model_name_is_the_120b_variant():
    assert g120.MODEL_NAME == "openai/gpt-oss-120b"


def test_prompts_are_byte_identical_to_the_20b_backend():
    # Same résumé-matching task, same model FAMILY (both Groq/gpt-oss) -
    # the prompt shouldn't drift between the two backends.
    from huntloop import skills_matching as groq20b
    assert g120._SYSTEM_PROMPT == groq20b._SYSTEM_PROMPT
    assert g120._BATCH_SYSTEM_PROMPT == groq20b._BATCH_SYSTEM_PROMPT


def test_batch_system_prompt_is_valid_json_shape_spec():
    assert '"missing_skills"' in g120._BATCH_SYSTEM_PROMPT
    assert '"results"' in g120._BATCH_SYSTEM_PROMPT
    assert g120._SYSTEM_PROMPT.count("{") == g120._SYSTEM_PROMPT.count("}")
    assert g120._BATCH_SYSTEM_PROMPT.count("{") == g120._BATCH_SYSTEM_PROMPT.count("}")


def test_pacing_constants_present_and_match_live_confirmed_limits():
    # Live-confirmed 2026-09-04: 1,000 RPD / 8,000 TPM for this model (see
    # the module docstring + SESSIONS.md). These constants must stay paced
    # under that, not equal to or above it.
    assert g120.MAX_BATCH_SIZE == 5
    assert g120.MAX_BATCH_ESTIMATED_TOKENS < 8_000
    assert g120.TARGET_TPM < 8_000


class _Choice:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


def test_match_skills_happy_path(monkeypatch):
    import json
    payload = {"matched_skills": ["Python"], "missing_skills": ["Rust"]}

    class FakeCompletions:
        def create(self, **kw):
            return _Resp(json.dumps(payload))

    class FakeClient:
        chat = type("C", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(g120, "_get_client", lambda: FakeClient())
    assert g120.match_skills("resume", "jd") == payload


def test_match_skills_batch_rejects_full_resume_dump(monkeypatch):
    import json
    dump = [f"skill{i}" for i in range(g120.MAX_PLAUSIBLE_MATCHED_SKILLS + 5)]
    payload = {"results": [
        {"job_index": 0, "matched_skills": dump, "missing_skills": []},
        {"job_index": 1, "matched_skills": ["Python"], "missing_skills": ["Go"]},
    ]}

    class FakeCompletions:
        def create(self, **kw):
            return _Resp(json.dumps(payload))

    class FakeClient:
        chat = type("C", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(g120, "_get_client", lambda: FakeClient())
    out = g120.match_skills_batch("resume", ["jd a", "jd b"])
    assert out[0] is None
    assert out[1] == {"matched_skills": ["Python"], "missing_skills": ["Go"]}


def test_match_skills_batch_missing_index_is_none(monkeypatch):
    import json
    payload = {"results": [{"job_index": 0, "matched_skills": [], "missing_skills": []}]}

    class FakeCompletions:
        def create(self, **kw):
            return _Resp(json.dumps(payload))

    class FakeClient:
        chat = type("C", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(g120, "_get_client", lambda: FakeClient())
    out = g120.match_skills_batch("resume", ["a", "b"])
    assert out[0] == {"matched_skills": [], "missing_skills": []}
    assert out[1] is None


def test_daily_quota_marker_raises(monkeypatch):
    class FakeCompletions:
        def create(self, **kw):
            raise GroqError("Rate limit reached for tokens per day (TPD)")

    class FakeClient:
        chat = type("C", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(g120, "_get_client", lambda: FakeClient())
    with pytest.raises(g120.DailyQuotaExhausted):
        g120.match_skills_batch("resume", ["a"])


def test_requests_per_day_marker_also_raises_daily_quota(monkeypatch):
    # Defensive marker (see the module docstring) - not itself observed
    # live, but exercised here so the classification logic is at least
    # proven correct if Groq's real message ever matches this phrasing.
    class FakeCompletions:
        def create(self, **kw):
            raise GroqError("Rate limit reached for requests per day (RPD)")

    class FakeClient:
        chat = type("C", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(g120, "_get_client", lambda: FakeClient())
    with pytest.raises(g120.DailyQuotaExhausted):
        g120.match_skills_batch("resume", ["a"])


def test_json_validate_failed_raises_provider_response_invalid(monkeypatch):
    class FakeCompletions:
        def create(self, **kw):
            raise GroqError("json_validate_failed: could not parse model output")

    class FakeClient:
        chat = type("C", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(g120, "_get_client", lambda: FakeClient())
    with pytest.raises(g120.ProviderResponseInvalid):
        g120.match_skills_batch("resume", ["a"])


def test_transient_error_returns_none_list(monkeypatch):
    class FakeCompletions:
        def create(self, **kw):
            raise GroqError("connection reset")

    class FakeClient:
        chat = type("C", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(g120, "_get_client", lambda: FakeClient())
    assert g120.match_skills_batch("resume", ["a", "b", "c"]) == [None, None, None]

"""
Network-free unit tests for huntloop.skills_matching_mistral - the
optional third skills-matching backend (2026-09-03, see SESSIONS.md),
behind Groq -> Gemini in the router.

The live API path is validated by actually running
scripts/validate_mistral_skills_match.py, not here. These tests cover the
pure response-shaping / quota-classification logic that must stay correct
for the routing design to work.

A dummy MISTRAL_API_KEY is set before import since the module fails fast
without one (same fail-fast contract as GROQ_API_KEY / GEMINI_API_KEY).
"""
import os

# All three backends fail fast without a key; this file imports the Groq
# and Gemini modules too (contract cross-checks), so set every default.
os.environ.setdefault("MISTRAL_API_KEY", "test-dummy-key")
os.environ.setdefault("GROQ_API_KEY", "test-dummy-key")
os.environ.setdefault("GEMINI_API_KEY", "test-dummy-key")

import pytest  # noqa: E402

from huntloop import skills_matching_mistral as m  # noqa: E402


def test_contract_surface_matches_groq_backend():
    from huntloop import skills_matching as groq
    for attr in ("match_skills", "match_skills_batch", "MODEL_NAME",
                 "MAX_PLAUSIBLE_MATCHED_SKILLS", "DailyQuotaExhausted",
                 "_SYSTEM_PROMPT", "_BATCH_SYSTEM_PROMPT"):
        assert hasattr(m, attr), f"mistral backend missing {attr}"
    assert m.MAX_PLAUSIBLE_MATCHED_SKILLS == groq.MAX_PLAUSIBLE_MATCHED_SKILLS


def test_pacing_constants_present_and_sane():
    # The router reads all four off the module (batch_limits()). Bounds
    # here are the real live-header limits for ministral-8b-latest on the
    # free tier (625,000 tokens/min, 188 req/min - see the module).
    assert m.MAX_BATCH_SIZE == 5
    assert m.MAX_BATCH_ESTIMATED_TOKENS >= 20_000  # 128K context, no forced cut
    assert m.TARGET_TPM <= 625_000                 # under the real free-tier TPM
    assert m.MAX_RPM <= 188                        # under the real req/min cap


def test_batch_system_prompt_is_valid_json_shape_spec():
    assert '"missing_skills"' in m._BATCH_SYSTEM_PROMPT
    assert '"results"' in m._BATCH_SYSTEM_PROMPT
    assert m._SYSTEM_PROMPT.count("{") == m._SYSTEM_PROMPT.count("}")
    assert m._BATCH_SYSTEM_PROMPT.count("{") == m._BATCH_SYSTEM_PROMPT.count("}")


def test_batch_prompt_is_byte_identical_to_gemini_backend():
    # The router feeds skills_matching_router._BATCH_SYSTEM_PROMPT into the
    # backfill's token estimation and assumes every backend's batch prompt
    # is the same length. Gemini/Mistral share the résumé-grounding wording.
    from huntloop import skills_matching_gemini as g
    assert m._BATCH_SYSTEM_PROMPT == g._BATCH_SYSTEM_PROMPT
    assert m._SYSTEM_PROMPT == g._SYSTEM_PROMPT


def test_extract_text_happy_path():
    body = {"choices": [{"message": {"content": '{"matched_skills": []}'}}]}
    assert m._extract_text(body) == '{"matched_skills": []}'


@pytest.mark.parametrize("body", [
    {},
    {"choices": []},
    {"choices": [{"message": {}}]},
    {"choices": [{"message": {"content": ""}}]},
    {"choices": [{"message": {"content": None}}]},
    {"choices": [{"finish_reason": "length"}]},
])
def test_extract_text_bad_shapes_return_none(body):
    assert m._extract_text(body) is None


def test_monthly_quota_markers_distinguish_month_cap_from_transient_429():
    monthly_examples = [
        "You have exceeded your monthly token quota",
        "Requests limit exceeded: tokens per month",
        "monthly usage limit exceeded for this workspace",
    ]
    for ex in monthly_examples:
        assert any(mk in ex.lower() for mk in m._MONTHLY_QUOTA_MARKERS), ex

    transient = [
        "Requests rate limit exceeded",
        "Service tier capacity exceeded for this model, please retry",
        "Too many requests, slow down (1 request per second)",
    ]
    for ex in transient:
        assert not any(mk in ex.lower() for mk in m._MONTHLY_QUOTA_MARKERS), ex


def test_match_skills_batch_rejects_full_resume_dump(monkeypatch):
    dump = [f"skill{i}" for i in range(m.MAX_PLAUSIBLE_MATCHED_SKILLS + 5)]
    payload = {"results": [
        {"job_index": 0, "matched_skills": dump, "missing_skills": []},
        {"job_index": 1, "matched_skills": ["Python"], "missing_skills": ["Go"]},
    ]}
    monkeypatch.setattr(m, "_generate", lambda *a, **k: __import__("json").dumps(payload))
    out = m.match_skills_batch("resume", ["jd a", "jd b"])
    assert out[0] is None                      # dump rejected -> left NULL
    assert out[1] == {"matched_skills": ["Python"], "missing_skills": ["Go"]}


def test_match_skills_batch_whole_batch_failure_is_all_none(monkeypatch):
    monkeypatch.setattr(m, "_generate", lambda *a, **k: None)
    assert m.match_skills_batch("resume", ["a", "b", "c"]) == [None, None, None]


def test_match_skills_batch_missing_index_is_none(monkeypatch):
    payload = {"results": [{"job_index": 0, "matched_skills": [], "missing_skills": []}]}
    monkeypatch.setattr(m, "_generate", lambda *a, **k: __import__("json").dumps(payload))
    out = m.match_skills_batch("resume", ["a", "b"])
    assert out[0] == {"matched_skills": [], "missing_skills": []}
    assert out[1] is None


def test_match_skills_happy_path(monkeypatch):
    payload = {"matched_skills": ["Python"], "missing_skills": ["Rust"]}
    monkeypatch.setattr(m, "_generate", lambda *a, **k: __import__("json").dumps(payload))
    assert m.match_skills("resume", "jd") == payload


def test_generate_raises_daily_quota_on_monthly_429(monkeypatch):
    class FakeResp:
        status_code = 429
        text = "Error: you have exceeded your monthly token quota for the free tier"

        def json(self):  # pragma: no cover - not reached on 429
            return {}

    monkeypatch.setattr(m.requests, "post", lambda *a, **k: FakeResp())
    with pytest.raises(m.DailyQuotaExhausted):
        m._generate("sys", "user")


def test_generate_transient_429_returns_none(monkeypatch):
    class FakeResp:
        status_code = 429
        text = "Requests rate limit exceeded"

        def json(self):  # pragma: no cover
            return {}

    monkeypatch.setattr(m.requests, "post", lambda *a, **k: FakeResp())
    assert m._generate("sys", "user") is None

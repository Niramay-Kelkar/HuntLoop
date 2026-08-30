"""
Network-free unit tests for huntloop.skills_matching_gemini - the
experimental, unwired Gemini backend (2026-08-29, see SESSIONS.md).

The live API path is validated by actually running
scripts/validate_gemini_skills_match.py, not here. These tests cover the
pure response-shaping / quota-classification logic that must stay
correct for the routing design to work.

A dummy GEMINI_API_KEY is set before import since the module fails fast
without one (same fail-fast contract as GROQ_API_KEY).
"""
import os

os.environ.setdefault("GEMINI_API_KEY", "test-dummy-key")

import pytest  # noqa: E402

from huntloop import skills_matching_gemini as g  # noqa: E402


def test_contract_surface_matches_groq_backend():
    from huntloop import skills_matching as groq
    for attr in ("match_skills", "match_skills_batch", "MODEL_NAME",
                 "MAX_PLAUSIBLE_MATCHED_SKILLS", "DailyQuotaExhausted",
                 "_BATCH_SYSTEM_PROMPT"):
        assert hasattr(g, attr), f"gemini backend missing {attr}"
    assert g.MAX_PLAUSIBLE_MATCHED_SKILLS == groq.MAX_PLAUSIBLE_MATCHED_SKILLS


def test_system_prompt_is_valid_json_shape_spec():
    # The earlier draft had a stray brace here - guard against regressing.
    assert '"missing_skills"' in g._SYSTEM_PROMPT
    assert g._SYSTEM_PROMPT.count("{") == g._SYSTEM_PROMPT.count("}")


def test_extract_text_happy_path():
    body = {"candidates": [{"content": {"parts": [{"text": '{"matched_skills": []}'}]}}]}
    assert g._extract_text(body) == '{"matched_skills": []}'


@pytest.mark.parametrize("body", [
    {},
    {"candidates": []},
    {"candidates": [{"content": {}}]},
    {"candidates": [{"content": {"parts": []}}]},
    {"candidates": [{"content": {"parts": [{"inlineData": "x"}]}}]},
    {"candidates": [{"finishReason": "SAFETY"}]},
])
def test_extract_text_bad_shapes_return_none(body):
    assert g._extract_text(body) is None


def test_per_day_markers_cover_common_gemini_quota_metric_names():
    # Real free-tier 429 payloads name the daily metric; a per-minute 429
    # names a *PerMinute metric. Make sure our classifier catches the
    # daily ones (case-insensitive - _generate lowercases before matching).
    daily_examples = [
        "quota exceeded ... limit: 500 ... Requests per day",
        "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
        "exceeded quota ... RequestsPerDay",
    ]
    for ex in daily_examples:
        assert any(m in ex.lower() for m in g._PER_DAY_MARKERS), ex

    per_minute = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    assert not any(m in per_minute.lower() for m in g._PER_DAY_MARKERS)

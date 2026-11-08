"""
Unit tests for huntloop.deep_dive - prompt assembly, output validation/
capping, and the three-stage provider chain's failover.

No real provider call is ever made: the Groq SDK is stubbed through
sys.modules (the module imports `groq` lazily inside _via_groq, exactly
so it stays importable in an environment without keys - see its
docstring) and `requests` is monkeypatched for the Gemini stage. No key
is needed and nothing touches the network.
"""
import json
import sys
import types

import pytest

from huntloop.deep_dive import (
    MAX_LIST_ITEMS,
    MAX_SUMMARY_CHARS,
    RESULT_VERSION,
    AllDeepDiveProvidersFailed,
    build_user_content,
    generate_deep_dive,
    validate_deep_dive_result,
)


@pytest.fixture(autouse=True)
def _no_provider_keys(monkeypatch):
    """Every test starts with NO provider keys, so a test that forgets
    to stub a stage gets a deterministic skip rather than a real call
    using whatever is in this machine's .env."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def _valid_payload(**overrides):
    payload = {
        "summary": "A solid backend match with one real infrastructure gap.",
        "fit_rating": "moderate",
        "strengths": [{"point": "Python depth", "resume_evidence": "Four years of Django at Acme."}],
        "gaps": [
            {
                "skill": "Kubernetes",
                "importance": "high",
                "why_it_matters": "The team runs its own clusters.",
                "how_to_close": "Ship a small service on a managed cluster.",
            }
        ],
        "resume_edits": [{"section": "Experience", "suggestion": "Quantify the migration's impact."}],
        "interview_focus": ["Walk through the Django migration"],
        "caveats": ["Resume text was extracted from a PDF and may be incomplete."],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------
# validate_deep_dive_result
# ---------------------------------------------------------------------


def test_validates_a_well_formed_payload_and_stamps_the_version():
    result = validate_deep_dive_result(_valid_payload())
    assert result is not None
    assert result["version"] == RESULT_VERSION
    assert result["fit_rating"] == "moderate"
    assert result["strengths"][0]["point"] == "Python depth"
    assert result["gaps"][0]["importance"] == "high"


@pytest.mark.parametrize("bad", [None, "a string", 42, [], {"summary": "only this"}])
def test_rejects_non_dict_and_missing_keys(bad):
    assert validate_deep_dive_result(bad) is None


def test_rejects_a_missing_or_empty_summary():
    assert validate_deep_dive_result(_valid_payload(summary="")) is None
    assert validate_deep_dive_result(_valid_payload(summary=None)) is None


def test_rejects_a_fit_rating_outside_the_allowed_set():
    assert validate_deep_dive_result(_valid_payload(fit_rating="excellent")) is None


def test_normalizes_fit_rating_and_importance_case():
    """Models routinely return "Strong"/"High" for an enum documented
    lowercase - accepted and lowercased rather than failed over."""
    result = validate_deep_dive_result(
        _valid_payload(
            fit_rating="Strong",
            gaps=[
                {
                    "skill": "Terraform",
                    "importance": "LOW",
                    "why_it_matters": "Infra is codified.",
                    "how_to_close": "Write a module.",
                }
            ],
        )
    )
    assert result["fit_rating"] == "strong"
    assert result["gaps"][0]["importance"] == "low"


def test_empty_list_sections_are_allowed_not_a_validation_failure():
    """A genuinely strong candidate can have no gaps worth naming - an
    empty section must not fail the whole result over."""
    result = validate_deep_dive_result(
        _valid_payload(strengths=[], gaps=[], resume_edits=[], interview_focus=[], caveats=[])
    )
    assert result is not None
    assert result["gaps"] == []
    assert result["interview_focus"] == []


def test_caps_list_lengths():
    result = validate_deep_dive_result(
        _valid_payload(
            interview_focus=[f"item {i}" for i in range(40)],
            strengths=[{"point": f"p{i}", "resume_evidence": "e"} for i in range(40)],
        )
    )
    assert len(result["interview_focus"]) == MAX_LIST_ITEMS
    assert len(result["strengths"]) == MAX_LIST_ITEMS


def test_caps_string_lengths():
    result = validate_deep_dive_result(_valid_payload(summary="x" * 50_000))
    assert len(result["summary"]) == MAX_SUMMARY_CHARS


def test_drops_object_entries_missing_a_required_field():
    """A half-formed strengths entry (no resume_evidence) is dropped
    rather than rendered with a hole in it - the grounding rule makes
    evidence the whole point of the section."""
    result = validate_deep_dive_result(
        _valid_payload(
            strengths=[
                {"point": "Has evidence", "resume_evidence": "Really does."},
                {"point": "No evidence at all"},
                "not even a dict",
            ]
        )
    )
    assert [s["point"] for s in result["strengths"]] == ["Has evidence"]


def test_drops_a_gap_with_an_unrecognized_importance():
    result = validate_deep_dive_result(
        _valid_payload(
            gaps=[
                {"skill": "A", "importance": "critical", "why_it_matters": "w", "how_to_close": "h"},
                {"skill": "B", "importance": "medium", "why_it_matters": "w", "how_to_close": "h"},
            ]
        )
    )
    assert [g["skill"] for g in result["gaps"]] == ["B"]


def test_non_list_sections_normalize_to_empty_lists():
    result = validate_deep_dive_result(_valid_payload(strengths="nope", caveats={"a": 1}))
    assert result["strengths"] == []
    assert result["caveats"] == []


# ---------------------------------------------------------------------
# build_user_content
# ---------------------------------------------------------------------


def test_user_content_includes_every_input_and_the_skill_lists():
    content = build_user_content(
        resume_text="RESUME BODY HERE",
        job_title="Backend Engineer",
        company_name="Acme",
        job_description="JOB BODY HERE",
        matched_skills=["Python", "SQL"],
        missing_skills=["Kubernetes"],
    )
    assert "Backend Engineer" in content
    assert "Acme" in content
    assert "RESUME BODY HERE" in content
    assert "JOB BODY HERE" in content
    assert "Python, SQL" in content
    assert "Kubernetes" in content


def test_user_content_handles_absent_skills_and_empty_text():
    content = build_user_content("", "", "", "", None, None)
    assert "(not available)" in content
    assert "(not given)" in content


def test_user_content_truncates_very_long_inputs():
    content = build_user_content("r" * 100_000, "T", "C", "j" * 100_000, [], [])
    # Both inputs are individually capped, so the whole prompt stays
    # bounded well under the raw 200k characters handed in.
    assert len(content) < 45_000


# ---------------------------------------------------------------------
# Provider chain / failover
# ---------------------------------------------------------------------


class _FakeGroqError(Exception):
    pass


def _install_fake_groq(monkeypatch, responses):
    """Stubs the `groq` module. `responses` is a list consumed one entry
    per call: a string (returned as message content) or an Exception
    instance (raised)."""
    calls = []

    class _FakeGroq:
        def __init__(self, api_key=None):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=self._create)
            )

        def _create(self, **kwargs):
            calls.append(kwargs["model"])
            outcome = responses.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            message = types.SimpleNamespace(content=outcome)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    fake = types.ModuleType("groq")
    fake.Groq = _FakeGroq
    fake.GroqError = _FakeGroqError
    monkeypatch.setitem(sys.modules, "groq", fake)
    return calls


def test_first_groq_stage_success_short_circuits_the_chain(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    calls = _install_fake_groq(monkeypatch, [json.dumps(_valid_payload())])

    result = generate_deep_dive("resume", "Title", "Co", "jd", [], [])

    assert result["version"] == RESULT_VERSION
    assert calls == ["openai/gpt-oss-120b"]


def test_falls_over_from_120b_to_20b_on_invalid_json(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    calls = _install_fake_groq(monkeypatch, ["not json at all", json.dumps(_valid_payload())])

    result = generate_deep_dive("resume", "Title", "Co", "jd", [], [])

    assert result["fit_rating"] == "moderate"
    assert calls == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def test_falls_over_on_a_wrong_shaped_but_valid_json_response(monkeypatch):
    """Parseable JSON that fails validation is treated exactly like a
    provider error - the next stage gets a turn."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    calls = _install_fake_groq(
        monkeypatch, [json.dumps({"summary": "only this"}), json.dumps(_valid_payload())]
    )

    assert generate_deep_dive("r", "T", "C", "j", [], []) is not None
    assert calls == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def test_falls_over_from_both_groq_stages_to_gemini(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-fake")
    groq_calls = _install_fake_groq(
        monkeypatch, [_FakeGroqError("rate limited"), _FakeGroqError("still bad")]
    )

    import requests

    def _fake_post(url, **kwargs):
        body = {
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(_valid_payload(fit_rating="stretch"))}]}}
            ]
        }
        return types.SimpleNamespace(status_code=200, json=lambda: body, text="")

    monkeypatch.setattr(requests, "post", _fake_post)

    result = generate_deep_dive("r", "T", "C", "j", [], [])

    assert result["fit_rating"] == "stretch"
    assert groq_calls == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def test_raises_when_every_provider_fails(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-fake")
    _install_fake_groq(monkeypatch, [_FakeGroqError("a"), _FakeGroqError("b")])

    import requests

    monkeypatch.setattr(
        requests, "post", lambda url, **kw: types.SimpleNamespace(status_code=500, json=lambda: {}, text="")
    )

    with pytest.raises(AllDeepDiveProvidersFailed):
        generate_deep_dive("r", "T", "C", "j", [], [])


def test_raises_when_no_provider_is_configured_at_all():
    """The Modal container is the only place this normally runs, and it
    gets its keys from the huntloop-llm Secret - if that secret is
    missing or empty, every stage is skipped and this must be a real
    error, not an empty result."""
    with pytest.raises(AllDeepDiveProvidersFailed):
        generate_deep_dive("r", "T", "C", "j", [], [])


def test_gemini_is_skipped_without_a_key_and_the_error_still_raises(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    _install_fake_groq(monkeypatch, [_FakeGroqError("a"), _FakeGroqError("b")])

    import requests

    def _must_not_be_called(*args, **kwargs):  # pragma: no cover
        raise AssertionError("Gemini must not be called with no GEMINI_API_KEY set")

    monkeypatch.setattr(requests, "post", _must_not_be_called)

    with pytest.raises(AllDeepDiveProvidersFailed):
        generate_deep_dive("r", "T", "C", "j", [], [])

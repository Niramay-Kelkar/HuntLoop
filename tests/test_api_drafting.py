"""
Integration tests for POST /jobs/{job_id}/draft-answer
(huntloop.api.routers.drafting / huntloop.drafting), against the
isolated test schema (see tests/conftest.py's api_client fixture) -
never touches real production data, and never makes a real network call
to Groq/Gemini - the provider SDK/HTTP layer is monkeypatched so these
tests exercise this project's own routing/error-mapping/rate-limiting
logic deterministically, not a third party's live API behavior.

huntloop.drafting.Groq is patched at the class level (huntloop.drafting.
Groq, not the groq package itself) with a stub whose
.chat.completions.create(...) either returns a fake successful response
or raises a REAL groq exception instance (groq.AuthenticationError /
groq.RateLimitError / groq.APIConnectionError, constructed with a real
httpx.Response/Request) - so huntloop.drafting's own except-clause
routing (which except clause fires for which real exception type) is
exercised for real, only the network transport is faked.
"""
import httpx
import groq
import pytest

from huntloop.api.routers.drafting import _rate_limiter
from huntloop.db_models import Company, JobPosting, ResumeVersion


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The FastAPI `app` (and therefore _rate_limiter, a module-level
    singleton) is shared across the whole test session - reset it before
    AND after every test so one test's requests never count against
    another's rate-limit window."""
    _rate_limiter.reset()
    yield
    _rate_limiter.reset()


def _seed(db_session, *, with_resume: bool = True, job_description: str | None = "We need a Python backend engineer."):
    company = Company(name="acme", ats_platform="greenhouse")
    db_session.add(company)
    db_session.commit()

    job = JobPosting(
        job_title="Backend Engineer",
        job_url="https://example.com/jobs/1",
        job_description=job_description,
        company_id=company.id,
    )
    db_session.add(job)

    if with_resume:
        resume = ResumeVersion(
            version_number=1,
            file_path="test.pdf",
            extracted_text="Experienced Python developer with 5 years building REST APIs.",
            is_active=True,
        )
        db_session.add(resume)

    db_session.commit()
    return job


def _fake_groq_response(text: str):
    class _Message:
        content = text

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]

    return _Response()


class _StubGroqClient:
    """Stands in for a real `groq.Groq(api_key=...)` client instance."""

    def __init__(self, behavior):
        self._behavior = behavior

        class _Completions:
            def create(_self, **kwargs):
                return behavior(kwargs)

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def _patch_groq(monkeypatch, behavior):
    def _fake_ctor(api_key):
        assert api_key, "Groq(api_key=...) must always receive a real key argument"
        return _StubGroqClient(behavior)

    monkeypatch.setattr("huntloop.drafting.Groq", _fake_ctor)


def _groq_auth_error(*_args, **_kwargs):
    resp = httpx.Response(401, request=httpx.Request("POST", "https://api.groq.com/x"), json={"error": {"message": "Invalid API Key"}})
    raise groq.AuthenticationError("Invalid API Key", response=resp, body={})


def _groq_rate_limit_error(*_args, **_kwargs):
    resp = httpx.Response(429, request=httpx.Request("POST", "https://api.groq.com/x"), json={"error": {"message": "rate limited"}})
    raise groq.RateLimitError("rate limited", response=resp, body={})


def _groq_connection_error(*_args, **_kwargs):
    raise groq.APIConnectionError(request=httpx.Request("POST", "https://api.groq.com/x"))


def test_missing_api_key_returns_400(api_client, db_session):
    job = _seed(db_session)
    resp = api_client.post(
        f"/jobs/{job.id}/draft-answer",
        json={"prompt": "Draft a why-this-company answer.", "provider": "groq"},
    )
    assert resp.status_code == 400
    assert "api_key" in resp.json()["detail"]


def test_blank_api_key_returns_400(api_client, db_session):
    job = _seed(db_session)
    resp = api_client.post(
        f"/jobs/{job.id}/draft-answer",
        json={"prompt": "Draft a why-this-company answer.", "provider": "groq", "api_key": "   "},
    )
    assert resp.status_code == 400


def test_invalid_api_key_returns_401(api_client, db_session, monkeypatch):
    job = _seed(db_session)
    _patch_groq(monkeypatch, _groq_auth_error)

    resp = api_client.post(
        f"/jobs/{job.id}/draft-answer",
        json={"prompt": "Draft a why-this-company answer.", "provider": "groq", "api_key": "gsk_garbage_not_a_real_key"},
    )
    assert resp.status_code == 401
    assert "rejected" in resp.json()["detail"].lower()


def test_provider_rate_limit_returns_429(api_client, db_session, monkeypatch):
    job = _seed(db_session)
    _patch_groq(monkeypatch, _groq_rate_limit_error)

    resp = api_client.post(
        f"/jobs/{job.id}/draft-answer",
        json={"prompt": "Draft a why-this-company answer.", "provider": "groq", "api_key": "gsk_some_key"},
    )
    assert resp.status_code == 429
    assert "provider reported a rate limit" in resp.json()["detail"].lower()


def test_provider_call_failure_returns_502(api_client, db_session, monkeypatch):
    job = _seed(db_session)
    _patch_groq(monkeypatch, _groq_connection_error)

    resp = api_client.post(
        f"/jobs/{job.id}/draft-answer",
        json={"prompt": "Draft a why-this-company answer.", "provider": "groq", "api_key": "gsk_some_key"},
    )
    assert resp.status_code == 502
    assert "provider call failed" in resp.json()["detail"].lower()


def test_job_not_found_returns_404(api_client, db_session):
    resp = api_client.post(
        "/jobs/999999/draft-answer",
        json={"prompt": "Draft a why-this-company answer.", "provider": "groq", "api_key": "gsk_some_key"},
    )
    assert resp.status_code == 404


def test_no_active_resume_returns_400(api_client, db_session):
    job = _seed(db_session, with_resume=False)
    resp = api_client.post(
        f"/jobs/{job.id}/draft-answer",
        json={"prompt": "Draft a why-this-company answer.", "provider": "groq", "api_key": "gsk_some_key"},
    )
    assert resp.status_code == 400
    assert "no active resume" in resp.json()["detail"].lower()


def test_unsupported_provider_returns_422(api_client, db_session):
    job = _seed(db_session)
    resp = api_client.post(
        f"/jobs/{job.id}/draft-answer",
        json={"prompt": "Draft a why-this-company answer.", "provider": "openai", "api_key": "sk_some_key"},
    )
    assert resp.status_code == 422


def test_successful_draft_returns_200_with_answer(api_client, db_session, monkeypatch):
    job = _seed(db_session)
    drafted_text = "I'm excited to apply because my 5 years of Python backend experience directly matches this role."

    captured_kwargs = {}

    def _success(kwargs):
        captured_kwargs.update(kwargs)
        return _fake_groq_response(drafted_text)

    _patch_groq(monkeypatch, _success)

    resp = api_client.post(
        f"/jobs/{job.id}/draft-answer",
        json={"prompt": "Draft a why-this-company answer.", "provider": "groq", "api_key": "gsk_real_looking_key_abc123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"answer": drafted_text, "provider": "groq"}

    # The system prompt actually carries the real job/resume data looked
    # up server-side, and the user prompt is passed through unchanged -
    # confirms the request truly is grounded, not just "the mock returned
    # something".
    messages = captured_kwargs["messages"]
    system_content = messages[0]["content"]
    assert "Backend Engineer" in system_content
    assert "acme" in system_content
    assert "Python developer with 5 years" in system_content
    assert messages[1]["content"] == "Draft a why-this-company answer."


def test_rate_limit_triggers_after_threshold(api_client, db_session, monkeypatch):
    job = _seed(db_session)
    _patch_groq(monkeypatch, lambda kwargs: _fake_groq_response("ok"))

    payload = {"prompt": "Draft a why-this-company answer.", "provider": "groq", "api_key": "gsk_some_key"}

    statuses = [api_client.post(f"/jobs/{job.id}/draft-answer", json=payload).status_code for _ in range(10)]
    assert all(s == 200 for s in statuses), statuses

    eleventh = api_client.post(f"/jobs/{job.id}/draft-answer", json=payload)
    assert eleventh.status_code == 429
    assert "rate limit exceeded for this endpoint" in eleventh.json()["detail"].lower()

"""
Integration tests for the Resume Deep-Dive endpoints
(huntloop.api.routers.deep_dive), against the isolated test schema (see
tests/conftest.py's api_client fixture) - never touches real production
data.

EVERYTHING Modal is mocked: modal.Function.from_name, the handle's
.spawn(), modal.FunctionCall.from_id and its .get(), plus the
still-running (modal.exception.TimeoutError) and expired-output
(OutputExpiredError) cases. No token is needed, nothing reaches the
network, and the real deployed app is never contacted - the one real
end-to-end verification against a live Modal deployment is a separate
manual step, after the huntloop-llm Secret exists.

Two non-obvious things worth knowing while reading this file:

  1. The still-running signal is modal.exception.TimeoutError, which is
     NOT a subclass of the builtin TimeoutError, and
     modal.exception.OutputExpiredError IS a subclass of modal's. Both
     are asserted here with the real modal exception classes rather
     than stand-ins, precisely so this stays honest about that
     hierarchy (see huntloop.modal_deep_dive's comment on the catch
     order).
  2. MODAL_TOKEN_ID/MODAL_TOKEN_SECRET are cleared for every test by
     default and set explicitly by the `configured` fixture - same
     hermeticity guard tests/test_api_resumes.py and
     tests/test_modal_resume_processing.py already use, since
     huntloop.modal_resume_processing calls load_dotenv() at import and
     would otherwise pick up a real developer's .env.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import modal.exception
import pytest
from sqlalchemy import func, text

from huntloop.api.routers.deep_dive import _rate_limiter
from huntloop.db_models import (
    Company,
    DeepDiveStatus,
    JobPosting,
    JobSource,
    ModalUsage,
    ResumeDeepDive,
    ResumeSkillMatch,
    ResumeVersion,
)
from huntloop.modal_resume_processing import _current_month_key


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The limiter is a module-level singleton shared across the whole
    test session - same reasoning as test_api_drafting.py's own reset."""
    _rate_limiter.reset()
    yield
    _rate_limiter.reset()


@pytest.fixture(autouse=True)
def _modal_not_configured(monkeypatch):
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("MODAL_MONTHLY_INVOCATION_BUDGET", raising=False)
    monkeypatch.delenv("MODAL_DEEP_DIVE_WEIGHT", raising=False)
    monkeypatch.delenv("DEEP_DIVE_STALE_MINUTES", raising=False)


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-test")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "as-test")


VALID_RESULT = {
    "summary": "Strong backend overlap with one infrastructure gap.",
    "fit_rating": "strong",
    "strengths": [{"point": "Python depth", "resume_evidence": "Four years of Django."}],
    "gaps": [
        {
            "skill": "Kubernetes",
            "importance": "high",
            "why_it_matters": "The team self-hosts.",
            "how_to_close": "Deploy a service to a managed cluster.",
        }
    ],
    "resume_edits": [{"section": "Experience", "suggestion": "Quantify the migration."}],
    "interview_focus": ["The Django migration"],
    "caveats": ["Extracted from a PDF; may be incomplete."],
}


def _seed(db_session, with_resume=True, with_skills=False):
    company = Company(name="acme")
    source = JobSource(name="greenhouse_api")
    db_session.add_all([company, source])
    db_session.commit()

    job = JobPosting(
        job_title="Backend Engineer",
        job_url="https://example.com/jobs/backend",
        job_description="We need Python and Kubernetes.",
        company_id=company.id,
        source_id=source.id,
    )
    db_session.add(job)
    db_session.commit()

    resume = None
    if with_resume:
        resume = ResumeVersion(
            version_number=1,
            file_path="data/resumes/fake_v1.pdf",
            extracted_text="Four years of Django at Acme.",
            is_active=True,
        )
        db_session.add(resume)
        db_session.commit()

    if with_skills and resume is not None:
        db_session.add(
            ResumeSkillMatch(
                job_posting_id=job.id,
                resume_version_id=resume.id,
                matched_skills=["Python"],
                missing_skills=["Kubernetes"],
            )
        )
        db_session.commit()

    return job, resume


def _spawn_patch(call_id="fc-123"):
    """Patches modal.Function.from_name so .spawn() returns a handle
    carrying `call_id` as its object_id, the attribute
    huntloop.modal_deep_dive reads."""
    handle = MagicMock()
    handle.spawn.return_value = MagicMock(object_id=call_id)
    return patch("modal.Function.from_name", return_value=handle), handle


# ---------------------------------------------------------------------
# POST /jobs/{job_id}/deep-dive
# ---------------------------------------------------------------------


def test_post_returns_404_for_an_unknown_job(api_client, db_session, configured):
    resp = api_client.post("/jobs/99999/deep-dive")
    assert resp.status_code == 404
    assert resp.json()["code"] == "job_not_found"
    assert "99999" in resp.json()["detail"]


def test_post_returns_400_when_there_is_no_active_resume(api_client, db_session, configured):
    job, _ = _seed(db_session, with_resume=False)
    resp = api_client.post(f"/jobs/{job.id}/deep-dive")
    assert resp.status_code == 400
    assert resp.json()["code"] == "no_active_resume"


def test_post_dispatches_and_returns_202_running(api_client, db_session, configured):
    job, resume = _seed(db_session, with_skills=True)
    spawn, handle = _spawn_patch()

    with spawn:
        resp = api_client.post(f"/jobs/{job.id}/deep-dive")

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "running"
    assert body["job_posting_id"] == job.id
    assert body["resume_version_id"] == resume.id
    assert body["result"] is None
    assert body["error"] is None

    row = db_session.get(ResumeDeepDive, body["id"])
    db_session.refresh(row)
    assert row.status == DeepDiveStatus.RUNNING
    assert row.modal_call_id == "fc-123"
    assert row.dispatched_at is not None

    # The payload is plain data only - no DB handle, no ORM object - and
    # carries the precomputed skills from resume_skill_matches.
    payload = handle.spawn.call_args[0][0]
    assert payload["job_title"] == "Backend Engineer"
    assert payload["company_name"] == "acme"
    assert payload["matched_skills"] == ["Python"]
    assert payload["missing_skills"] == ["Kubernetes"]
    assert "Django" in payload["resume_text"]


def test_post_dispatches_fine_with_no_precomputed_skills(api_client, db_session, configured):
    """Most postings have no resume_skill_matches row yet (the backfill
    works through the backlog over days) - that's optional context, not
    a prerequisite."""
    job, _ = _seed(db_session, with_skills=False)
    spawn, handle = _spawn_patch()

    with spawn:
        resp = api_client.post(f"/jobs/{job.id}/deep-dive")

    assert resp.status_code == 202
    payload = handle.spawn.call_args[0][0]
    assert payload["matched_skills"] == []
    assert payload["missing_skills"] == []


def test_post_does_not_redispatch_a_fresh_running_row(api_client, db_session, configured):
    job, _ = _seed(db_session)
    spawn, handle = _spawn_patch()

    with spawn:
        first = api_client.post(f"/jobs/{job.id}/deep-dive")
        second = api_client.post(f"/jobs/{job.id}/deep-dive")

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    assert handle.spawn.call_count == 1
    assert db_session.query(ResumeDeepDive).count() == 1


def test_post_returns_200_and_the_full_record_when_already_done(api_client, db_session, configured):
    job, resume = _seed(db_session)
    db_session.add(
        ResumeDeepDive(
            job_posting_id=job.id,
            resume_version_id=resume.id,
            status=DeepDiveStatus.DONE,
            result=VALID_RESULT,
        )
    )
    db_session.commit()

    spawn, handle = _spawn_patch()
    with spawn:
        resp = api_client.post(f"/jobs/{job.id}/deep-dive")

    assert resp.status_code == 200
    assert resp.json()["status"] == "done"
    assert resp.json()["result"]["fit_rating"] == "strong"
    handle.spawn.assert_not_called()


def test_post_redispatches_a_failed_row_in_place(api_client, db_session, configured):
    job, resume = _seed(db_session)
    row = ResumeDeepDive(
        job_posting_id=job.id,
        resume_version_id=resume.id,
        status=DeepDiveStatus.FAILED,
        error="an earlier failure",
    )
    db_session.add(row)
    db_session.commit()
    original_id = row.id

    spawn, handle = _spawn_patch(call_id="fc-retry")
    with spawn:
        resp = api_client.post(f"/jobs/{job.id}/deep-dive")

    assert resp.status_code == 202
    assert resp.json()["id"] == original_id  # same row, not a second one
    assert resp.json()["status"] == "running"
    assert resp.json()["error"] is None
    assert handle.spawn.call_count == 1
    assert db_session.query(ResumeDeepDive).count() == 1


def test_post_redispatches_a_stale_running_row(api_client, db_session, configured, monkeypatch):
    """A spawn that succeeded but whose call never resolves would
    otherwise return 202 forever and the user could never retry."""
    monkeypatch.setenv("DEEP_DIVE_STALE_MINUTES", "10")
    job, resume = _seed(db_session)
    row = ResumeDeepDive(
        job_posting_id=job.id,
        resume_version_id=resume.id,
        status=DeepDiveStatus.RUNNING,
        modal_call_id="fc-old",
        # Aged off the DATABASE's clock, not Python's. These columns are
        # naive timestamps written by func.now(), so they sit in the
        # Postgres session's timezone, which is not necessarily UTC (it
        # is America/New_York on the machine this was written on). A
        # Python `datetime.now(timezone.utc)` here would be hours off
        # and the row would not read as stale at all - the same skew
        # that was a real bug in _is_stale itself, see its docstring.
        dispatched_at=func.now() - text("interval '45 minutes'"),
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    spawn, handle = _spawn_patch(call_id="fc-fresh")
    with spawn:
        resp = api_client.post(f"/jobs/{job.id}/deep-dive")

    assert resp.status_code == 202
    assert handle.spawn.call_count == 1
    db_session.refresh(row)
    assert row.modal_call_id == "fc-fresh"


def test_a_freshly_dispatched_row_is_not_immediately_stale(api_client, db_session, configured, monkeypatch):
    """Regression test for a real bug (see SESSIONS.md 2026-11-07).
    created_at/updated_at/dispatched_at are naive timestamps written by
    func.now(), so they sit in the POSTGRES session's timezone, which is
    not necessarily UTC - it is America/New_York on the machine this was
    written on. While dispatched_at was set from Python's UTC clock
    instead, a row created seconds ago read as five hours old, i.e.
    instantly stale, so EVERY POST re-dispatched and spent the Modal
    budget again. Asserted through the endpoint rather than against
    _is_stale directly, so it covers the real path.

    A tiny stale window makes this independent of how fast the test
    runs: 1 minute is far shorter than any clock skew would be, yet far
    longer than the milliseconds between these two calls."""
    monkeypatch.setenv("DEEP_DIVE_STALE_MINUTES", "1")
    job, _ = _seed(db_session)
    spawn, handle = _spawn_patch()

    with spawn:
        first = api_client.post(f"/jobs/{job.id}/deep-dive")
        second = api_client.post(f"/jobs/{job.id}/deep-dive")

    assert first.json()["id"] == second.json()["id"]
    assert handle.spawn.call_count == 1, "a seconds-old row must not read as stale"


def test_dispatched_at_is_on_the_same_clock_as_created_at(api_client, db_session, configured):
    """The other half of the same bug: these three timestamps have to be
    comparable and subtractable. The Grafana dashboard computes
    `updated_at - dispatched_at` as a completion time, which read as
    NEGATIVE five hours while they were on different clocks."""
    job, _ = _seed(db_session)
    spawn, _handle = _spawn_patch()

    with spawn:
        resp = api_client.post(f"/jobs/{job.id}/deep-dive")

    row = db_session.get(ResumeDeepDive, resp.json()["id"])
    db_session.refresh(row)
    skew = abs((row.dispatched_at - row.created_at).total_seconds())
    assert skew < 60, f"dispatched_at and created_at are {skew}s apart - different clocks"


def test_post_returns_503_modal_unavailable_without_tokens(api_client, db_session):
    """No `configured` fixture here - this is exactly the public demo's
    situation if its Modal tokens aren't set. There is deliberately no
    local fallback, so this must be a clean, structured 503."""
    job, _ = _seed(db_session)
    resp = api_client.post(f"/jobs/{job.id}/deep-dive")

    assert resp.status_code == 503
    assert resp.json()["code"] == "modal_unavailable"
    assert isinstance(resp.json()["detail"], str)

    # The row is left `failed`, NOT `pending` - a pending row would look
    # "not stale yet" to the next POST and wedge the feature.
    row = db_session.query(ResumeDeepDive).one()
    assert row.status == DeepDiveStatus.FAILED
    assert row.modal_call_id is None


def test_post_returns_503_modal_unavailable_when_the_lookup_fails(api_client, db_session, configured):
    job, _ = _seed(db_session)
    with patch("modal.Function.from_name", side_effect=RuntimeError("not deployed")):
        resp = api_client.post(f"/jobs/{job.id}/deep-dive")

    assert resp.status_code == 503
    assert resp.json()["code"] == "modal_unavailable"
    # A lookup failure means nothing was dispatched, so nothing may be
    # charged to the ledger.
    assert db_session.query(ModalUsage).first() is None


def test_post_returns_503_budget_exhausted_with_its_own_code(api_client, db_session, configured, monkeypatch):
    monkeypatch.setenv("MODAL_MONTHLY_INVOCATION_BUDGET", "6")
    monkeypatch.setenv("MODAL_DEEP_DIVE_WEIGHT", "5")
    db_session.add(ModalUsage(month=_current_month_key(), invocations_used=4))
    db_session.commit()

    job, _ = _seed(db_session)
    spawn, handle = _spawn_patch()
    with spawn:
        resp = api_client.post(f"/jobs/{job.id}/deep-dive")

    assert resp.status_code == 503
    assert resp.json()["code"] == "budget_exhausted"
    handle.spawn.assert_not_called()
    # Refused outright, not partially charged - 4 of 6 used, weight 5
    # would overshoot, so the counter must be untouched.
    usage = db_session.query(ModalUsage).filter_by(month=_current_month_key()).one()
    db_session.refresh(usage)
    assert usage.invocations_used == 4


def test_post_charges_the_deep_dive_weight_not_one(api_client, db_session, configured, monkeypatch):
    monkeypatch.setenv("MODAL_DEEP_DIVE_WEIGHT", "5")
    job, _ = _seed(db_session)
    spawn, _handle = _spawn_patch()

    with spawn:
        assert api_client.post(f"/jobs/{job.id}/deep-dive").status_code == 202

    usage = db_session.query(ModalUsage).filter_by(month=_current_month_key()).one()
    db_session.refresh(usage)
    assert usage.invocations_used == 5


def test_post_is_rate_limited_per_client(api_client, db_session, configured):
    job, _ = _seed(db_session)
    spawn, _handle = _spawn_patch()

    with spawn:
        for _ in range(5):
            api_client.post(f"/jobs/{job.id}/deep-dive")
        blocked = api_client.post(f"/jobs/{job.id}/deep-dive")

    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"


def test_deep_dive_is_available_in_demo_mode(demo_api_client, db_session, configured):
    """Unlike the BYOK drafting route and resume upload, this endpoint is
    mounted in demo mode - it takes no upload and no caller-supplied
    key."""
    job, _ = _seed(db_session)
    spawn, _handle = _spawn_patch()

    with spawn:
        resp = demo_api_client.post(f"/jobs/{job.id}/deep-dive")

    assert resp.status_code == 202
    assert resp.json()["status"] == "running"


# ---------------------------------------------------------------------
# GET /deep-dives/{id}
# ---------------------------------------------------------------------


def _running_row(db_session, job, resume, call_id="fc-123"):
    row = ResumeDeepDive(
        job_posting_id=job.id,
        resume_version_id=resume.id,
        status=DeepDiveStatus.RUNNING,
        modal_call_id=call_id,
        dispatched_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(row)
    db_session.commit()
    return row


def _call_patch(get_side_effect=None, get_return=None):
    call = MagicMock()
    if get_side_effect is not None:
        call.get.side_effect = get_side_effect
    else:
        call.get.return_value = get_return
    return patch("modal.FunctionCall.from_id", return_value=call), call


def test_get_returns_404_for_an_unknown_id(api_client, db_session):
    resp = api_client.get("/deep-dives/99999")
    assert resp.status_code == 404
    assert resp.json()["code"] == "deep_dive_not_found"


def test_get_reports_still_running_on_a_modal_timeout(api_client, db_session, configured):
    """modal.exception.TimeoutError is the real still-running signal and
    is NOT a builtin TimeoutError subclass - asserted with the real
    class so this test would fail if the catch were ever narrowed back
    to the builtin."""
    job, resume = _seed(db_session)
    row = _running_row(db_session, job, resume)
    patcher, call = _call_patch(get_side_effect=modal.exception.TimeoutError())

    with patcher:
        resp = api_client.get(f"/deep-dives/{row.id}")

    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    assert resp.json()["result"] is None
    call.get.assert_called_once_with(timeout=0)
    db_session.refresh(row)
    assert row.status == DeepDiveStatus.RUNNING


def test_get_copies_a_finished_result_into_the_row_and_sets_done(api_client, db_session, configured):
    job, resume = _seed(db_session)
    row = _running_row(db_session, job, resume)
    patcher, _call = _call_patch(get_return=VALID_RESULT)

    with patcher:
        resp = api_client.get(f"/deep-dives/{row.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    assert body["result"]["fit_rating"] == "strong"
    assert body["result"]["version"] == "deep-dive-v1"
    assert body["error"] is None

    # Copied into OUR database - Modal results expire, so the row must
    # stand on its own afterwards.
    db_session.refresh(row)
    assert row.status == DeepDiveStatus.DONE
    assert row.result["summary"].startswith("Strong backend overlap")


def test_get_does_not_re_poll_modal_once_the_row_is_done(api_client, db_session, configured):
    job, resume = _seed(db_session)
    row = _running_row(db_session, job, resume)
    patcher, call = _call_patch(get_return=VALID_RESULT)

    with patcher:
        api_client.get(f"/deep-dives/{row.id}")
        api_client.get(f"/deep-dives/{row.id}")
        api_client.get(f"/deep-dives/{row.id}")

    assert call.get.call_count == 1


def test_get_marks_failed_when_the_output_has_expired(api_client, db_session, configured):
    """OutputExpiredError is a SUBCLASS of modal's TimeoutError, so a
    wrong catch order would silently report this as still-running
    forever."""
    job, resume = _seed(db_session)
    row = _running_row(db_session, job, resume)
    patcher, _call = _call_patch(get_side_effect=modal.exception.OutputExpiredError())

    with patcher:
        resp = api_client.get(f"/deep-dives/{row.id}")

    assert resp.json()["status"] == "failed"
    assert "expired" in resp.json()["error"].lower()
    db_session.refresh(row)
    assert row.status == DeepDiveStatus.FAILED


def test_get_marks_failed_when_the_remote_function_raised(api_client, db_session, configured):
    job, resume = _seed(db_session)
    row = _running_row(db_session, job, resume)
    patcher, _call = _call_patch(get_side_effect=RuntimeError("every provider failed"))

    with patcher:
        resp = api_client.get(f"/deep-dives/{row.id}")

    error = resp.json()["error"]
    assert resp.json()["status"] == "failed"
    # Sanitized: the exception TYPE is named, the raw message (which for
    # a provider error can echo prompt content) is not.
    assert "RuntimeError" in error
    assert "every provider failed" not in error


def test_get_marks_failed_on_a_result_that_fails_validation(api_client, db_session, configured):
    """A successful Modal call is not on its own a reason to trust the
    payload's shape - this process didn't produce that JSON."""
    job, resume = _seed(db_session)
    row = _running_row(db_session, job, resume)
    patcher, _call = _call_patch(get_return={"summary": "no fit_rating here"})

    with patcher:
        resp = api_client.get(f"/deep-dives/{row.id}")

    assert resp.json()["status"] == "failed"
    assert "unexpected result shape" in resp.json()["error"]
    db_session.refresh(row)
    assert row.result is None


def test_get_does_not_poll_modal_for_a_pending_row(api_client, db_session, configured):
    job, resume = _seed(db_session)
    row = ResumeDeepDive(
        job_posting_id=job.id, resume_version_id=resume.id, status=DeepDiveStatus.PENDING
    )
    db_session.add(row)
    db_session.commit()

    with patch("modal.FunctionCall.from_id") as from_id:
        resp = api_client.get(f"/deep-dives/{row.id}")

    assert resp.json()["status"] == "pending"
    from_id.assert_not_called()


def test_get_works_with_no_modal_tokens_once_the_row_is_terminal(api_client, db_session):
    """Reading a finished Deep-Dive must keep working on a deployment
    that can't reach Modal at all - the result lives in our database."""
    job, resume = _seed(db_session)
    row = ResumeDeepDive(
        job_posting_id=job.id,
        resume_version_id=resume.id,
        status=DeepDiveStatus.DONE,
        result=VALID_RESULT,
    )
    db_session.add(row)
    db_session.commit()

    with patch("modal.FunctionCall.from_id") as from_id:
        resp = api_client.get(f"/deep-dives/{row.id}")

    assert resp.json()["result"]["fit_rating"] == "strong"
    from_id.assert_not_called()


def test_full_lifecycle_post_then_poll_to_done(api_client, db_session, configured):
    """The whole flow a browser actually drives: POST -> 202 running,
    poll -> still running, poll -> done with a result."""
    job, _resume = _seed(db_session, with_skills=True)
    spawn, _handle = _spawn_patch(call_id="fc-lifecycle")

    with spawn:
        started = api_client.post(f"/jobs/{job.id}/deep-dive")
    assert started.status_code == 202
    deep_dive_id = started.json()["id"]

    patcher, _call = _call_patch(get_side_effect=modal.exception.TimeoutError())
    with patcher:
        mid = api_client.get(f"/deep-dives/{deep_dive_id}")
    assert mid.json()["status"] == "running"

    patcher, _call = _call_patch(get_return=VALID_RESULT)
    with patcher:
        finished = api_client.get(f"/deep-dives/{deep_dive_id}")
    assert finished.json()["status"] == "done"
    assert len(finished.json()["result"]["gaps"]) == 1

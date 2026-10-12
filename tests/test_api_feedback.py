"""
Tests for the public feedback capture endpoint
(huntloop.api.routers.feedback.submit_feedback) and its new-feedback
notification alert (huntloop.feedback_alerts), now routed through Knock
rather than Resend directly - see CLAUDE.md/SESSIONS.md.

Starlette's TestClient runs FastAPI BackgroundTasks synchronously as
part of completing the response, so by the time client.post(...)
returns, send_new_feedback_alert() has already run (or already failed
and been swallowed) - no sleep/poll needed to observe it.

huntloop.feedback_alerts.send_new_feedback_alert() opens its OWN fresh
DB session (huntloop.api.dependencies.SessionLocal), separate from the
request's own db_session fixture (see that module's docstring for why -
get_db()'s session is already closed by the time a background task
runs). Left un-patched, that would point at this project's real
DATABASE_URL instead of this test's isolated schema - every test here
repoints SessionLocal at the same isolated-schema sessionmaker
`pipeline` already uses, so nothing here can ever touch real data, same
guarantee as every other fixture in this file.

Every real Knock trigger is mocked (huntloop.feedback_alerts.trigger_workflow) -
no real API key needed, no real network call anywhere in this file.
"""
from unittest.mock import patch

import huntloop.feedback_alerts as feedback_alerts
from huntloop.db_models import Feedback
from huntloop.knock_client import KnockUnavailable


def _feedback_payload(**overrides):
    payload = {"category": "bug", "description": "The export button is broken on Safari."}
    payload.update(overrides)
    return payload


def _repoint_session_local(monkeypatch, pipeline):
    """See module docstring - keeps the background task's own session on
    the same isolated test schema as everything else in this test."""
    monkeypatch.setattr(feedback_alerts, "SessionLocal", pipeline.Session)


def test_submit_feedback_triggers_exactly_one_send_attempt(api_client, db_session, pipeline, monkeypatch):
    _repoint_session_local(monkeypatch, pipeline)

    with patch("huntloop.feedback_alerts.trigger_workflow", return_value=None) as mock_trigger:
        response = api_client.post("/feedback", json=_feedback_payload())

    assert response.status_code == 202
    mock_trigger.assert_called_once()

    kwargs = mock_trigger.call_args.kwargs
    assert kwargs["to"]
    assert "bug" in kwargs["subject"].lower()
    assert "export button is broken" in kwargs["html"]
    assert "/admin/feedback" in kwargs["html"]

    # The real row this alert describes really was persisted.
    row = db_session.query(Feedback).filter_by(id=response.json()["id"]).one()
    assert row.raw_text == "The export button is broken on Safari."


def test_submit_feedback_truncates_a_long_description_in_the_email(api_client, pipeline, monkeypatch):
    _repoint_session_local(monkeypatch, pipeline)
    long_text = "x" * 500

    with patch("huntloop.feedback_alerts.trigger_workflow", return_value=None) as mock_trigger:
        response = api_client.post("/feedback", json=_feedback_payload(description=long_text))

    assert response.status_code == 202
    html = mock_trigger.call_args.kwargs["html"]
    assert "x" * 280 in html
    assert "x" * 300 not in html  # truncated well before the full 500 chars


def test_a_reservation_failure_does_not_block_the_feedback_submission(api_client, db_session, pipeline, monkeypatch):
    """The real requirement under test: a Knock failure (budget
    exhausted, not configured, a trigger error - any KnockUnavailable)
    must never fail or roll back the actual feedback submission."""
    _repoint_session_local(monkeypatch, pipeline)

    with patch(
        "huntloop.feedback_alerts.trigger_workflow",
        side_effect=KnockUnavailable("daily trigger budget reached", reason="daily_budget_exhausted"),
    ) as mock_trigger:
        response = api_client.post("/feedback", json=_feedback_payload())

    assert response.status_code == 202
    mock_trigger.assert_called_once()

    body = response.json()
    row = db_session.query(Feedback).filter_by(id=body["id"]).one()
    assert row.raw_text == "The export button is broken on Safari."
    assert row.triage_status == row.triage_status.PENDING


def test_an_unexpected_exception_in_the_alert_does_not_fail_the_request(api_client, db_session, pipeline, monkeypatch):
    """Not just KnockUnavailable - send_new_feedback_alert's own
    top-level try/except must swallow anything."""
    _repoint_session_local(monkeypatch, pipeline)

    with patch("huntloop.feedback_alerts.trigger_workflow", side_effect=RuntimeError("boom")):
        response = api_client.post("/feedback", json=_feedback_payload())

    assert response.status_code == 202
    assert db_session.query(Feedback).filter_by(id=response.json()["id"]).count() == 1


def test_submit_feedback_without_knock_configured_still_succeeds(api_client, db_session, pipeline, monkeypatch):
    """End-to-end through the real trigger_workflow() (not mocked this
    time), with KNOCK_API_KEY deliberately unset - exercises the real
    not_configured path rather than a mocked KnockUnavailable."""
    _repoint_session_local(monkeypatch, pipeline)
    monkeypatch.delenv("KNOCK_API_KEY", raising=False)

    response = api_client.post("/feedback", json=_feedback_payload())

    assert response.status_code == 202
    assert db_session.query(Feedback).filter_by(id=response.json()["id"]).count() == 1

"""
Unit tests for huntloop.feedback_alerts - the new-feedback notification
alert, now routed through Knock (huntloop.knock_client) rather than
Resend directly. Mocks huntloop.feedback_alerts.trigger_workflow
throughout - no real Knock API key needed, no real network call, no
real DB session opened (SessionLocal is also mocked), same convention
tests/test_knock_client.py / tests/test_budget_alerts.py already
established.
"""
from unittest.mock import MagicMock, patch

import pytest

from huntloop.feedback_alerts import send_new_feedback_alert
from huntloop.knock_client import KnockUnavailable


@pytest.fixture(autouse=True)
def _mock_session_local():
    with patch("huntloop.feedback_alerts.SessionLocal", return_value=MagicMock()):
        yield


def _mock_trigger_workflow(**kwargs):
    return patch("huntloop.feedback_alerts.trigger_workflow", **kwargs)


def test_sends_with_the_default_workflow_key_and_alert_address(monkeypatch):
    monkeypatch.delenv("KNOCK_FEEDBACK_ALERT_WORKFLOW_KEY", raising=False)
    monkeypatch.delenv("RESEND_ALERT_TO_ADDRESS", raising=False)

    with _mock_trigger_workflow() as mock_trigger:
        send_new_feedback_alert(feedback_id=42, category="bug", raw_text="it broke")

    mock_trigger.assert_called_once()
    kwargs = mock_trigger.call_args.kwargs
    assert kwargs["workflow_key"] == "feedback-alert"
    assert kwargs["to"] == "niramayrkelkar@gmail.com"
    assert "bug" in kwargs["subject"]
    assert "it broke" in kwargs["html"]
    assert "42" in kwargs["html"]


def test_uses_an_overridden_workflow_key_and_alert_address(monkeypatch):
    monkeypatch.setenv("KNOCK_FEEDBACK_ALERT_WORKFLOW_KEY", "custom-feedback-workflow")
    monkeypatch.setenv("RESEND_ALERT_TO_ADDRESS", "someone-else@example.com")

    with _mock_trigger_workflow() as mock_trigger:
        send_new_feedback_alert(feedback_id=1, category="idea", raw_text="a suggestion")

    kwargs = mock_trigger.call_args.kwargs
    assert kwargs["workflow_key"] == "custom-feedback-workflow"
    assert kwargs["to"] == "someone-else@example.com"


def test_knock_unavailable_is_caught_and_does_not_raise():
    with _mock_trigger_workflow(side_effect=KnockUnavailable("not configured", reason="not_configured")):
        # Must not raise.
        send_new_feedback_alert(feedback_id=1, category="bug", raw_text="boom")


def test_an_unexpected_exception_is_also_swallowed():
    """Not just KnockUnavailable - this function must never raise,
    since it runs as a FastAPI BackgroundTasks callback after the
    response has already been sent."""
    with _mock_trigger_workflow(side_effect=RuntimeError("boom")):
        # Must not raise.
        send_new_feedback_alert(feedback_id=1, category="bug", raw_text="boom")


def test_raw_text_is_html_escaped_in_the_preview():
    with _mock_trigger_workflow() as mock_trigger:
        send_new_feedback_alert(feedback_id=1, category="bug", raw_text="<script>alert(1)</script>")

    html = mock_trigger.call_args.kwargs["html"]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html

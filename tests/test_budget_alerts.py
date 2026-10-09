"""
Unit tests for huntloop.budget_alerts - the shared 80%/95% budget-
threshold alert check used by huntloop.modal_resume_processing,
scripts/backfill_company_research.py, and scripts/triage_feedback.py
(Resend Phase B - see CLAUDE.md/SESSIONS.md).

Mocks huntloop.budget_alerts.send_email throughout - no real Resend API
key needed, no real network call anywhere in this file, same convention
tests/test_resend_client.py already established for Phase A.
"""
from unittest.mock import patch

import pytest

from huntloop.budget_alerts import check_and_alert_budget_threshold
from huntloop.db_models import BudgetAlertState
from huntloop.resend_client import ResendUnavailable


def _mock_send_email(**kwargs):
    return patch("huntloop.budget_alerts.send_email", **kwargs)


def test_no_alert_below_either_threshold(db_session):
    with _mock_send_email() as mock_send:
        check_and_alert_budget_threshold(db_session, ledger="tavily", period="2026-10", used=50, cap=600)

    mock_send.assert_not_called()
    row = db_session.query(BudgetAlertState).filter_by(ledger="tavily", period="2026-10").first()
    assert row is not None
    assert row.last_threshold == 0


def test_crossing_80_percent_sends_one_alert_and_records_it(db_session):
    with _mock_send_email() as mock_send:
        # 480 / 600 = 80% exactly.
        check_and_alert_budget_threshold(db_session, ledger="tavily", period="2026-10", used=480, cap=600)

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to"]
    assert "80%" in kwargs["subject"]
    assert "tavily" in kwargs["subject"]

    row = db_session.query(BudgetAlertState).filter_by(ledger="tavily", period="2026-10").one()
    assert row.last_threshold == 80


def test_crossing_80_then_95_in_separate_calls_sends_two_distinct_alerts(db_session):
    with _mock_send_email() as mock_send:
        check_and_alert_budget_threshold(db_session, ledger="modal", period="2026-10", used=400, cap=500)  # 80%
        check_and_alert_budget_threshold(db_session, ledger="modal", period="2026-10", used=475, cap=500)  # 95%

    assert mock_send.call_count == 2
    subjects = [c.kwargs["subject"] for c in mock_send.call_args_list]
    assert any("80%" in s for s in subjects)
    assert any("95%" in s for s in subjects)

    row = db_session.query(BudgetAlertState).filter_by(ledger="modal", period="2026-10").one()
    assert row.last_threshold == 95


def test_repeated_calls_at_the_same_crossed_threshold_do_not_re_alert(db_session):
    """The real requirement under test: duplicate threshold crossings in
    the same period must not re-fire."""
    with _mock_send_email() as mock_send:
        check_and_alert_budget_threshold(db_session, ledger="triage", period="2026-10-09", used=85, cap=100)
        # Usage keeps climbing but stays under 95 - still only the one
        # 80% alert should ever have fired.
        check_and_alert_budget_threshold(db_session, ledger="triage", period="2026-10-09", used=90, cap=100)
        check_and_alert_budget_threshold(db_session, ledger="triage", period="2026-10-09", used=94, cap=100)

    mock_send.assert_called_once()
    row = db_session.query(BudgetAlertState).filter_by(ledger="triage", period="2026-10-09").one()
    assert row.last_threshold == 80


def test_a_single_jump_past_both_thresholds_fires_both_once(db_session):
    with _mock_send_email() as mock_send:
        check_and_alert_budget_threshold(db_session, ledger="tavily", period="2026-10", used=600, cap=600)

    assert mock_send.call_count == 2
    row = db_session.query(BudgetAlertState).filter_by(ledger="tavily", period="2026-10").one()
    assert row.last_threshold == 95


def test_a_new_period_re_arms_both_thresholds(db_session):
    with _mock_send_email() as mock_send:
        check_and_alert_budget_threshold(db_session, ledger="tavily", period="2026-09", used=600, cap=600)
        check_and_alert_budget_threshold(db_session, ledger="tavily", period="2026-10", used=480, cap=600)

    # 2 for September (80 + 95) + 1 for October (80 only) = 3.
    assert mock_send.call_count == 3
    sep_row = db_session.query(BudgetAlertState).filter_by(ledger="tavily", period="2026-09").one()
    oct_row = db_session.query(BudgetAlertState).filter_by(ledger="tavily", period="2026-10").one()
    assert sep_row.last_threshold == 95
    assert oct_row.last_threshold == 80


def test_different_ledgers_track_independently_even_in_the_same_period(db_session):
    with _mock_send_email() as mock_send:
        check_and_alert_budget_threshold(db_session, ledger="tavily", period="2026-10", used=480, cap=600)
        check_and_alert_budget_threshold(db_session, ledger="modal", period="2026-10", used=400, cap=500)

    assert mock_send.call_count == 2
    tavily_row = db_session.query(BudgetAlertState).filter_by(ledger="tavily", period="2026-10").one()
    modal_row = db_session.query(BudgetAlertState).filter_by(ledger="modal", period="2026-10").one()
    assert tavily_row.last_threshold == 80
    assert modal_row.last_threshold == 80


def test_zero_or_negative_cap_is_a_safe_no_op(db_session):
    with _mock_send_email() as mock_send:
        check_and_alert_budget_threshold(db_session, ledger="tavily", period="2026-10", used=10, cap=0)

    mock_send.assert_not_called()
    assert db_session.query(BudgetAlertState).count() == 0


def test_resend_unavailable_is_caught_and_does_not_raise(db_session):
    """A reservation/budget/config failure on the Resend side must never
    propagate - the ledger's own state tracking still records the
    crossing (so it won't spam-retry on the next call)."""
    with _mock_send_email(side_effect=ResendUnavailable("budget reached", reason="daily_budget_exhausted")):
        check_and_alert_budget_threshold(db_session, ledger="tavily", period="2026-10", used=480, cap=600)

    row = db_session.query(BudgetAlertState).filter_by(ledger="tavily", period="2026-10").one()
    assert row.last_threshold == 80


def test_an_unexpected_exception_anywhere_in_the_check_is_swallowed(db_session):
    """Not just ResendUnavailable - the whole check is defensive, per
    the module's own 'never raises' contract (mirrors
    huntloop.metrics.push_run_metrics and every other best-effort side
    effect in this project)."""
    with _mock_send_email(side_effect=RuntimeError("boom")):
        # Must not raise.
        check_and_alert_budget_threshold(db_session, ledger="tavily", period="2026-10", used=480, cap=600)

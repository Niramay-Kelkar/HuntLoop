"""
Unit tests for huntloop.resend_client - the budget ledger, locking, and
not-configured/budget-exhausted/send-error paths. Mocks
resend.Emails.send rather than calling the real Resend API (no API key
needed, no network, deterministic in CI) - same technique
tests/test_modal_resume_processing.py uses for modal.Function.from_name.

Nothing calls huntloop.resend_client yet (Phase A is infrastructure
only - see CLAUDE.md/SESSIONS.md), so there is no end-to-end "real send"
verification to do here or anywhere else in this phase.
"""
import threading
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import ResendUsage
from huntloop.resend_client import (
    DEFAULT_DAILY_EMAIL_BUDGET,
    DEFAULT_MONTHLY_EMAIL_BUDGET,
    ResendUnavailable,
    _configured,
    _current_day_key,
    _current_month_prefix,
    _reserve_send_slot,
    send_email,
)


@pytest.fixture(autouse=True)
def _clear_resend_env(monkeypatch):
    """Every test starts with Resend NOT configured and the default
    budgets - individual tests opt in to setting these."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_FROM_ADDRESS", raising=False)
    monkeypatch.delenv("RESEND_DAILY_EMAIL_BUDGET", raising=False)
    monkeypatch.delenv("RESEND_MONTHLY_EMAIL_BUDGET", raising=False)


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")


# ---------------------------------------------------------------------
# _configured
# ---------------------------------------------------------------------


def test_not_configured_when_api_key_unset():
    assert _configured() is False


def test_configured_when_api_key_set(configured):
    assert _configured() is True


# ---------------------------------------------------------------------
# Budget ledger - _reserve_send_slot
# ---------------------------------------------------------------------


def test_reserve_creates_todays_row_and_increments_it(db_session):
    day = _current_day_key()
    assert db_session.query(ResendUsage).filter_by(day=day).first() is None

    reserved, denial = _reserve_send_slot(db_session)

    assert reserved is True
    assert denial is None
    row = db_session.query(ResendUsage).filter_by(day=day).one()
    assert row.emails_sent == 1


def test_reserve_increments_an_existing_row_across_calls(db_session):
    for _ in range(3):
        reserved, denial = _reserve_send_slot(db_session)
        assert reserved is True
        assert denial is None

    day = _current_day_key()
    row = db_session.query(ResendUsage).filter_by(day=day).one()
    assert row.emails_sent == 3


def test_reserve_refuses_once_daily_budget_is_reached_and_does_not_increment(db_session, monkeypatch):
    monkeypatch.setenv("RESEND_DAILY_EMAIL_BUDGET", "2")

    first = _reserve_send_slot(db_session)
    second = _reserve_send_slot(db_session)
    assert first[0] is True
    assert second[0] is True

    reserved, denial = _reserve_send_slot(db_session)
    assert reserved is False
    assert denial.reason == "daily_budget_exhausted"
    assert denial.daily_used == 2
    assert denial.daily_budget == 2

    day = _current_day_key()
    row = db_session.query(ResendUsage).filter_by(day=day).one()
    # The refused third call must not have incremented anything further.
    assert row.emails_sent == 2


def test_reserve_refuses_once_monthly_budget_is_reached_even_under_daily_budget(db_session, monkeypatch):
    monkeypatch.setenv("RESEND_DAILY_EMAIL_BUDGET", "10")
    monkeypatch.setenv("RESEND_MONTHLY_EMAIL_BUDGET", "1")

    first = _reserve_send_slot(db_session)
    assert first[0] is True

    reserved, denial = _reserve_send_slot(db_session)
    assert reserved is False
    assert denial.reason == "monthly_budget_exhausted"
    assert denial.monthly_used == 1
    assert denial.monthly_budget == 1

    day = _current_day_key()
    row = db_session.query(ResendUsage).filter_by(day=day).one()
    # Still just the one successful reservation - daily budget (10) was
    # never the blocker here, so make sure the monthly check didn't
    # still sneak an increment through.
    assert row.emails_sent == 1


def test_reserve_with_zero_daily_budget_refuses_immediately(db_session, monkeypatch):
    monkeypatch.setenv("RESEND_DAILY_EMAIL_BUDGET", "0")

    reserved, denial = _reserve_send_slot(db_session)
    assert reserved is False
    assert denial.reason == "daily_budget_exhausted"
    assert denial.daily_used == 0
    assert denial.daily_budget == 0


def test_reserve_sums_across_days_within_the_same_month(db_session, monkeypatch):
    """The monthly cap is enforced by summing resend_usage rows for the
    current month, not just today's row - seed a second day's row
    directly to prove the sum spans more than just today."""
    monkeypatch.setenv("RESEND_DAILY_EMAIL_BUDGET", "10")
    monkeypatch.setenv("RESEND_MONTHLY_EMAIL_BUDGET", "5")

    month_prefix = _current_month_prefix()
    other_day = f"{month_prefix}-01" if not _current_day_key().endswith("-01") else f"{month_prefix}-02"
    db_session.add(ResendUsage(day=other_day, emails_sent=4))
    db_session.commit()

    reserved, denial = _reserve_send_slot(db_session)
    assert reserved is True  # 4 used + this 1 = 5, exactly at the cap, still allowed

    reserved, denial = _reserve_send_slot(db_session)
    assert reserved is False
    assert denial.reason == "monthly_budget_exhausted"
    assert denial.monthly_used == 5


def test_default_budgets_match_resends_real_free_tier():
    assert DEFAULT_DAILY_EMAIL_BUDGET == 100
    assert DEFAULT_MONTHLY_EMAIL_BUDGET == 3000


def test_reserve_is_race_safe_under_real_concurrent_callers(test_database_url, db_session, monkeypatch):
    """The real thing _reserve_send_slot's `SELECT ... FOR UPDATE` lock
    exists to prevent - a bare read-then-increment would let concurrent
    callers both read the same `used_before` and both write `used_before
    + 1`, double-counting (or, worse, letting both through past a cap
    that should have blocked the second one). Mirrors
    tests/test_backfill_lock.py's real-overlap approach, but with
    threads + separate engines/sessions racing the same row instead of
    two long-lived advisory-lock holders, since this lock is held only
    for one short check-then-increment, not a whole script run.

    Daily budget is set to exactly N threads: if the lock works, exactly
    N reservations succeed and the row ends at N - never more (a double
    count) and never fewer (a reservation silently lost)."""
    monkeypatch.setenv("RESEND_DAILY_EMAIL_BUDGET", "20")
    monkeypatch.setenv("RESEND_MONTHLY_EMAIL_BUDGET", "20")

    n_threads = 20
    results = []
    results_lock = threading.Lock()

    def worker():
        engine = create_engine(test_database_url)
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            reserved, _ = _reserve_send_slot(session)
            with results_lock:
                results.append(reserved)
        finally:
            session.close()
            engine.dispose()

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == n_threads
    assert results.count(False) == 0

    # db_session (via the `pipeline` fixture) sees the same schema the
    # worker threads wrote to, and its teardown clears this row again
    # afterward - using it here (rather than a fourth throwaway engine)
    # keeps this test from leaking state into the next one.
    day = _current_day_key()
    row = db_session.query(ResendUsage).filter_by(day=day).one()
    # The real assertion: exactly n_threads, never more (a
    # double-counted race) and never less (a lost increment).
    assert row.emails_sent == n_threads


# ---------------------------------------------------------------------
# send_email - the public entry point. Unlike
# huntloop.modal_resume_processing's public functions, this one does NOT
# swallow ResendUnavailable - every path below asserts it propagates.
# ---------------------------------------------------------------------


def test_send_email_raises_without_touching_the_ledger_when_not_configured(db_session):
    with pytest.raises(ResendUnavailable, match="RESEND_API_KEY") as exc_info:
        send_email(db_session, to="owner@example.com", subject="test", html="<p>hi</p>")

    assert exc_info.value.reason == "not_configured"
    day = _current_day_key()
    assert db_session.query(ResendUsage).filter_by(day=day).first() is None


def test_send_email_succeeds_and_increments_the_ledger(db_session, configured):
    with patch("huntloop.resend_client.resend.Emails.send", return_value={"id": "fake-id"}) as mock_send:
        send_email(db_session, to="owner@example.com", subject="New match", html="<p>hi</p>")

    mock_send.assert_called_once()
    sent_params = mock_send.call_args[0][0]
    assert sent_params["to"] == ["owner@example.com"]
    assert sent_params["subject"] == "New match"

    day = _current_day_key()
    assert db_session.query(ResendUsage).filter_by(day=day).one().emails_sent == 1


def test_send_email_raises_once_daily_budget_is_reached(db_session, configured, monkeypatch):
    monkeypatch.setenv("RESEND_DAILY_EMAIL_BUDGET", "1")

    with patch("huntloop.resend_client.resend.Emails.send", return_value={"id": "fake-id"}) as mock_send:
        send_email(db_session, to="owner@example.com", subject="first", html="<p>hi</p>")

        with pytest.raises(ResendUnavailable, match="budget reached") as exc_info:
            send_email(db_session, to="owner@example.com", subject="second", html="<p>hi</p>")

    assert exc_info.value.reason == "daily_budget_exhausted"
    # Only the first (under-budget) call actually dispatched.
    mock_send.assert_called_once()


def test_send_email_raises_on_send_error_but_still_counts_the_reserved_slot(db_session, configured):
    """A real call that reaches Resend and then errors still consumed
    its reserved budget slot - see module docstring's reasoning
    (mirrors ModalUnavailable's 'still counts' behavior)."""
    with patch("huntloop.resend_client.resend.Emails.send", side_effect=RuntimeError("boom")):
        with pytest.raises(ResendUnavailable, match="failed") as exc_info:
            send_email(db_session, to="owner@example.com", subject="test", html="<p>hi</p>")

    assert exc_info.value.reason == "send_error"
    day = _current_day_key()
    assert db_session.query(ResendUsage).filter_by(day=day).one().emails_sent == 1


def test_send_email_uses_sandbox_from_address_by_default(db_session, configured):
    with patch("huntloop.resend_client.resend.Emails.send", return_value={"id": "fake-id"}) as mock_send:
        send_email(db_session, to="owner@example.com", subject="test", html="<p>hi</p>")

    assert mock_send.call_args[0][0]["from"] == "HuntLoop <onboarding@resend.dev>"


def test_send_email_uses_configured_from_address_when_set(db_session, configured, monkeypatch):
    monkeypatch.setenv("RESEND_FROM_ADDRESS", "HuntLoop Alerts <alerts@example.com>")

    with patch("huntloop.resend_client.resend.Emails.send", return_value={"id": "fake-id"}) as mock_send:
        send_email(db_session, to="owner@example.com", subject="test", html="<p>hi</p>")

    assert mock_send.call_args[0][0]["from"] == "HuntLoop Alerts <alerts@example.com>"


def test_send_email_explicit_from_address_argument_overrides_env(db_session, configured, monkeypatch):
    monkeypatch.setenv("RESEND_FROM_ADDRESS", "HuntLoop Alerts <alerts@example.com>")

    with patch("huntloop.resend_client.resend.Emails.send", return_value={"id": "fake-id"}) as mock_send:
        send_email(
            db_session, to="owner@example.com", subject="test", html="<p>hi</p>",
            from_address="Someone Else <someone@example.com>",
        )

    assert mock_send.call_args[0][0]["from"] == "Someone Else <someone@example.com>"

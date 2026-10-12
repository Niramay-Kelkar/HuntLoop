"""
Unit tests for huntloop.knock_client - the budget ledger, locking, and
not-configured/budget-exhausted/trigger-error paths. Mocks
requests.post rather than calling the real Knock API (no API key
needed, no network, deterministic in CI) - same technique
tests/test_resend_client.py uses for resend.Emails.send.
"""
import threading
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import KnockUsage
from huntloop.knock_client import (
    DEFAULT_DAILY_TRIGGER_BUDGET,
    DEFAULT_MONTHLY_TRIGGER_BUDGET,
    KnockUnavailable,
    _configured,
    _current_day_key,
    _current_month_prefix,
    _default_recipient_id,
    _reserve_trigger_slot,
    trigger_workflow,
)


@pytest.fixture(autouse=True)
def _clear_knock_env(monkeypatch):
    """Every test starts with Knock NOT configured and the default
    budgets - individual tests opt in to setting these."""
    monkeypatch.delenv("KNOCK_API_KEY", raising=False)
    monkeypatch.delenv("KNOCK_DAILY_TRIGGER_BUDGET", raising=False)
    monkeypatch.delenv("KNOCK_MONTHLY_TRIGGER_BUDGET", raising=False)


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setenv("KNOCK_API_KEY", "knock_test_key")


def _mock_response(status_code: int = 200):
    response = MagicMock()
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    return response


# ---------------------------------------------------------------------
# _configured / _default_recipient_id
# ---------------------------------------------------------------------


def test_not_configured_when_api_key_unset():
    assert _configured() is False


def test_configured_when_api_key_set(configured):
    assert _configured() is True


def test_default_recipient_id_derives_a_knock_legal_id_from_an_email():
    assert _default_recipient_id("owner@example.com") == "owner-at-example-com"


# ---------------------------------------------------------------------
# Budget ledger - _reserve_trigger_slot
# ---------------------------------------------------------------------


def test_reserve_creates_todays_row_and_increments_it(db_session):
    day = _current_day_key()
    assert db_session.query(KnockUsage).filter_by(day=day).first() is None

    reserved, denial = _reserve_trigger_slot(db_session)

    assert reserved is True
    assert denial is None
    row = db_session.query(KnockUsage).filter_by(day=day).one()
    assert row.triggers_sent == 1


def test_reserve_increments_an_existing_row_across_calls(db_session):
    for _ in range(3):
        reserved, denial = _reserve_trigger_slot(db_session)
        assert reserved is True
        assert denial is None

    day = _current_day_key()
    row = db_session.query(KnockUsage).filter_by(day=day).one()
    assert row.triggers_sent == 3


def test_reserve_refuses_once_daily_budget_is_reached_and_does_not_increment(db_session, monkeypatch):
    monkeypatch.setenv("KNOCK_DAILY_TRIGGER_BUDGET", "2")

    first = _reserve_trigger_slot(db_session)
    second = _reserve_trigger_slot(db_session)
    assert first[0] is True
    assert second[0] is True

    reserved, denial = _reserve_trigger_slot(db_session)
    assert reserved is False
    assert denial.reason == "daily_budget_exhausted"
    assert denial.daily_used == 2
    assert denial.daily_budget == 2

    day = _current_day_key()
    row = db_session.query(KnockUsage).filter_by(day=day).one()
    # The refused third call must not have incremented anything further.
    assert row.triggers_sent == 2


def test_reserve_refuses_once_monthly_budget_is_reached_even_under_daily_budget(db_session, monkeypatch):
    monkeypatch.setenv("KNOCK_DAILY_TRIGGER_BUDGET", "10")
    monkeypatch.setenv("KNOCK_MONTHLY_TRIGGER_BUDGET", "1")

    first = _reserve_trigger_slot(db_session)
    assert first[0] is True

    reserved, denial = _reserve_trigger_slot(db_session)
    assert reserved is False
    assert denial.reason == "monthly_budget_exhausted"
    assert denial.monthly_used == 1
    assert denial.monthly_budget == 1

    day = _current_day_key()
    row = db_session.query(KnockUsage).filter_by(day=day).one()
    # Still just the one successful reservation - daily budget (10) was
    # never the blocker here, so make sure the monthly check didn't
    # still sneak an increment through.
    assert row.triggers_sent == 1


def test_reserve_with_zero_daily_budget_refuses_immediately(db_session, monkeypatch):
    monkeypatch.setenv("KNOCK_DAILY_TRIGGER_BUDGET", "0")

    reserved, denial = _reserve_trigger_slot(db_session)
    assert reserved is False
    assert denial.reason == "daily_budget_exhausted"
    assert denial.daily_used == 0
    assert denial.daily_budget == 0


def test_reserve_sums_across_days_within_the_same_month(db_session, monkeypatch):
    """The monthly cap is enforced by summing knock_usage rows for the
    current month, not just today's row - seed a second day's row
    directly to prove the sum spans more than just today."""
    monkeypatch.setenv("KNOCK_DAILY_TRIGGER_BUDGET", "10")
    monkeypatch.setenv("KNOCK_MONTHLY_TRIGGER_BUDGET", "5")

    month_prefix = _current_month_prefix()
    other_day = f"{month_prefix}-01" if not _current_day_key().endswith("-01") else f"{month_prefix}-02"
    db_session.add(KnockUsage(day=other_day, triggers_sent=4))
    db_session.commit()

    reserved, denial = _reserve_trigger_slot(db_session)
    assert reserved is True  # 4 used + this 1 = 5, exactly at the cap, still allowed

    reserved, denial = _reserve_trigger_slot(db_session)
    assert reserved is False
    assert denial.reason == "monthly_budget_exhausted"
    assert denial.monthly_used == 5


def test_default_budgets_match_module_constants():
    assert DEFAULT_DAILY_TRIGGER_BUDGET == 100
    assert DEFAULT_MONTHLY_TRIGGER_BUDGET == 3000


def test_reserve_is_race_safe_under_real_concurrent_callers(test_database_url, db_session, monkeypatch):
    """The real thing _reserve_trigger_slot's `SELECT ... FOR UPDATE`
    lock exists to prevent - a bare read-then-increment would let
    concurrent callers both read the same `used_before` and both write
    `used_before + 1`, double-counting (or, worse, letting both through
    past a cap that should have blocked the second one). Mirrors
    tests/test_resend_client.py's equivalent test, with threads +
    separate engines/sessions racing the same row instead of two
    long-lived advisory-lock holders, since this lock is held only for
    one short check-then-increment, not a whole script run.

    Daily budget is set to exactly N threads: if the lock works, exactly
    N reservations succeed and the row ends at N - never more (a double
    count) and never fewer (a reservation silently lost)."""
    monkeypatch.setenv("KNOCK_DAILY_TRIGGER_BUDGET", "20")
    monkeypatch.setenv("KNOCK_MONTHLY_TRIGGER_BUDGET", "20")

    n_threads = 20
    results = []
    results_lock = threading.Lock()

    def worker():
        engine = create_engine(test_database_url)
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            reserved, _ = _reserve_trigger_slot(session)
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
    row = db_session.query(KnockUsage).filter_by(day=day).one()
    # The real assertion: exactly n_threads, never more (a
    # double-counted race) and never less (a lost increment).
    assert row.triggers_sent == n_threads


# ---------------------------------------------------------------------
# trigger_workflow - the public entry point. Like
# huntloop.resend_client.send_email, this does NOT swallow
# KnockUnavailable - every path below asserts it propagates.
# ---------------------------------------------------------------------


def test_trigger_workflow_raises_without_touching_the_ledger_when_not_configured(db_session):
    with pytest.raises(KnockUnavailable, match="KNOCK_API_KEY") as exc_info:
        trigger_workflow(
            db_session, workflow_key="feedback-alert", to="owner@example.com", subject="test", html="<p>hi</p>"
        )

    assert exc_info.value.reason == "not_configured"
    day = _current_day_key()
    assert db_session.query(KnockUsage).filter_by(day=day).first() is None


def test_trigger_workflow_succeeds_and_increments_the_ledger(db_session, configured):
    with patch("huntloop.knock_client.requests.post", return_value=_mock_response()) as mock_post:
        trigger_workflow(
            db_session, workflow_key="feedback-alert", to="owner@example.com", subject="New match", html="<p>hi</p>"
        )

    mock_post.assert_called_once()
    call = mock_post.call_args
    assert call.args[0] == "https://api.knock.app/v1/workflows/feedback-alert/trigger"
    assert call.kwargs["headers"]["Authorization"] == "Bearer knock_test_key"
    body = call.kwargs["json"]
    assert body["recipients"] == [{"id": "owner-at-example-com", "email": "owner@example.com"}]
    assert body["data"]["subject"] == "New match"
    assert body["data"]["html"] == "<p>hi</p>"
    assert call.kwargs["timeout"] == 10

    day = _current_day_key()
    assert db_session.query(KnockUsage).filter_by(day=day).one().triggers_sent == 1


def test_trigger_workflow_merges_extra_data_and_recipient_fields(db_session, configured):
    with patch("huntloop.knock_client.requests.post", return_value=_mock_response()) as mock_post:
        trigger_workflow(
            db_session,
            workflow_key="feedback-alert",
            to="owner@example.com",
            subject="test",
            html="<p>hi</p>",
            recipient_id="explicit-id",
            recipient_name="Project Owner",
            data={"extra_field": "value"},
        )

    body = mock_post.call_args.kwargs["json"]
    assert body["recipients"] == [
        {"id": "explicit-id", "email": "owner@example.com", "name": "Project Owner"}
    ]
    assert body["data"] == {"subject": "test", "html": "<p>hi</p>", "extra_field": "value"}


def test_trigger_workflow_raises_once_daily_budget_is_reached(db_session, configured, monkeypatch):
    monkeypatch.setenv("KNOCK_DAILY_TRIGGER_BUDGET", "1")

    with patch("huntloop.knock_client.requests.post", return_value=_mock_response()) as mock_post:
        trigger_workflow(
            db_session, workflow_key="feedback-alert", to="owner@example.com", subject="first", html="<p>hi</p>"
        )

        with pytest.raises(KnockUnavailable, match="budget reached") as exc_info:
            trigger_workflow(
                db_session, workflow_key="feedback-alert", to="owner@example.com", subject="second", html="<p>hi</p>"
            )

    assert exc_info.value.reason == "daily_budget_exhausted"
    # Only the first (under-budget) call actually dispatched.
    mock_post.assert_called_once()


def test_trigger_workflow_raises_on_trigger_error_but_still_counts_the_reserved_slot(db_session, configured):
    """A real call that reaches Knock and then errors still consumed
    its reserved budget slot - see module docstring's reasoning
    (mirrors ResendUnavailable's 'still counts' behavior)."""
    with patch("huntloop.knock_client.requests.post", side_effect=RuntimeError("boom")):
        with pytest.raises(KnockUnavailable, match="failed") as exc_info:
            trigger_workflow(
                db_session, workflow_key="feedback-alert", to="owner@example.com", subject="test", html="<p>hi</p>"
            )

    assert exc_info.value.reason == "trigger_error"
    day = _current_day_key()
    assert db_session.query(KnockUsage).filter_by(day=day).one().triggers_sent == 1


def test_trigger_workflow_raises_on_non_2xx_response(db_session, configured):
    response = _mock_response(status_code=401)
    response.raise_for_status.side_effect = Exception("401 Unauthorized")
    with patch("huntloop.knock_client.requests.post", return_value=response):
        with pytest.raises(KnockUnavailable) as exc_info:
            trigger_workflow(
                db_session, workflow_key="feedback-alert", to="owner@example.com", subject="test", html="<p>hi</p>"
            )

    assert exc_info.value.reason == "trigger_error"

"""
Unit tests for huntloop.modal_resume_processing - the budget ledger,
locking, and fallback logic. Mocks modal.Function.from_name/.remote/
.remote.aio rather than calling real Modal (no token needed, no network,
deterministic in CI) - the one real end-to-end call against a live
deployed app was verified manually (see CLAUDE.md's Modal integration
write-up) and is out of scope for an automated suite with no real token.

huntloop.embeddings is not imported here at all - every fallback path
exercised below stops at the ModalUnavailable branch before reaching
embed_text(), except where a test explicitly stubs it (same sys.modules
technique tests/test_api_resumes.py already uses, for the same reason -
see that file's module docstring for why monkeypatch.setattr doesn't
work here).
"""
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from huntloop.db_models import ModalUsage
from huntloop.modal_resume_processing import (
    DEFAULT_MONTHLY_INVOCATION_BUDGET,
    LocalProcessingUnavailable,
    ModalUnavailable,
    _current_month_key,
    _invoke_modal,
    _local_embed_text,
    _modal_configured,
    _process_resume_locally,
    _reserve_invocation_slot,
    embed_resume_text,
)


@pytest.fixture(autouse=True)
def _clear_modal_env(monkeypatch):
    """Every test starts with Modal NOT configured and the default
    budget - individual tests opt in to setting these."""
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("MODAL_MONTHLY_INVOCATION_BUDGET", raising=False)


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-test")
    monkeypatch.setenv("MODAL_TOKEN_SECRET", "as-test")


# ---------------------------------------------------------------------
# _modal_configured
# ---------------------------------------------------------------------


def test_not_configured_when_both_env_vars_unset():
    assert _modal_configured() is False


def test_not_configured_when_only_one_env_var_set(monkeypatch):
    monkeypatch.setenv("MODAL_TOKEN_ID", "ak-test")
    assert _modal_configured() is False


def test_configured_when_both_env_vars_set(configured):
    assert _modal_configured() is True


# ---------------------------------------------------------------------
# Budget ledger - _reserve_invocation_slot
# ---------------------------------------------------------------------


def test_reserve_creates_a_row_for_the_current_month_and_increments_it(db_session):
    month = _current_month_key()
    assert db_session.query(ModalUsage).filter_by(month=month).first() is None

    reserved, used_before, budget = _reserve_invocation_slot(db_session)

    assert reserved is True
    assert used_before == 0
    assert budget == DEFAULT_MONTHLY_INVOCATION_BUDGET
    row = db_session.query(ModalUsage).filter_by(month=month).one()
    assert row.invocations_used == 1


def test_reserve_increments_an_existing_row_across_calls(db_session):
    for expected_used_before in range(3):
        reserved, used_before, _ = _reserve_invocation_slot(db_session)
        assert reserved is True
        assert used_before == expected_used_before

    month = _current_month_key()
    row = db_session.query(ModalUsage).filter_by(month=month).one()
    assert row.invocations_used == 3


def test_reserve_refuses_once_budget_is_reached_and_does_not_increment(db_session, monkeypatch):
    """This test drives usage to exactly 100% of a tiny fake budget on
    purpose (to exercise the refusal path below) - which also crosses
    both of Resend Phase B's 80%/95% alert thresholds in that same real
    call. check_and_alert_budget_threshold is mocked here as explicit
    defense in depth, on top of (not instead of) the project-wide
    RESEND_API_KEY-deletion fixture in conftest.py - see that fixture's
    docstring and SESSIONS.md for the real 16-real-email incident this
    is fixing. This test is about reservation/refusal behavior, not
    alerting, so it shouldn't depend on alert internals either way."""
    monkeypatch.setenv("MODAL_MONTHLY_INVOCATION_BUDGET", "2")

    with patch("huntloop.modal_resume_processing.check_and_alert_budget_threshold"):
        first = _reserve_invocation_slot(db_session)
        second = _reserve_invocation_slot(db_session)
        assert first[0] is True
        assert second[0] is True

        third = _reserve_invocation_slot(db_session)
        assert third == (False, 2, 2)

    month = _current_month_key()
    row = db_session.query(ModalUsage).filter_by(month=month).one()
    # The refused third call must not have incremented anything further.
    assert row.invocations_used == 2


def test_reserve_with_zero_budget_refuses_immediately(db_session, monkeypatch):
    monkeypatch.setenv("MODAL_MONTHLY_INVOCATION_BUDGET", "0")

    reserved, used_before, budget = _reserve_invocation_slot(db_session)
    assert reserved is False
    assert used_before == 0
    assert budget == 0


def test_reserve_still_increments_the_ledger_even_if_the_budget_alert_check_blows_up(db_session):
    """Resend Phase B wired a budget-threshold alert check
    (huntloop.budget_alerts.check_and_alert_budget_threshold) right
    after this function's own real increment - the real requirement
    under test: that check failing must never take the real Modal
    invocation-budget increment down with it."""
    with patch(
        "huntloop.modal_resume_processing.check_and_alert_budget_threshold",
        side_effect=RuntimeError("boom - alerting infrastructure is down"),
    ):
        reserved, used_before, budget = _reserve_invocation_slot(db_session)

    assert reserved is True
    assert used_before == 0
    month = _current_month_key()
    # The real assertion: the ledger increment landed and was committed,
    # even though the alert check raised.
    assert db_session.query(ModalUsage).filter_by(month=month).one().invocations_used == 1


# ---------------------------------------------------------------------
# _invoke_modal / _prepare_invocation - lookup happens before the
# budget is touched; the ledger is only reserved once a real handle to
# call is in hand.
# ---------------------------------------------------------------------


def test_invoke_modal_raises_without_touching_the_ledger_when_not_configured(db_session):
    with pytest.raises(ModalUnavailable, match="MODAL_TOKEN_ID"):
        _invoke_modal(db_session, "embed_text_remote", ("some text",), "test")

    month = _current_month_key()
    assert db_session.query(ModalUsage).filter_by(month=month).first() is None


def test_invoke_modal_raises_without_touching_the_ledger_on_lookup_failure(db_session, configured):
    with patch("huntloop.modal_resume_processing.modal.Function.from_name", side_effect=RuntimeError("not deployed")):
        with pytest.raises(ModalUnavailable, match="Could not look up"):
            _invoke_modal(db_session, "embed_text_remote", ("some text",), "test")

    month = _current_month_key()
    assert db_session.query(ModalUsage).filter_by(month=month).first() is None


def test_invoke_modal_reserves_a_slot_before_dispatch_and_returns_the_result(db_session, configured):
    fake_fn = MagicMock()
    fake_fn.remote.return_value = [0.1, 0.2]
    with patch("huntloop.modal_resume_processing.modal.Function.from_name", return_value=fake_fn):
        result = _invoke_modal(db_session, "embed_text_remote", ("some text",), "test")

    assert result == [0.1, 0.2]
    fake_fn.remote.assert_called_once_with("some text")
    month = _current_month_key()
    assert db_session.query(ModalUsage).filter_by(month=month).one().invocations_used == 1


def test_invoke_modal_still_counts_the_slot_when_the_dispatch_itself_fails(db_session, configured):
    """A real invocation that errors mid-dispatch still consumed real
    Modal compute, so it must still count against the budget - see the
    module docstring's Locking/budget section. Confirmed against the
    real integration too: two crash-looping deploys before a working one
    still each incremented the real ledger (see CLAUDE.md)."""
    fake_fn = MagicMock()
    fake_fn.remote.side_effect = RuntimeError("boom mid-call")
    with patch("huntloop.modal_resume_processing.modal.Function.from_name", return_value=fake_fn):
        with pytest.raises(ModalUnavailable, match="failed"):
            _invoke_modal(db_session, "embed_text_remote", ("some text",), "test")

    month = _current_month_key()
    assert db_session.query(ModalUsage).filter_by(month=month).one().invocations_used == 1


def test_invoke_modal_raises_once_budget_is_reached(db_session, configured, monkeypatch):
    """Same reasoning as test_reserve_refuses_once_budget_is_reached_and_does_not_increment
    above - this test's own first call lands at exactly 100% of a tiny
    fake budget (1), which crosses both alert thresholds for real unless
    mocked. Explicit defense-in-depth mock, on top of the project-wide
    RESEND_API_KEY-deletion fixture - see SESSIONS.md for the real
    incident."""
    monkeypatch.setenv("MODAL_MONTHLY_INVOCATION_BUDGET", "1")
    fake_fn = MagicMock()
    fake_fn.remote.return_value = [0.1]

    with patch("huntloop.modal_resume_processing.modal.Function.from_name", return_value=fake_fn), \
         patch("huntloop.modal_resume_processing.check_and_alert_budget_threshold"):
        first = _invoke_modal(db_session, "embed_text_remote", ("a",), "test")
        assert first == [0.1]

        with pytest.raises(ModalUnavailable, match="budget reached"):
            _invoke_modal(db_session, "embed_text_remote", ("b",), "test")

    # Only the first (successful, under-budget) call dispatched - the
    # second never reached fn.remote at all.
    fake_fn.remote.assert_called_once()


# ---------------------------------------------------------------------
# embed_resume_text - the public, catch-everything entry point used by
# PATCH /resumes/{id}/activate
# ---------------------------------------------------------------------


@pytest.fixture()
def stub_embed_text(monkeypatch):
    """Same sys.modules technique as tests/test_api_resumes.py's fixture
    of the same name - huntloop.embeddings can't be imported on this
    machine (no torch), so the lazy `from huntloop.embeddings import
    embed_text` fallback inside embed_resume_text() needs a fake module
    already sitting in sys.modules before it runs."""
    calls = []

    def _fake(text):
        calls.append(text)
        return [9.9, 9.9]

    fake_module = types.ModuleType("huntloop.embeddings")
    fake_module.embed_text = _fake
    monkeypatch.setitem(sys.modules, "huntloop.embeddings", fake_module)
    return calls


def test_embed_resume_text_uses_modal_when_configured_and_available(db_session, configured):
    fake_fn = MagicMock()
    fake_fn.remote.return_value = [0.5, 0.5]
    with patch("huntloop.modal_resume_processing.modal.Function.from_name", return_value=fake_fn):
        embedding, used_modal = embed_resume_text(db_session, "some resume text")

    assert embedding == [0.5, 0.5]
    assert used_modal is True


def test_embed_resume_text_falls_back_locally_when_not_configured(db_session, stub_embed_text):
    embedding, used_modal = embed_resume_text(db_session, "some resume text")

    assert used_modal is False
    assert embedding == [9.9, 9.9]
    assert stub_embed_text == ["some resume text"]


def test_embed_resume_text_falls_back_locally_when_modal_dispatch_fails(db_session, configured, stub_embed_text):
    fake_fn = MagicMock()
    fake_fn.remote.side_effect = RuntimeError("bad token")
    with patch("huntloop.modal_resume_processing.modal.Function.from_name", return_value=fake_fn):
        embedding, used_modal = embed_resume_text(db_session, "some resume text")

    assert used_modal is False
    assert embedding == [9.9, 9.9]


def test_embed_resume_text_falls_back_locally_when_budget_exhausted(db_session, configured, monkeypatch, stub_embed_text):
    monkeypatch.setenv("MODAL_MONTHLY_INVOCATION_BUDGET", "0")

    embedding, used_modal = embed_resume_text(db_session, "some resume text")

    assert used_modal is False
    assert embedding == [9.9, 9.9]


# ---------------------------------------------------------------------
# Weighted reservations (added with the Resume Deep-Dive, which reserves
# MODAL_DEEP_DIVE_WEIGHT slots instead of 1 - see
# huntloop.modal_deep_dive). Every pre-existing caller still passes no
# weight at all, so the tests above cover the default-1 behaviour; these
# cover the weighting itself.
# ---------------------------------------------------------------------


def test_reserve_defaults_to_weight_one(db_session):
    reserved, used_before, budget = _reserve_invocation_slot(db_session)

    assert reserved is True
    assert used_before == 0
    row = db_session.query(ModalUsage).filter_by(month=_current_month_key()).one()
    assert row.invocations_used == 1


def test_reserve_increments_by_the_given_weight(db_session):
    reserved, _used_before, _budget = _reserve_invocation_slot(db_session, weight=5)

    assert reserved is True
    row = db_session.query(ModalUsage).filter_by(month=_current_month_key()).one()
    assert row.invocations_used == 5


def test_reserve_refuses_rather_than_overshooting_the_cap(db_session, monkeypatch):
    """A weight-5 request with only 3 slots left is refused outright and
    charges nothing - leaving those 3 available to a weight-1 caller -
    rather than being partially granted or allowed over the cap."""
    monkeypatch.setenv("MODAL_MONTHLY_INVOCATION_BUDGET", "10")
    db_session.add(ModalUsage(month=_current_month_key(), invocations_used=7))
    db_session.commit()

    reserved, used_before, budget = _reserve_invocation_slot(db_session, weight=5)

    assert reserved is False
    assert (used_before, budget) == (7, 10)
    row = db_session.query(ModalUsage).filter_by(month=_current_month_key()).one()
    db_session.refresh(row)
    assert row.invocations_used == 7

    # ... and a weight-1 caller can still use what's left.
    assert _reserve_invocation_slot(db_session, weight=1)[0] is True
    db_session.refresh(row)
    assert row.invocations_used == 8


def test_reserve_allows_a_weight_that_lands_exactly_on_the_cap(db_session, monkeypatch):
    monkeypatch.setenv("MODAL_MONTHLY_INVOCATION_BUDGET", "10")
    db_session.add(ModalUsage(month=_current_month_key(), invocations_used=5))
    db_session.commit()

    assert _reserve_invocation_slot(db_session, weight=5)[0] is True
    row = db_session.query(ModalUsage).filter_by(month=_current_month_key()).one()
    db_session.refresh(row)
    assert row.invocations_used == 10
    # And now nothing more fits, not even weight 1.
    assert _reserve_invocation_slot(db_session, weight=1)[0] is False


def test_reserve_treats_a_nonsense_weight_as_one(db_session):
    assert _reserve_invocation_slot(db_session, weight=0)[0] is True
    row = db_session.query(ModalUsage).filter_by(month=_current_month_key()).one()
    assert row.invocations_used == 1


def test_weighted_reservation_crossing_two_alert_thresholds_fires_each_once(db_session, monkeypatch):
    """A weighted jump can cross 80% AND 95% in a single reservation.
    huntloop.budget_alerts should then send both, once each, and never
    re-fire for a threshold already passed - the behaviour weighting
    could plausibly have broken, so it's asserted rather than assumed."""
    monkeypatch.setenv("MODAL_MONTHLY_INVOCATION_BUDGET", "100")
    db_session.add(ModalUsage(month=_current_month_key(), invocations_used=70))
    db_session.commit()

    sent = []

    def _fake_send(db, *, ledger, period, used, cap, threshold):
        sent.append(threshold)

    monkeypatch.setattr("huntloop.budget_alerts._send_threshold_alert", _fake_send)

    # 70 -> 97: crosses both 80 and 95 in one step.
    assert _reserve_invocation_slot(db_session, weight=27)[0] is True
    assert sorted(sent) == [80, 95]

    # A further reservation in the same period re-fires nothing.
    sent.clear()
    assert _reserve_invocation_slot(db_session, weight=1)[0] is True
    assert sent == []


# ---------------------------------------------------------------------
# LocalProcessingUnavailable - the local fallback's own lazy
# sentence-transformers import failing, with no Modal to fall back from.
# ---------------------------------------------------------------------


@pytest.fixture()
def no_embeddings_module(monkeypatch):
    """Simulates an environment with no sentence-transformers/torch at
    all (the lean demo image, and this project's own dev venv) by making
    `import huntloop.embeddings` raise ImportError. sys.modules is set to
    None, which Python's import system treats as "this module is
    definitively absent" and raises ImportError for - the same
    sys.modules technique the stub fixtures above use, inverted."""
    monkeypatch.setitem(sys.modules, "huntloop.embeddings", None)


def test_local_embed_raises_typed_error_when_the_import_fails(no_embeddings_module):
    with pytest.raises(LocalProcessingUnavailable) as exc:
        _local_embed_text("some text")

    assert "sentence-transformers" in str(exc.value)


def test_embed_resume_text_raises_local_processing_unavailable(db_session, no_embeddings_module):
    """Not configured for Modal AND no local fallback available - the
    one case where this function is allowed to raise rather than
    returning a usable result. huntloop.api.routers.resumes turns this
    into a structured 503 instead of a bodyless 500."""
    with pytest.raises(LocalProcessingUnavailable):
        embed_resume_text(db_session, "some resume text")


def test_process_resume_locally_raises_local_processing_unavailable(no_embeddings_module, monkeypatch, tmp_path):
    fake_ingestion = types.ModuleType("huntloop.resume_ingestion")
    fake_ingestion.extract_text = lambda path: "real extracted text"
    monkeypatch.setitem(sys.modules, "huntloop.resume_ingestion", fake_ingestion)

    with pytest.raises(LocalProcessingUnavailable):
        _process_resume_locally(str(tmp_path / "x.pdf"))


def test_local_path_still_works_when_the_module_is_importable(db_session, stub_embed_text):
    """The guard must not change the happy path - with an importable
    huntloop.embeddings, nothing raises and the embedding comes back."""
    embedding, used_modal = embed_resume_text(db_session, "some resume text")
    assert (embedding, used_modal) == ([9.9, 9.9], False)

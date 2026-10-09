"""
Shared budget-threshold email alerting for every usage ledger in this
project - Phase B of the Resend integration (see CLAUDE.md/SESSIONS.md),
built on top of Phase A's huntloop.resend_client/huntloop.resend_metrics.

Public surface:

    check_and_alert_budget_threshold(db, ledger=, period=, used=, cap=)

Called right after a real usage increment commits, from each of the
three existing budget ledgers this phase wires in:
  - scripts/backfill_company_research.py's _record_request_spent()
    (tavily_usage, monthly)
  - huntloop.modal_resume_processing._reserve_invocation_slot()
    (modal_usage, monthly)
  - scripts/triage_feedback.py's per-row triage loop (the
    TRIAGE_DAILY_BUDGET daily count, derived from a live COUNT query
    against `feedback` - there is no dedicated incrementing ledger row
    for it the way the other two have)

Design decision (flagged, per the task's own "your call" allowance,
rather than silently picked): a `last_alerted_threshold` column could
have been added directly to tavily_usage and modal_usage, but that still
leaves triage's daily budget with nowhere to record state, since it has
no ledger table at all - only a derived count. Rather than inventing a
ledger-shaped table just to hold one counter for triage, OR mixing two
different tracking mechanisms across the three ledgers, this module uses
ONE shared table, `huntloop.db_models.BudgetAlertState`, keyed by
(ledger, period) - a ledger name and that ledger's current reset window
("YYYY-MM" for the two monthly ledgers, "YYYY-MM-DD" for triage's daily
one). This keeps tavily_usage/modal_usage (already-merged Phase A/
company-research schema) completely untouched, and generalizes to any
future budget-tracked integration with no new migration.

Thresholds: 80% and 95% of the given cap, each fired AT MOST ONCE per
(ledger, period) - tracked via BudgetAlertState.last_threshold, which
only ever increases within a period and naturally re-arms once the
period string itself changes (a new calendar month, or a new UTC day for
triage) - a new period has no row yet, so both thresholds are
unfired again. If a single increment jumps straight past both
thresholds in one step, both alerts fire, in ascending order - each is
still a genuine, distinct, first-time crossing for that period.

Concurrency: this ledger is read/incremented from potentially-
concurrent callers (e.g. two overlapping `POST /resumes/upload` requests
could both push the `modal` ledger across a threshold at once), so
_get_or_create_state() uses the same `SELECT ... FOR UPDATE` +
IntegrityError-on-creation + `.populate_existing()` treatment as
huntloop.resend_client.ResendUsage - see that module's docstring for the
two real concurrency bugs (a row-creation race, and a stale
identity-map read surviving the row lock) that pattern exists to avoid;
both are latent in any naive "read row, maybe create it, lock it,
compare, increment" ledger shape, not unique to resend_usage.

Send pattern - deliberately NOT the same literal mechanism as the
feedback-submission alert (huntloop.feedback_alerts), though it has the
same effect ("never blocks or fails the real operation"): the feedback
alert runs inside a live HTTP request (POST /feedback) and can use
FastAPI's BackgroundTasks to defer the send past the response. This
module is called from three very different contexts - two plain
synchronous scripts with no request/response cycle at all
(backfill_company_research.py, triage_feedback.py), and Modal's budget
check, which runs inline on both a sync code path (PATCH
/resumes/{id}/activate) and directly on the event loop inside an async
one (POST /resumes/upload - see huntloop.modal_resume_processing's own
docstring on why _prepare_invocation's DB calls already run
synchronously there even in the async path, pre-existing from Phase A,
not introduced here). There is no BackgroundTasks-equivalent available
at this shared, non-request-scoped layer. So "never blocks/fails the
real operation" is enforced the same way every other best-effort side
effect in this project already is (see e.g.
huntloop.metrics.push_run_metrics's docstring): by catching every
exception here, never letting one escape to the caller - not by true
async deferral. Threading real backgrounding through two standalone
scripts and a library function called from both a sync and an async
request path would be a much larger refactor than this phase's "wire
two real triggers" scope.
"""
import logging
import os

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from huntloop.db_models import BudgetAlertState
from huntloop.resend_client import ResendUnavailable, send_email

logger = logging.getLogger(__name__)

# Fired in ascending order - see module docstring for the "both fire if
# both are newly crossed in one step" behavior.
ALERT_THRESHOLDS_PERCENT = (80, 95)

ALERT_TO_ADDRESS_ENV = "RESEND_ALERT_TO_ADDRESS"
DEFAULT_ALERT_TO_ADDRESS = "niramayrkelkar@gmail.com"


def _alert_to_address() -> str:
    return os.getenv(ALERT_TO_ADDRESS_ENV, DEFAULT_ALERT_TO_ADDRESS)


def _get_or_create_state(db: Session, ledger: str, period: str) -> BudgetAlertState:
    """Mirrors huntloop.resend_client._reserve_send_slot()'s row
    creation/locking - see this module's own docstring for why."""
    row = db.query(BudgetAlertState).filter_by(ledger=ledger, period=period).first()
    if row is None:
        try:
            row = BudgetAlertState(ledger=ledger, period=period, last_threshold=0)
            db.add(row)
            db.commit()
        except IntegrityError:
            db.rollback()

    return (
        db.query(BudgetAlertState)
        .filter_by(ledger=ledger, period=period)
        .with_for_update()
        .populate_existing()
        .first()
    )


def _send_threshold_alert(db: Session, *, ledger: str, period: str, used: int, cap: int, threshold: int) -> None:
    pct_actual = (used / cap) * 100 if cap else 0.0
    subject = f"HuntLoop: {ledger} budget at {threshold}% ({period})"
    html = (
        f"<p>The <strong>{ledger}</strong> usage ledger has crossed "
        f"<strong>{threshold}%</strong> of its budget for <strong>{period}</strong>.</p>"
        f"<p>Current usage: <strong>{used} / {cap}</strong> ({pct_actual:.1f}%).</p>"
    )
    try:
        send_email(db, to=_alert_to_address(), subject=subject, html=html)
        logger.info(f"Sent budget-threshold alert: ledger={ledger} period={period} threshold={threshold}%")
    except ResendUnavailable as e:
        logger.warning(
            f"Budget-threshold alert email not sent (ledger={ledger}, period={period}, "
            f"threshold={threshold}%, reason={e.reason}): {e}"
        )


def check_and_alert_budget_threshold(db: Session, *, ledger: str, period: str, used: int, cap: int) -> None:
    """Call immediately after a real usage increment commits for
    `ledger` in the given `period`. Sends one email per newly-crossed
    threshold (80%, then 95%) that hasn't already been alerted on for
    this exact (ledger, period) - never re-fires for one already
    alerted. Never raises - see module docstring's Send pattern
    section; any failure (DB, Resend, or otherwise) is logged and
    swallowed so the real usage increment this is reporting on is never
    affected."""
    if cap <= 0:
        return
    try:
        state = _get_or_create_state(db, ledger, period)
        pct = (used / cap) * 100
        newly_crossed = [t for t in ALERT_THRESHOLDS_PERCENT if pct >= t and t > state.last_threshold]
        if not newly_crossed:
            db.commit()  # releases the row lock even when nothing to do
            return

        for threshold in newly_crossed:
            _send_threshold_alert(db, ledger=ledger, period=period, used=used, cap=cap, threshold=threshold)
            state.last_threshold = threshold
        db.commit()
    except Exception:
        logger.exception(
            f"Budget-threshold alert check failed for ledger={ledger} period={period} "
            f"(used={used}, cap={cap}) - the underlying usage increment is unaffected"
        )

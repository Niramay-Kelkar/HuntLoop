"""
Knock notification-orchestration layer, sitting in front of the
existing Resend email-alert infrastructure (huntloop.resend_client /
huntloop.resend_metrics - see CLAUDE.md/SESSIONS.md). Modeled directly
on that module's budget-ledger/locking pattern and on
huntloop.resend_metrics' metrics pattern - resend_client is itself the
established template for "a third-party API this project calls under a
hard free-tier quota, from potentially-concurrent callers," and this
module is a close copy of it, swapping Resend's direct-send call for a
Knock workflow trigger.

Resend remains the actual delivery provider today - Knock is configured
(in Knock's own dashboard, not in this codebase) to send email via a
Resend-backed channel for now. This module does not talk to Resend
directly at all; it only triggers a named Knock workflow and lets Knock
decide how to fan that out. That's also why `subject`/`html` are
forwarded here as `data.subject`/`data.html` verbatim, rather than
reshaped into some Knock-native field - it keeps the Knock workflow's
email step a thin pass-through ({{ data.subject }} / {{ data.html }}),
which is exactly what lets a second channel (in-app, Slack, ...) be
added later as a pure Knock-side workflow-config change, with zero code
changes in this module.

Public surface:

    trigger_workflow(db, *, workflow_key=..., to=..., subject=..., html=...) -> None

Raises the typed KnockUnavailable (and only that) on any failure - not
configured, today's/this month's trigger budget exhausted, or the Knock
API call itself erroring. Like huntloop.resend_client.send_email(), it
does NOT swallow KnockUnavailable itself - there is no local fallback
for "trigger a notification workflow" the way there is for Modal's
resume-processing fallback. Every caller (huntloop.feedback_alerts,
huntloop.budget_alerts) must decide for itself whether a failed trigger
should be logged-and-ignored or retried - both current callers log and
swallow it, mirroring their existing Resend behavior.

Auth: KNOCK_API_KEY env var (a single API key) - stored the same way
RESEND_API_KEY/GROQ_API_KEY/GEMINI_API_KEY/TAVILY_API_KEY/MODAL_TOKEN_ID
are (see .env.example). Missing it is treated as "not configured" and
raises immediately, no partial-config attempt. This is an externally
provided secret - nothing in this codebase invents or defaults a real
value for it.

Budget ledger: knock_usage (one row per calendar day) - modeled on
resend_usage, tracking TWO caps against the same ledger:
KNOCK_DAILY_TRIGGER_BUDGET (default 100) and
KNOCK_MONTHLY_TRIGGER_BUDGET (default 3000). Both are checked before
every trigger; hitting either one blocks the trigger with a distinct
reason, same "check before every call, not just once at startup"
principle resend_client's budget check uses.

Locking: this ledger is read/incremented from potentially-concurrent
callers (a live feedback-submission request and a budget-threshold
alert could genuinely overlap), so _reserve_trigger_slot() takes a
`SELECT ... FOR UPDATE` row lock on TODAY's row for the
check-then-increment - the exact same two real concurrency bugs
resend_client._reserve_send_slot() was built to fix are reproduced here
unchanged: (a) the first reservation of a new day racing a row-creation
INSERT, fixed by catching the resulting IntegrityError, rolling back,
and falling through to the locked re-select; and (b) SQLAlchemy's
identity map quietly handing back an earlier, stale-attribute row object
for a repeat query on the same primary key instead of the row the
FOR UPDATE query actually just locked, fixed with `.populate_existing()`
on that locked re-select. See resend_client's module docstring for the
full explanation of both - they are latent in any naive "read row, maybe
create it, lock it, compare, increment" ledger shape, not unique to
either module.

Metrics: every trigger attempt (success or any failure reason) and
every budget-ledger check is recorded to Prometheus via
huntloop.knock_metrics - the same CollectorRegistry/Pushgateway pattern
huntloop.resend_metrics already established, under its own job name
("huntloop_knock") so a push here never collides with Resend's.
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from huntloop.db_models import KnockUsage
from huntloop.knock_metrics import record_knock_budget, record_knock_trigger

load_dotenv()

logger = logging.getLogger(__name__)

# Plain env vars so they can be tuned without a code change once this
# account's real plan/usage is known - same reasoning as Resend's own
# DEFAULT_DAILY_EMAIL_BUDGET/DEFAULT_MONTHLY_EMAIL_BUDGET.
DEFAULT_DAILY_TRIGGER_BUDGET = 100
DEFAULT_MONTHLY_TRIGGER_BUDGET = 3000

KNOCK_API_BASE_URL = os.getenv("KNOCK_API_BASE_URL", "https://api.knock.app")
KNOCK_API_TIMEOUT_SECONDS = 10


class KnockUnavailable(Exception):
    """Raised by trigger_workflow() whenever a notification could not be
    triggered - not configured, today's or this month's trigger budget
    exhausted, or the real Knock API call itself failing. `reason` is a
    short machine-readable tag (not_configured / daily_budget_exhausted /
    monthly_budget_exhausted / trigger_error) used to label the
    corresponding metric in huntloop.knock_metrics, mirroring
    huntloop.resend_client.ResendUnavailable's `reason` convention.

    Like ResendUnavailable, this is NOT guaranteed to be caught
    somewhere inside this module - there is no local fallback for
    "trigger a notification workflow". Every caller must handle it
    explicitly."""

    def __init__(self, message: str, reason: str = "trigger_error"):
        super().__init__(message)
        self.reason = reason


def _configured() -> bool:
    return bool(os.getenv("KNOCK_API_KEY"))


def _daily_budget() -> int:
    return int(os.getenv("KNOCK_DAILY_TRIGGER_BUDGET", str(DEFAULT_DAILY_TRIGGER_BUDGET)))


def _monthly_budget() -> int:
    return int(os.getenv("KNOCK_MONTHLY_TRIGGER_BUDGET", str(DEFAULT_MONTHLY_TRIGGER_BUDGET)))


def _current_day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _current_month_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _default_recipient_id(to: str) -> str:
    """Derives a Knock-legal recipient id from a plain email address -
    Knock recipient ids can't contain "@"/".", so this is a simple,
    deterministic, reversible-by-eye transform, used whenever a caller
    doesn't already have a real recipient id of its own to pass in."""
    return to.replace("@", "-at-").replace(".", "-")


@dataclass
class _ReservationDenial:
    reason: str  # "daily_budget_exhausted" | "monthly_budget_exhausted"
    daily_used: int
    daily_budget: int
    monthly_used: int
    monthly_budget: int


def _monthly_used(db: Session, month_prefix: str) -> int:
    return (
        db.query(func.coalesce(func.sum(KnockUsage.triggers_sent), 0))
        .filter(KnockUsage.day.like(f"{month_prefix}%"))
        .scalar()
    )


def _reserve_trigger_slot(db: Session) -> tuple[bool, _ReservationDenial | None]:
    """Atomically checks today's AND this month's trigger budget and, if
    there's room under both, reserves one slot by incrementing today's
    row immediately - see module docstring's Locking section. Returns
    (reserved, denial) - denial is None on success; never raises on a
    normal "budget exhausted" outcome (that's communicated via the
    returned value), only trigger_workflow() raises KnockUnavailable,
    with the context to build a clear message."""
    day = _current_day_key()
    row = db.query(KnockUsage).filter_by(day=day).first()
    if row is None:
        # Two concurrent callers can both see no row for today and both
        # try to create one - same real race resend_client's
        # _reserve_send_slot() fixes the same way. The loser's INSERT
        # fails with IntegrityError; that just means the winner already
        # created today's row, so roll back and fall through to the
        # locked re-select below, which will find it either way.
        try:
            row = KnockUsage(day=day, triggers_sent=0)
            db.add(row)
            db.commit()
        except IntegrityError:
            db.rollback()

    # Re-select WITH the lock now that today's row definitely exists -
    # held only across this one check-then-increment, not the whole
    # trigger attempt. `.populate_existing()` is required here, not
    # cosmetic: see resend_client._reserve_send_slot()'s own comment at
    # the equivalent call for the full explanation - without it, every
    # caller computes `daily_used_before` from its own stale first read
    # instead of the locked, current value, so the FOR UPDATE lock
    # serializes DB access but the Python-side numbers racing through it
    # are wrong anyway.
    row = db.query(KnockUsage).filter_by(day=day).with_for_update().populate_existing().first()
    daily_budget = _daily_budget()
    monthly_budget = _monthly_budget()
    daily_used_before = row.triggers_sent
    monthly_used_before = _monthly_used(db, _current_month_prefix())

    if daily_used_before >= daily_budget:
        db.commit()  # releases the row lock; nothing incremented
        record_knock_budget(
            daily_used=daily_used_before, daily_cap=daily_budget,
            monthly_used=monthly_used_before, monthly_cap=monthly_budget,
        )
        return False, _ReservationDenial(
            "daily_budget_exhausted", daily_used_before, daily_budget, monthly_used_before, monthly_budget
        )

    if monthly_used_before >= monthly_budget:
        db.commit()
        record_knock_budget(
            daily_used=daily_used_before, daily_cap=daily_budget,
            monthly_used=monthly_used_before, monthly_cap=monthly_budget,
        )
        return False, _ReservationDenial(
            "monthly_budget_exhausted", daily_used_before, daily_budget, monthly_used_before, monthly_budget
        )

    row.triggers_sent = daily_used_before + 1
    db.commit()
    record_knock_budget(
        daily_used=daily_used_before + 1, daily_cap=daily_budget,
        monthly_used=monthly_used_before + 1, monthly_cap=monthly_budget,
    )
    return True, None


def trigger_workflow(
    db: Session,
    *,
    workflow_key: str,
    to: str,
    subject: str,
    html: str,
    recipient_id: str | None = None,
    recipient_name: str | None = None,
    data: dict | None = None,
) -> None:
    """Triggers exactly one Knock workflow run, after reserving a budget
    slot. Raises KnockUnavailable (and only that) on: Knock not
    configured, today's/this month's trigger budget exhausted, or the
    real Knock API call itself failing. See module docstring for why
    this does NOT swallow that exception the way
    huntloop.modal_resume_processing's public functions swallow
    ModalUnavailable - there is no local fallback for triggering a
    notification, so the caller must decide what to do.

    `subject`/`html` are merged into the workflow's `data` payload
    verbatim (as `data.subject`/`data.html`), alongside any extra
    `data` the caller passes in - see module docstring for why. The
    recipient is upserted inline from `to` (plus optional
    `recipient_id`/`recipient_name`), so Knock never needs the
    recipient to already exist."""
    if not _configured():
        record_knock_trigger(outcome="not_configured")
        raise KnockUnavailable("KNOCK_API_KEY is not set", reason="not_configured")

    reserved, denial = _reserve_trigger_slot(db)
    if not reserved:
        if denial.reason == "daily_budget_exhausted":
            record_knock_trigger(outcome="daily_budget_exhausted")
            raise KnockUnavailable(
                f"Knock daily trigger budget reached ({denial.daily_used}/{denial.daily_budget} for "
                f"{_current_day_key()}) - no more triggers until tomorrow (UTC)",
                reason="daily_budget_exhausted",
            )
        record_knock_trigger(outcome="monthly_budget_exhausted")
        raise KnockUnavailable(
            f"Knock monthly trigger budget reached ({denial.monthly_used}/{denial.monthly_budget} for "
            f"{_current_month_prefix()}) - no more triggers until next calendar month",
            reason="monthly_budget_exhausted",
        )

    trigger_data = {"subject": subject, "html": html, **(data or {})}
    recipient = {"id": recipient_id or _default_recipient_id(to), "email": to}
    if recipient_name is not None:
        recipient["name"] = recipient_name

    try:
        response = requests.post(
            f"{KNOCK_API_BASE_URL}/v1/workflows/{workflow_key}/trigger",
            headers={"Authorization": f"Bearer {os.environ['KNOCK_API_KEY']}"},
            json={"recipients": [recipient], "data": trigger_data},
            timeout=KNOCK_API_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Knock API call failed (workflow_key={workflow_key!r}, to={to!r}): {e}")
        record_knock_trigger(outcome="trigger_error")
        raise KnockUnavailable(
            f"Knock API call failed ({type(e).__name__}): {e}", reason="trigger_error"
        ) from e

    logger.info(f"Triggered Knock workflow (workflow_key={workflow_key!r}, subject={subject!r}, to={to!r})")
    record_knock_trigger(outcome="success")

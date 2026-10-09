"""
Resend email-alert infrastructure - Phase A of the Resend integration
(see CLAUDE.md/SESSIONS.md for the full phasing). Modeled directly on
huntloop.modal_resume_processing (its budget-ledger/locking pattern) and
huntloop.modal_metrics (its metrics pattern) - the two established
templates for "a third-party API this project calls under a hard free-tier
quota, from potentially-concurrent callers."

This is for the project owner's own local/production use, not the public
demo - there is no multi-user/account system, so every alert this
eventually sends is meant to go to the project owner, never to an
arbitrary visitor. This module itself takes `to` as a plain argument and
does not hardcode or default it to anything - deciding the actual
recipient address (likely a fixed env var, mirroring
RESEND_FROM_ADDRESS) is left to whichever later phase first calls
send_email(), since nothing does yet.

Public surface (nothing calls this yet - see below):

    send_email(db, to=..., subject=..., html=...) -> None

Raises the typed ResendUnavailable (and only that) on any failure -
not configured, today's/this month's send budget exhausted, or the
Resend API call itself erroring. It never sends unhandled exceptions to
a caller, but UNLIKE huntloop.modal_resume_processing's
process_resume()/embed_resume_text() (which always have a local fallback
to fall back to), send_email() does NOT swallow ResendUnavailable itself
- there is no local fallback for "send an email" the way there is for
"parse a resume locally instead of on Modal". Every future caller
(a feedback-alert hook, a job-match-alert cron stage) must decide for
itself whether a failed send should be logged-and-ignored (most likely,
mirroring this project's "a best-effort side effect must never break the
real work" principle used throughout - see e.g.
huntloop.metrics.push_run_metrics) or retried. Phase A deliberately does
not make that decision, since nothing is wired to call this yet.

Auth: RESEND_API_KEY env var (a single API key, not Modal's two-part
token) - stored the same way GROQ_API_KEY/GEMINI_API_KEY/TAVILY_API_KEY/
MODAL_TOKEN_ID are (see .env.example). Missing it is treated as "not
configured" and raises immediately, no partial-config attempt.

Sending model: Resend's Python SDK (`resend.Emails.send({...})`) needs a
verified sending domain for anything beyond its own sandbox test address
(onboarding@resend.dev) - that is a one-time, manual DNS setup step
outside this codebase, not something this module can detect or work
around. RESEND_FROM_ADDRESS defaults to the sandbox address so this
module is importable and testable with zero setup; a real deployment
must override it once a domain is verified, or every real send will
fail with a Resend-side authorization error (surfaced here as a
send_error, not a crash).

Budget ledger: resend_usage (one row per calendar day) - modeled on
tavily_usage/modal_usage (see huntloop.company_research /
huntloop.modal_resume_processing / CLAUDE.md), but tracking TWO caps
against the same ledger: RESEND_DAILY_EMAIL_BUDGET (Resend's free tier
hard-caps at 100/day - the tighter constraint) and
RESEND_MONTHLY_EMAIL_BUDGET (3,000/month). Both are checked before every
send; hitting either one blocks the send with a distinct reason, same
"check before every call, not just once at startup" principle
huntloop.company_research's Tavily budget check uses.

Locking: this ledger is read/incremented from potentially-concurrent
callers once Phase B/C exist (a live feedback-submission request and a
cron-based job-match-alert stage could genuinely overlap), so
_reserve_send_slot() takes a `SELECT ... FOR UPDATE` row lock on
TODAY's row for the check-then-increment - same reasoning as
huntloop.modal_resume_processing._reserve_invocation_slot(), which this
is a close copy of. One real race the Modal reference pattern also has,
but never exercises hard enough to hit in practice: the very first
reservation of a new day, when today's row doesn't exist yet, has two
steps (create-the-row, then lock-and-increment) - two callers racing the
"doesn't exist yet" moment can both try to INSERT it. Confirmed for
real (not hypothetically) by a 20-thread concurrency test here hitting
the unique constraint on `day`; fixed by catching that IntegrityError
and falling through to the locked re-select, which finds whichever
caller's row won. A second, related real bug the same concurrency test caught: even with
the row lock correctly serializing DB access, each caller's in-Python
`row` object for an already-existing day was first loaded by the
earlier plain (unlocked) query, and SQLAlchemy's identity map quietly
reused that same stale-attribute object for the later FOR UPDATE query
instead of refreshing it - see `_reserve_send_slot`'s own comment at the
`.populate_existing()` call for the full explanation. Both of these are
latent in huntloop.modal_resume_processing._reserve_invocation_slot()
too (same two-query shape), just never exercised hard enough there to
surface - worth carrying both fixes back if that code path is ever
touched again. The monthly total is read via a plain (unlocked) sum
over this same table's rows for the current month; only today's row is
lock-protected. That is a deliberate, acceptable gap for a single-operator
alerting feature with low send volume (unlike a billing-critical
counter): the daily lock is what actually prevents a double-send inside
one calendar day, and an occasional off-by-one on the monthly total under
rare simultaneous cross-day sends costs nothing worse than one email
sent slightly early relative to the cap.

The budget slot is reserved only once Resend is confirmed configured -
there is no separate "lookup" step the way Modal has (no deployed app
to resolve a handle for), so unlike
huntloop.modal_resume_processing._prepare_invocation(), the slot is
reserved before the real HTTP call to Resend, not after a lookup
succeeds. A send that fails inside the real API call (bad domain,
network error, Resend-side error) still consumes its reserved slot -
same principle as Modal's "a real invocation that errors mid-dispatch
still consumed real compute, so it still counts" - here read as "we
decided to spend a budget slot attempting this send" rather than "we
know Resend definitely charged a quota unit for it" (Resend's API gives
no way to tell the difference after the fact).

Metrics: every send attempt (success or any failure reason) and every
budget-ledger check is recorded to Prometheus via huntloop.resend_metrics
- the same CollectorRegistry/Pushgateway pattern huntloop.modal_metrics/
huntloop.feedback_metrics already established.
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import resend
from dotenv import load_dotenv
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from huntloop.db_models import ResendUsage
from huntloop.resend_metrics import record_resend_budget, record_resend_send

load_dotenv()

logger = logging.getLogger(__name__)

# See module docstring - these are Resend's real published free-tier
# caps (confirmed against current third-party pricing summaries at the
# time this was written, since Resend's own pricing page isn't directly
# scrapable - re-check resend.com/pricing if these ever look wrong).
# Both are plain env vars so they can be tuned without a code change
# once this account's real plan/usage is known.
DEFAULT_DAILY_EMAIL_BUDGET = 100
DEFAULT_MONTHLY_EMAIL_BUDGET = 3000

# Resend's own sandbox sender/recipient addresses - usable with zero
# domain setup, but only for testing (real third-party recipients will
# not actually receive mail sent from this address). A real deployment
# must set RESEND_FROM_ADDRESS once a sending domain is verified - see
# module docstring.
_SANDBOX_FROM_ADDRESS = "HuntLoop <onboarding@resend.dev>"


class ResendUnavailable(Exception):
    """Raised by send_email() whenever an email could not be sent - not
    configured, today's or this month's send budget exhausted, or the
    real Resend API call itself failing. `reason` is a short
    machine-readable tag (not_configured / daily_budget_exhausted /
    monthly_budget_exhausted / send_error) used to label the
    corresponding metric in huntloop.resend_metrics, mirroring
    huntloop.modal_resume_processing.ModalUnavailable's `reason`
    convention.

    Unlike ModalUnavailable, this is NOT guaranteed to be caught
    somewhere inside this module - see the module docstring's note that
    there is no local fallback for "send an email". Every caller must
    handle it explicitly."""

    def __init__(self, message: str, reason: str = "error"):
        super().__init__(message)
        self.reason = reason


def _configured() -> bool:
    return bool(os.getenv("RESEND_API_KEY"))


def _daily_budget() -> int:
    return int(os.getenv("RESEND_DAILY_EMAIL_BUDGET", str(DEFAULT_DAILY_EMAIL_BUDGET)))


def _monthly_budget() -> int:
    return int(os.getenv("RESEND_MONTHLY_EMAIL_BUDGET", str(DEFAULT_MONTHLY_EMAIL_BUDGET)))


def _current_day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _current_month_prefix() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


@dataclass
class _ReservationDenial:
    reason: str  # "daily_budget_exhausted" | "monthly_budget_exhausted"
    daily_used: int
    daily_budget: int
    monthly_used: int
    monthly_budget: int


def _monthly_used(db: Session, month_prefix: str) -> int:
    return (
        db.query(func.coalesce(func.sum(ResendUsage.emails_sent), 0))
        .filter(ResendUsage.day.like(f"{month_prefix}%"))
        .scalar()
    )


def _reserve_send_slot(db: Session) -> tuple[bool, _ReservationDenial | None]:
    """Atomically checks today's AND this month's send budget and, if
    there's room under both, reserves one slot by incrementing today's
    row immediately - see module docstring's Locking section. Returns
    (reserved, denial) - denial is None on success; never raises on a
    normal "budget exhausted" outcome (that's communicated via the
    returned value), only send_email() raises ResendUnavailable, with
    the context to build a clear message."""
    day = _current_day_key()
    row = db.query(ResendUsage).filter_by(day=day).first()
    if row is None:
        # Two concurrent callers can both see no row for today and both
        # try to create one - confirmed for real, not hypothetically, by
        # a 20-thread concurrency test hitting the unique constraint on
        # `day` here. The loser's INSERT fails with IntegrityError; that
        # just means the winner already created today's row, so roll
        # back and fall through to the locked re-select below, which
        # will find it either way.
        try:
            row = ResendUsage(day=day, emails_sent=0)
            db.add(row)
            db.commit()
        except IntegrityError:
            db.rollback()

    # Re-select WITH the lock now that today's row definitely exists -
    # held only across this one check-then-increment, not the whole
    # send attempt. `.populate_existing()` is required here, not
    # cosmetic: the plain `.first()` query above (the "does today's row
    # exist yet" check) already loaded this same row into the session's
    # identity map on every call after the first one today, and
    # SQLAlchemy's default identity-map behavior is to hand back that
    # already-loaded Python object as-is on a repeat query for the same
    # primary key, WITHOUT overwriting its attributes from the new
    # result - even though the new query is the one actually holding the
    # row lock and reading committed-fresh data. Without this, every
    # caller computes `daily_used_before` from its own stale first read
    # instead of the locked, current value, so the FOR UPDATE lock
    # serializes DB access but the Python-side numbers racing through it
    # are wrong anyway - confirmed for real: the same 20-thread
    # concurrency test that caught the row-creation race above also
    # caught this, silently dropping most of 20 concurrent reservations
    # down to 2 actually-recorded increments before this fix.
    row = db.query(ResendUsage).filter_by(day=day).with_for_update().populate_existing().first()
    daily_budget = _daily_budget()
    monthly_budget = _monthly_budget()
    daily_used_before = row.emails_sent
    monthly_used_before = _monthly_used(db, _current_month_prefix())

    if daily_used_before >= daily_budget:
        db.commit()  # releases the row lock; nothing incremented
        record_resend_budget(
            daily_used=daily_used_before, daily_cap=daily_budget,
            monthly_used=monthly_used_before, monthly_cap=monthly_budget,
        )
        return False, _ReservationDenial(
            "daily_budget_exhausted", daily_used_before, daily_budget, monthly_used_before, monthly_budget
        )

    if monthly_used_before >= monthly_budget:
        db.commit()
        record_resend_budget(
            daily_used=daily_used_before, daily_cap=daily_budget,
            monthly_used=monthly_used_before, monthly_cap=monthly_budget,
        )
        return False, _ReservationDenial(
            "monthly_budget_exhausted", daily_used_before, daily_budget, monthly_used_before, monthly_budget
        )

    row.emails_sent = daily_used_before + 1
    db.commit()
    record_resend_budget(
        daily_used=daily_used_before + 1, daily_cap=daily_budget,
        monthly_used=monthly_used_before + 1, monthly_cap=monthly_budget,
    )
    return True, None


def send_email(db: Session, *, to: str, subject: str, html: str, from_address: str | None = None) -> None:
    """Sends exactly one email via Resend, after reserving a budget
    slot. Raises ResendUnavailable (and only that) on: Resend not
    configured, today's/this month's send budget exhausted, or the real
    Resend API call itself failing. See module docstring for why this
    does NOT swallow that exception the way
    huntloop.modal_resume_processing's public functions swallow
    ModalUnavailable - there is no local fallback for sending an email,
    so the caller must decide what to do.

    `from_address` defaults to RESEND_FROM_ADDRESS, falling further back
    to Resend's own sandbox sender if that's unset too (see module
    docstring - a real deployment must set RESEND_FROM_ADDRESS once a
    sending domain is verified, or every real send will fail as a
    send_error)."""
    if not _configured():
        record_resend_send(outcome="fallback_not_configured")
        raise ResendUnavailable("RESEND_API_KEY is not set", reason="not_configured")

    reserved, denial = _reserve_send_slot(db)
    if not reserved:
        if denial.reason == "daily_budget_exhausted":
            record_resend_send(outcome="fallback_daily_budget_exhausted")
            raise ResendUnavailable(
                f"Resend daily send budget reached ({denial.daily_used}/{denial.daily_budget} for "
                f"{_current_day_key()}) - no more emails until tomorrow (UTC)",
                reason="daily_budget_exhausted",
            )
        record_resend_send(outcome="fallback_monthly_budget_exhausted")
        raise ResendUnavailable(
            f"Resend monthly send budget reached ({denial.monthly_used}/{denial.monthly_budget} for "
            f"{_current_month_prefix()}) - no more emails until next calendar month",
            reason="monthly_budget_exhausted",
        )

    resend.api_key = os.environ["RESEND_API_KEY"]
    from_addr = from_address or os.getenv("RESEND_FROM_ADDRESS", _SANDBOX_FROM_ADDRESS)
    try:
        resend.Emails.send({
            "from": from_addr,
            "to": [to],
            "subject": subject,
            "html": html,
        })
    except Exception as e:
        record_resend_send(outcome="fallback_send_error")
        raise ResendUnavailable(
            f"Resend API call failed ({type(e).__name__}): {e}", reason="send_error"
        ) from e

    logger.info(f"Sent email via Resend (subject={subject!r}, to={to!r})")
    record_resend_send(outcome="success")

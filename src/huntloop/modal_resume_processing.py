"""
Resume parsing (pdfplumber) + embedding generation (sentence-transformers)
on Modal - Phase A of the Modal integration (see CLAUDE.md). The actual
Modal App/Image/Function definitions live in
huntloop.modal_resume_functions (deliberately a separate, minimal file -
see its own docstring for why: this module's budget-ledger/SQLAlchemy/
dotenv imports must never end up inside the remote container's import
path). This module wraps calling that already-deployed app, with a local
in-process fallback - huntloop.resume_ingestion.extract_text() and
huntloop.embeddings.embed_text() themselves are never reimplemented here
either, only called (by huntloop.modal_resume_functions remotely, or by
this module's own fallback locally).

Public surface, used by huntloop.api.routers.resumes:

    process_resume(db, pdf_path) -> ResumeProcessingResult
    embed_resume_text(db, text) -> (embedding, used_modal)

Both try Modal first and fall back to running the exact same functions
in-process (the original, pre-Modal behavior) whenever Modal isn't
configured, this month's invocation budget is exhausted, or the remote
call fails for any reason - a Modal outage, bad token, or network issue
must never break resume upload or activation. The caller never needs to
know which path actually ran, only that it got a usable result. Every
fallback is logged at WARNING with the specific reason (not configured /
budget exhausted / remote error).

The one exception to "these never raise": if Modal is unusable AND the
local fallback's own lazy `huntloop.embeddings` import fails (no
sentence-transformers/torch in this environment - true of the lean demo
image and of this project's local dev venv), there is nothing left to
fall back to and LocalProcessingUnavailable is raised, which
huntloop.api.routers.resumes converts into a structured 503 rather than
letting a bare ImportError become a bodyless 500. See that class's own
docstring.

Auth: MODAL_TOKEN_ID + MODAL_TOKEN_SECRET env vars, the same two-part
token Modal's own `modal token new` / account settings page issues and
the documented way to authenticate a non-interactive process without
running `modal setup` - stored the same way GROQ_API_KEY/GEMINI_API_KEY/
TAVILY_API_KEY are (see .env.example), not a new pattern. Both must be
set for _modal_configured() to consider Modal usable; either missing is
treated as "not configured" and falls back immediately, no partial-config
attempt.

Deployment: `modal deploy src/huntloop/modal_resume_functions.py` (run
once, and again after any change to that file - NOT this one) before
process_resume()/embed_resume_text() can actually reach Modal (the
lookup below resolves against an already-deployed app by name, not an
ephemeral one started per-call - deliberate, since this project's
resume-upload path is a request handler in a long-running API process,
not a one-off `modal run` script). Until deployed (or whenever Modal is
unreachable), process_resume()/embed_resume_text() simply fall back to
the local path every time - a missing deployment is not a crash.

Budget ledger: modal_usage (one row per calendar month) - modeled
directly on tavily_usage/TavilyUsage (see huntloop.company_research /
scripts/backfill_company_research.py / CLAUDE.md's Tavily write-up), but
counting INVOCATIONS, not a provider-reported credit figure: Modal's
response has no per-call cost field to reconcile against (same situation
Tavily's own ledger is in), and resume parsing+embedding is a short,
bounded operation where "one invocation" is a reasonable, simple cost
proxy. The default budget (see DEFAULT_MONTHLY_INVOCATION_BUDGET below)
is sized against Modal's real Starter-plan free tier - confirmed directly
against https://modal.com/pricing (not assumed): $30/month in free
compute credits, reset monthly, no rollover.

Locking: unlike the Tavily ledger (read/incremented only by a single
serial backfill script run, so a bare read-then-write race was never a
real risk - see CLAUDE.md's own note on this), this ledger is read and
incremented from live, potentially-concurrent HTTP requests (POST
/resumes/upload and PATCH /resumes/{id}/activate can genuinely overlap).
A bare read-then-increment here really could double-spend under
concurrency, so _reserve_invocation_slot() takes a `SELECT ... FOR
UPDATE` row lock on the current month's row for the short
check-then-increment, committing (and releasing the lock) immediately -
a small, cheap addition over the Tavily pattern (one extra `.with_for_update()`
on a query that already has to run), not a new subsystem, so there's no
real reason to skip it here even though the Tavily reference pattern
doesn't have it.

Metrics: every invocation (success or fallback) and every budget-ledger
check is also recorded to Prometheus via huntloop.modal_metrics - Phase C
of the Modal integration, the exact same CollectorRegistry/Pushgateway
pattern huntloop.feedback_metrics already established. See that module's
docstring for the metric names and the Grafana dashboard that renders them.

Budget-threshold email alerts: every successful reservation also calls
huntloop.budget_alerts.check_and_alert_budget_threshold() (Resend Phase
B - see CLAUDE.md/SESSIONS.md), which emails the project owner the first
time this month's usage crosses 80%/95% of MODAL_MONTHLY_INVOCATION_BUDGET.
That check can never raise or slow down this function's own real work
beyond its own synchronous DB/Resend call - see that module's own
docstring for why it isn't backgrounded the way the feedback-submission
alert is.
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import modal
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from huntloop.budget_alerts import check_and_alert_budget_threshold
from huntloop.db_models import ModalUsage
from huntloop.modal_metrics import record_modal_budget, record_modal_invocation
from huntloop.modal_resume_functions import EMBED_TEXT_FUNCTION, MODAL_APP_NAME, PROCESS_RESUME_FUNCTION

load_dotenv()

logger = logging.getLogger(__name__)

# See module docstring - sized against Modal's real $30/month Starter
# free-tier credit (confirmed against modal.com/pricing), not guessed.
# Resume parsing+embedding is a short CPU job (no GPU needed - the
# embedding model is CPU-only, see huntloop.embeddings), so a single
# invocation costs a small fraction of a cent; 500/month leaves generous
# headroom under the free credit while still being a meaningful cap, and
# is a plain env var so it can be tuned without a code change once real
# per-invocation cost is observed.
DEFAULT_MONTHLY_INVOCATION_BUDGET = 500


class ModalUnavailable(Exception):
    """Raised internally whenever Modal can't be used for this call -
    not configured, this month's invocation budget exhausted, or the
    remote call itself failing for any reason. Always caught inside this
    module's own process_resume()/embed_resume_text(); never escapes to
    huntloop.api.routers.resumes.

    `reason` is a short machine-readable tag (not_configured /
    lookup_failed / budget_exhausted / invocation_error) set at each raise
    site below - used only to label the fallback metric in
    huntloop.modal_metrics so a Grafana panel can tell "Modal isn't set up
    here" apart from "the budget is spent" apart from "Modal itself
    errored", rather than lumping every fallback into one bucket."""

    def __init__(self, message: str, reason: str = "error"):
        super().__init__(message)
        self.reason = reason


class LocalProcessingUnavailable(Exception):
    """Raised when Modal was unusable AND the local in-process fallback
    cannot run either, because huntloop.embeddings (and therefore
    sentence-transformers/torch) can't be imported in this environment.

    This is the ONE case where process_resume()/embed_resume_text() do
    raise rather than returning a usable result - there is genuinely
    nothing left to fall back to, and the alternative is worse: before
    this class existed, the lazy `from huntloop.embeddings import
    embed_text` inside the fallback paths below raised a bare
    ImportError straight out of the route handler, which Starlette
    turned into a bodyless 500 "Internal Server Error". That is exactly
    the shape the ATS-report endpoint already learned to avoid (see
    huntloop.api.routers.resumes.get_active_ats_report's 503 branch): a
    deployment that simply isn't configured to do this work is a
    deployment fact, not a server fault, and deserves a structured 503
    the frontend can render.

    huntloop.api.routers.resumes catches this in both upload_resume()
    and activate_resume() and converts it to a 503. Note this is a real
    possibility, not a hypothetical: the lean demo image has no torch at
    all, and this project's own local dev venv can't install it either
    (see huntloop.embeddings' docstring)."""


def _modal_configured() -> bool:
    return bool(os.getenv("MODAL_TOKEN_ID") and os.getenv("MODAL_TOKEN_SECRET"))


def _monthly_budget() -> int:
    return int(os.getenv("MODAL_MONTHLY_INVOCATION_BUDGET", str(DEFAULT_MONTHLY_INVOCATION_BUDGET)))


def _current_month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _reserve_invocation_slot(db: Session, weight: int = 1) -> tuple[bool, int, int]:
    """Atomically checks this month's invocation budget and, if there's
    room, reserves `weight` slots by incrementing the counter
    immediately - see module docstring's Locking section for why this
    needs a row lock (unlike the Tavily reference pattern) and why the
    slot is reserved before the remote call rather than only after a
    successful one (a Modal invocation that errors partway through still
    consumes real compute time, so it still counts). Returns
    (reserved, used_before_this_call, budget) for logging; never raises
    on a normal "budget exhausted" outcome - that's communicated via the
    returned bool, not an exception (ModalUnavailable is raised by the
    caller, which has the context to log a clear message).

    `weight` defaults to 1, which is byte-for-byte the original
    behaviour for every pre-existing caller (resume processing and
    embedding): `used_before + 1 > budget` is exactly the old
    `used_before >= budget` check. A heavier function reserves more than
    one slot - huntloop.modal_deep_dive passes MODAL_DEEP_DIVE_WEIGHT
    (default 5), since a Deep-Dive runs a multi-provider LLM chain with
    a 10-minute timeout rather than a seconds-long CPU job, and the
    ledger counts invocations as a cost proxy (Modal reports no per-call
    cost to reconcile against - see the module docstring). The weight is
    an approximation of relative credit use, not a measured figure.

    A reservation that WOULD cross the cap is refused outright rather
    than partially granted or allowed to overshoot - so a weight-5
    request with 3 slots left is refused, leaving those 3 for a
    weight-1 caller. Budget-threshold alerts stay correct under
    weighting: huntloop.budget_alerts fires on the percentage `used/cap`
    reaches and records the highest threshold already alerted on, so a
    weighted jump that crosses 80% and 95% in one step sends both emails
    once each, and never re-fires for a threshold already passed."""
    if weight < 1:
        weight = 1
    month = _current_month_key()
    row = db.query(ModalUsage).filter_by(month=month).first()
    if row is None:
        row = ModalUsage(month=month, invocations_used=0)
        db.add(row)
        db.commit()
    # Re-select WITH the lock now that the row definitely exists - held
    # only across this one check-then-increment, not the whole request.
    row = db.query(ModalUsage).filter_by(month=month).with_for_update().first()
    budget = _monthly_budget()
    used_before = row.invocations_used
    if used_before + weight > budget:
        db.commit()  # releases the row lock; nothing incremented
        record_modal_budget(used=used_before, cap=budget)
        return False, used_before, budget
    row.invocations_used = used_before + weight
    db.commit()
    record_modal_budget(used=used_before + weight, cap=budget)
    # Belt-and-suspenders on top of check_and_alert_budget_threshold's
    # own internal try/except: the real increment just above is already
    # committed, so nothing past this point may ever propagate and take
    # that real result down with it, even if the alert-check call itself
    # fails in some way its own internals can't catch.
    try:
        check_and_alert_budget_threshold(db, ledger="modal", period=month, used=used_before + weight, cap=budget)
    except Exception:
        logger.exception("Budget-threshold alert check raised unexpectedly for ledger=modal - invocation still succeeded")
    return True, used_before, budget


def _prepare_invocation(db: Session, function_name: str, weight: int = 1):
    """Shared setup for both the sync and async dispatch paths below:
    confirms Modal is configured, looks up the named already-deployed
    Modal function, and reserves one invocation against this month's
    budget. Raises ModalUnavailable (and only that) on any failure.

    Looking up the function happens BEFORE the budget is touched,
    deliberately: a lookup failure (the app hasn't been `modal deploy`ed
    yet, a bad/expired token, no network to Modal at all) means nothing
    was ever dispatched to Modal to run - no compute was spent, so it
    must not count against the invocation budget. The budget is only
    reserved once we have a real handle to call - that's the point a
    real invocation (and its cost) begins, even if it then fails partway
    through the actual dispatch, which happens in the caller."""
    if not _modal_configured():
        raise ModalUnavailable(
            "MODAL_TOKEN_ID/MODAL_TOKEN_SECRET are not both set", reason="not_configured"
        )

    try:
        fn = modal.Function.from_name(MODAL_APP_NAME, function_name)
        # from_name() is LAZY - it builds a handle without contacting
        # Modal at all, so on its own it raises for nothing: a bad or
        # expired token, or an app that was never deployed, both sail
        # through here and only blow up later at the actual .remote()/
        # .spawn(), which the caller charges as a real invocation.
        # Found live (2026-11-11, see SESSIONS.md): a deliberately fake
        # MODAL_TOKEN_ID still reserved the full Deep-Dive weight before
        # failing. hydrate() forces the lookup to happen HERE, which is
        # what makes the "a lookup failure is never charged" contract
        # above actually hold.
        fn.hydrate()
    except Exception as e:
        raise ModalUnavailable(
            f"Could not look up Modal function {function_name!r} - not deployed yet, or a "
            f"connectivity/auth problem ({type(e).__name__}): {e}",
            reason="lookup_failed",
        ) from e

    reserved, used_before, budget = _reserve_invocation_slot(db, weight=weight)
    if not reserved:
        raise ModalUnavailable(
            f"Modal monthly invocation budget reached ({used_before}/{budget} for "
            f"{_current_month_key()}, this call needs {weight}) - no new Modal invocations "
            f"until next calendar month",
            reason="budget_exhausted",
        )
    return fn


def _invoke_modal(db: Session, function_name: str, args: tuple, label: str):
    """Synchronous dispatch - used from PATCH /resumes/{id}/activate,
    which is a plain `def` route handler (FastAPI runs it in a worker
    thread, not the event loop, so a blocking call here is harmless -
    same characteristic the pre-existing in-process embed_text() call
    already had)."""
    fn = _prepare_invocation(db, function_name)
    try:
        return fn.remote(*args)
    except Exception as e:
        raise ModalUnavailable(
            f"Modal invocation of {function_name!r} for {label} failed ({type(e).__name__}): {e}",
            reason="invocation_error",
        ) from e


async def _invoke_modal_async(db: Session, function_name: str, args: tuple, label: str):
    """Async dispatch - used from POST /resumes/upload, which IS an
    `async def` route handler sharing the process's one event loop.
    Modal's own SDK warns (confirmed live, running this project's test
    suite) that calling the blocking `.remote()` from inside an async
    function stalls that event loop for every other concurrent request
    for the duration of the call - `.remote.aio()` is Modal's documented
    non-blocking equivalent for exactly this situation."""
    fn = _prepare_invocation(db, function_name)
    try:
        return await fn.remote.aio(*args)
    except Exception as e:
        raise ModalUnavailable(
            f"Modal invocation of {function_name!r} for {label} failed ({type(e).__name__}): {e}",
            reason="invocation_error",
        ) from e


# ---------------------------------------------------------------------
# Public API - called from huntloop.api.routers.resumes
# ---------------------------------------------------------------------


@dataclass
class ResumeProcessingResult:
    extracted_text: str
    embedding: list[float] | None
    used_modal: bool


async def process_resume(db: Session, pdf_path: str) -> ResumeProcessingResult:
    """Extraction + embedding for a newly-uploaded resume PDF (POST
    /resumes/upload - an `async def` route, hence this is async too; see
    _invoke_modal_async). Tries Modal first - one remote invocation does
    both steps, mirroring the original in-process call order - and falls
    back to running extract_text()/embed_text() locally, unchanged, on
    any failure. Raises only LocalProcessingUnavailable, and only when
    that local fallback cannot run either - see that class's
    docstring."""
    try:
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        result = await _invoke_modal_async(db, PROCESS_RESUME_FUNCTION, (pdf_bytes,), "resume processing")
        logger.info(f"Processed resume via Modal ({len(result['extracted_text'])} chars extracted)")
        record_modal_invocation(function=PROCESS_RESUME_FUNCTION, outcome="success")
        return ResumeProcessingResult(
            extracted_text=result["extracted_text"],
            embedding=result["embedding"],
            used_modal=True,
        )
    except ModalUnavailable as e:
        logger.warning(f"Modal unavailable for resume processing, falling back to the local path: {e}")
        record_modal_invocation(function=PROCESS_RESUME_FUNCTION, outcome=f"fallback_{e.reason}")
        return _process_resume_locally(pdf_path)


def _process_resume_locally(pdf_path: str) -> ResumeProcessingResult:
    """The original, pre-Modal in-process path - extract_text()/
    embed_text() unchanged, run inline. Used both as the fallback inside
    process_resume() and directly wherever Modal shouldn't be tried at
    all."""
    from huntloop.resume_ingestion import extract_text

    extracted_text = extract_text(pdf_path)
    embedding = None
    if extracted_text.strip():
        embedding = _local_embed_text(extracted_text)
    return ResumeProcessingResult(extracted_text=extracted_text, embedding=embedding, used_modal=False)


def _local_embed_text(text: str) -> list[float]:
    """The lazy `huntloop.embeddings` import, in one place, with its
    import failure turned into a typed LocalProcessingUnavailable
    instead of a bare ImportError escaping the route handler as a
    bodyless 500 (see that class's docstring).

    Imported lazily, not at module level, for the long-standing reason
    documented in huntloop.embeddings and huntloop.api.routers.resumes:
    huntloop.api.main imports every router together, so a module-level
    torch-dependent import here would break importing the WHOLE API in
    any environment without torch, not just these two endpoints.

    Only the import itself is translated. A failure inside embed_text()
    once it HAS imported (e.g. an offline HuggingFace model load) is a
    different, genuinely unexpected condition and is left to propagate
    as before - not silently relabelled as a configuration problem."""
    try:
        from huntloop.embeddings import embed_text
    except ImportError as exc:
        raise LocalProcessingUnavailable(
            "Modal was unavailable and the local embedding fallback cannot run here either - "
            f"huntloop.embeddings could not be imported ({type(exc).__name__}: {exc}). This "
            "deployment has no sentence-transformers/torch install."
        ) from exc

    return embed_text(text)


def embed_resume_text(db: Session, text: str) -> tuple[list[float], bool]:
    """Embedding-only path for PATCH /resumes/{id}/activate's
    backfill-missing-embedding branch. Tries Modal first, falls back to
    the local embed_text() on any failure. Returns (embedding,
    used_modal). Raises only LocalProcessingUnavailable, and only when
    that local fallback cannot run either - see that class's
    docstring."""
    try:
        embedding = _invoke_modal(db, EMBED_TEXT_FUNCTION, (text,), "resume embedding")
        logger.info("Computed resume embedding via Modal")
        record_modal_invocation(function=EMBED_TEXT_FUNCTION, outcome="success")
        return embedding, True
    except ModalUnavailable as e:
        logger.warning(f"Modal unavailable for resume embedding, falling back to the local path: {e}")
        record_modal_invocation(function=EMBED_TEXT_FUNCTION, outcome=f"fallback_{e.reason}")
        return _local_embed_text(text), False

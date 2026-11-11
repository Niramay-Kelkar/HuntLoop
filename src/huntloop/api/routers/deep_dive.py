"""
POST /jobs/{job_id}/deep-dive and GET /deep-dives/{deep_dive_id} - the
Resume Deep-Dive: a heavier skill-gap analysis of the active resume
against one job posting, too slow to run inside a single synchronous
request.

This is the FIRST spawn-and-poll flow in this codebase. Every other
endpoint here either answers from the database or makes a blocking
call and returns the answer; there was no existing poll-by-id endpoint
to copy a convention from, so the shape is set here:

    POST /jobs/{job_id}/deep-dive   -> 202 {id, status: "running", ...}
    GET  /deep-dives/{id}           -> 200, status running | done | failed

The division of labour across the three new modules:
  * huntloop.deep_dive         - the prompt, the provider chain, and
                                 result validation. Runs inside Modal.
  * huntloop.modal_deep_dive   - spawn + non-blocking reconcile; owns
                                 every Modal client call.
  * this module                - the resume_deep_dives row lifecycle,
                                 HTTP status codes, and error codes.

MOUNTED IN DEMO MODE TOO (see huntloop.api.main). Unlike
resumes.unsafe_router (file uploads) and drafting.router (a
caller-supplied LLM key), this endpoint takes no upload and no
credential: the LLM keys live only in Modal's `huntloop-llm` Secret, so
there is nothing here that is unsafe to expose on the public demo. If
the demo's own Modal tokens aren't set, every POST is simply a clean
503 with code modal_unavailable.

NO LOCAL FALLBACK, in any environment - see huntloop.modal_deep_dive's
docstring. The API process has no LLM keys by design, so "Modal is
unavailable" genuinely means "this cannot be done here", and says so
rather than degrading silently.

Error bodies are {"detail": "<human message>", "code": "<machine
code>"} - the `detail` string stays exactly where the existing
ATS-report 503 puts it (so frontend/src/lib/api.ts's established
reader works unchanged), with `code` added alongside for the cases the
UI must word differently. Codes: job_not_found, no_active_resume,
deep_dive_not_found, modal_unavailable, budget_exhausted, rate_limited.

Routes are plain `def`, not `async def`, on purpose: both the Modal
client calls (.spawn(), FunctionCall.get()) and the SELECT ... FOR
UPDATE are blocking, and FastAPI runs a sync route in a worker thread
rather than on the event loop - the same reasoning
huntloop.modal_resume_processing._invoke_modal already documents for
PATCH /resumes/{id}/activate. An `async def` here would stall every
other concurrent request for the duration of the Modal round trip.

Logging discipline: ids only. Never resume text, never job description
text, never a provider response, never a token.
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, null, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse

from huntloop.api.dependencies import get_db
from huntloop.api.routers.drafting import _RateLimiter
from huntloop.api.schemas.deep_dive import DeepDiveResponse
from huntloop.db_models import (
    Company,
    DeepDiveStatus,
    JobPosting,
    ResumeDeepDive,
    ResumeSkillMatch,
    ResumeVersion,
)
from huntloop.modal_deep_dive import reconcile_deep_dive, spawn_deep_dive, stale_minutes
from huntloop.modal_metrics import record_modal_invocation
from huntloop.modal_resume_functions import DEEP_DIVE_FUNCTION
from huntloop.modal_resume_processing import ModalUnavailable

logger = logging.getLogger(__name__)

router = APIRouter(tags=["deep-dive"])

# Starting a Deep-Dive spends real Modal credit from a shared monthly
# ledger, so this is bounded per client - same in-process sliding-window
# limiter huntloop.api.routers.drafting built and resumes.py already
# reuses, not a new mechanism. Same documented limitation: per-process
# memory, resets on restart, not shared across workers. GET is NOT
# limited here beyond any app-wide demo limit - polling is the
# documented way to use this endpoint and costs only a row read (plus,
# at most once, a non-blocking Modal poll).
RATE_LIMIT_MAX_REQUESTS = 5
RATE_LIMIT_WINDOW_SECONDS = 60.0

_rate_limiter = _RateLimiter(RATE_LIMIT_MAX_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)

_TERMINAL_STATUSES = (DeepDiveStatus.DONE, DeepDiveStatus.FAILED)


class DeepDiveError(Exception):
    """Carries an HTTP status, a human message and a machine code to
    huntloop.api.main's registered handler, which renders them as
    {"detail": ..., "code": ...}.

    A plain HTTPException can't express this: FastAPI renders its
    `detail` as the whole body, so a top-level sibling `code` field is
    not expressible without either nesting (`{"detail": {"detail":
    ..., "code": ...}}`, which would break the existing frontend
    reader that expects `detail` to be a string) or returning a raw
    JSONResponse from every error branch (which would mean no helper
    could raise)."""

    def __init__(self, status_code: int, detail: str, code: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code


async def deep_dive_error_handler(request: StarletteRequest, exc: Exception) -> JSONResponse:
    """Registered on the app in huntloop.api.main.create_app()."""
    assert isinstance(exc, DeepDiveError)
    return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status_code)


def _enforce_rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    if not _rate_limiter.allow(client_ip):
        raise DeepDiveError(
            429,
            f"Too many Deep-Dive runs started from this client ({RATE_LIMIT_MAX_REQUESTS} per "
            f"{int(RATE_LIMIT_WINDOW_SECONDS)}s). Try again shortly.",
            "rate_limited",
        )


def _to_response(row: ResumeDeepDive) -> DeepDiveResponse:
    status = row.status.value if isinstance(row.status, DeepDiveStatus) else str(row.status)
    return DeepDiveResponse(
        id=row.id,
        job_posting_id=row.job_posting_id,
        resume_version_id=row.resume_version_id,
        status=status,
        # Only ever surfaced for the status it belongs to, so a stale
        # `error` left on a row that has since been re-dispatched can't
        # leak into a running/done response.
        result=row.result if status == DeepDiveStatus.DONE.value else None,
        error=row.error if status == DeepDiveStatus.FAILED.value else None,
        dispatched_at=row.dispatched_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _is_stale(db: Session, row: ResumeDeepDive) -> bool:
    """True once a pending/running row has sat longer than
    DEEP_DIVE_STALE_MINUTES, measured from dispatched_at (or created_at
    for a row that never got as far as being dispatched). Guards the
    real case where a spawn succeeded but the call will never resolve -
    without this, such a row would return 202 forever and the user could
    never retry.

    "NOW" COMES FROM THE DATABASE, not from Python, and that is not
    incidental. Every timestamp on this table is a naive DateTime filled
    by `func.now()` (server_default / onupdate / _dispatch below), so it
    is stored in whatever timezone the Postgres session is configured
    for - NOT necessarily UTC. On the machine this was developed on it
    is America/New_York, five hours off UTC, which was found by actually
    running the endpoint and reading the row back rather than by
    inspection: comparing these timestamps against a Python
    `datetime.now(timezone.utc)` made a row created seconds earlier look
    five hours old, i.e. instantly "stale", which would have
    re-dispatched every single POST and spent the Modal budget over and
    over. Asking Postgres for its own clock makes the comparison correct
    on any server timezone, including UTC, instead of only on a server
    that happens to be set to UTC.
    """
    reference = row.dispatched_at or row.created_at
    if reference is None:
        return True
    now = db.execute(select(func.now())).scalar()
    if now is None:  # pragma: no cover - defensive
        now = datetime.now(timezone.utc)
    # func.now() is timestamptz; the stored columns are naive. Drop the
    # offset so both sides are on the same (session-local) wall clock.
    if now.tzinfo is not None and reference.tzinfo is None:
        now = now.replace(tzinfo=None)
    return (now - reference) > timedelta(minutes=stale_minutes())


def _lookup_job(db: Session, job_id: int) -> tuple[JobPosting, Company | None]:
    row = db.execute(
        select(JobPosting, Company)
        .outerjoin(Company, JobPosting.company_id == Company.id)
        .where(JobPosting.id == job_id)
    ).first()
    if row is None:
        raise DeepDiveError(404, f"No job posting with id={job_id}.", "job_not_found")
    return row.JobPosting, row.Company


def _active_resume(db: Session) -> ResumeVersion:
    """Same "resolve the active resume fresh, per request" pattern as
    huntloop.api.routers.jobs._active_resume() and
    drafting._lookup_job_and_active_resume() - never cached."""
    resume = db.query(ResumeVersion).filter_by(is_active=True).first()
    if resume is None:
        raise DeepDiveError(
            400,
            "No active resume is on file - upload one before running a Deep-Dive.",
            "no_active_resume",
        )
    return resume


def _skills_for(db: Session, job_id: int, resume_version_id: int) -> tuple[list, list]:
    """The already-computed matched/missing split for this exact
    (job, resume version) pair, from resume_skill_matches. Absent for
    most postings (the skills-matching backfill works through the
    backlog over days), which is fine - the Deep-Dive prompt treats
    them as optional context, not a prerequisite."""
    match = (
        db.query(ResumeSkillMatch)
        .filter_by(job_posting_id=job_id, resume_version_id=resume_version_id)
        .first()
    )
    if match is None:
        return [], []
    return list(match.matched_skills or []), list(match.missing_skills or [])


def _dispatch(db: Session, row: ResumeDeepDive, job: JobPosting, company: Company | None, resume: ResumeVersion) -> None:
    """Reserve budget, spawn the Modal call, and move the row to
    `running`. On a ModalUnavailable the row is marked `failed` (not
    left `pending`) and committed before the 503 is raised - otherwise a
    row stuck at `pending` would look "not stale yet" to the next POST,
    which would return 202 for a run that was never actually
    dispatched and can never finish."""
    matched, missing = _skills_for(db, job.id, resume.id)
    payload = {
        "resume_text": resume.extracted_text or "",
        "job_title": job.job_title or "",
        "company_name": (company.name if company else "") or "",
        "job_description": job.job_description or "",
        "matched_skills": matched,
        "missing_skills": missing,
    }

    try:
        call_id = spawn_deep_dive(db, payload)
    except ModalUnavailable as e:
        row.status = DeepDiveStatus.FAILED
        row.modal_call_id = None
        # sqlalchemy.null(), never a bare Python None - on this JSON
        # column binding None stores the JSON scalar `null` (so
        # `result IS NULL` is false and `result::text` is the string
        # 'null'), while null() stores a real SQL NULL. This exact
        # distinction cost this project a real bug once already, on
        # job_postings.matched_skills - see CLAUDE.md's write-up of the
        # resume-activation reset. Applies at every point this column is
        # cleared, below too.
        row.result = null()
        row.error = (
            "The analysis service is not available right now, so this run was not started."
        )
        db.commit()
        record_modal_invocation(function=DEEP_DIVE_FUNCTION, outcome=f"fallback_{e.reason}")
        logger.warning(
            "Deep-Dive dispatch refused for deep_dive_id=%s job_posting_id=%s (reason=%s): %s",
            row.id,
            job.id,
            e.reason,
            e,
        )
        if e.reason == "budget_exhausted":
            raise DeepDiveError(
                503,
                "This month's analysis budget has been used up. Deep-Dives will be available "
                "again at the start of next month.",
                "budget_exhausted",
            ) from e
        raise DeepDiveError(
            503,
            "The Deep-Dive analysis service is not available on this deployment right now. "
            "Please try again later.",
            "modal_unavailable",
        ) from e

    row.status = DeepDiveStatus.RUNNING
    row.modal_call_id = call_id
    # Both cleared so a re-dispatch of a previously failed/stale row
    # can't surface the old run's result or error alongside the new
    # run's `running` status. null(), not None - see above.
    row.result = null()
    row.error = None
    # func.now(), not a Python timestamp: created_at/updated_at on this
    # table are filled by the DATABASE's clock (server_default/onupdate),
    # and this column has to be on that same clock or the two can't be
    # compared or subtracted. They genuinely weren't at first - see
    # _is_stale's note on the five-hour skew that caused, and the
    # Grafana panel that computes `updated_at - dispatched_at` as a
    # completion time (which read as NEGATIVE five hours until this
    # changed).
    row.dispatched_at = func.now()
    db.commit()
    db.refresh(row)
    record_modal_invocation(function=DEEP_DIVE_FUNCTION, outcome="spawned")
    logger.info(
        "Deep-Dive dispatched: deep_dive_id=%s job_posting_id=%s resume_version_id=%s",
        row.id,
        job.id,
        resume.id,
    )


@router.post("/jobs/{job_id}/deep-dive", response_model=DeepDiveResponse, status_code=202)
def start_deep_dive(
    job_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> DeepDiveResponse | JSONResponse:
    """Start (or return) the Deep-Dive for this job against the active
    resume.

    One row per (resume_version, job_posting) - the unique constraint on
    huntloop.db_models.ResumeDeepDive - so this is idempotent per pair
    rather than queueing a second run:

      * done                      -> 200 with the full record
      * pending/running, fresh    -> 202 with that record, NO re-dispatch
      * pending/running, stale    -> re-dispatch (see _is_stale)
      * failed                    -> re-dispatch
      * no row at all             -> create one, then dispatch
    """
    _enforce_rate_limit(request)
    job, company = _lookup_job(db, job_id)
    resume = _active_resume(db)

    row = (
        db.query(ResumeDeepDive)
        .filter_by(job_posting_id=job_id, resume_version_id=resume.id)
        .first()
    )

    if row is not None:
        if row.status == DeepDiveStatus.DONE and row.result:
            # A finished analysis is returned as a plain 200, not this
            # route's declared 202 - hence the explicit JSONResponse
            # (FastAPI applies the decorator's status_code to anything
            # that isn't already a Response).
            return JSONResponse(
                _to_response(row).model_dump(mode="json"),
                status_code=200,
            )
        if row.status in (DeepDiveStatus.PENDING, DeepDiveStatus.RUNNING) and not _is_stale(db, row):
            return _to_response(row)
        logger.info(
            "Re-dispatching Deep-Dive deep_dive_id=%s (previous status=%s)",
            row.id,
            row.status,
        )
    else:
        row = ResumeDeepDive(
            job_posting_id=job_id,
            resume_version_id=resume.id,
            status=DeepDiveStatus.PENDING,
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            # Lost the insert race with a concurrent POST for the same
            # pair (the unique constraint is the real backstop across
            # workers, which no in-process guard can cover). The winner
            # has already dispatched, so serve their row rather than
            # dispatching a second time and double-charging the ledger.
            db.rollback()
            existing = (
                db.query(ResumeDeepDive)
                .filter_by(job_posting_id=job_id, resume_version_id=resume.id)
                .first()
            )
            if existing is None:
                raise DeepDiveError(
                    503,
                    "Could not start a Deep-Dive right now. Please try again.",
                    "modal_unavailable",
                )
            return _to_response(existing)
        db.refresh(row)

    _dispatch(db, row, job, company, resume)
    return _to_response(row)


@router.get("/deep-dives/{deep_dive_id}", response_model=DeepDiveResponse)
def get_deep_dive(deep_dive_id: int, db: Session = Depends(get_db)) -> DeepDiveResponse:
    """The polling endpoint. Returns the record as-is, except that a
    `running` row with a Modal call id is first reconciled against
    Modal - a non-blocking `get(timeout=0)` that either finds the
    finished result (copied in here and the row set `done`), finds a
    real failure (row set `failed` with a short sanitized reason), or
    reports the call still unfinished (row untouched).

    Reconciliation holds a SELECT ... FOR UPDATE row lock for its whole
    read-check-write. The frontend polls every few seconds and several
    tabs/visitors can poll the same row at once, so without the lock two
    concurrent polls could both see `running`, both poll Modal, and both
    write - double-charging nothing (the poll is free) but racing each
    other's writes. With it, the loser re-reads a terminal row and
    simply returns it."""
    row = db.get(ResumeDeepDive, deep_dive_id)
    if row is None:
        raise DeepDiveError(404, f"No Deep-Dive with id={deep_dive_id}.", "deep_dive_not_found")

    if row.status != DeepDiveStatus.RUNNING or not row.modal_call_id:
        return _to_response(row)

    locked = (
        db.query(ResumeDeepDive)
        .filter_by(id=deep_dive_id)
        .with_for_update()
        .first()
    )
    if locked is None:
        raise DeepDiveError(404, f"No Deep-Dive with id={deep_dive_id}.", "deep_dive_not_found")
    if locked.status in _TERMINAL_STATUSES or not locked.modal_call_id:
        # Another poll won the race and already finished the row while
        # this request waited for the lock.
        db.commit()
        return _to_response(locked)

    status, result, error = reconcile_deep_dive(locked.modal_call_id)

    if status == "running":
        db.commit()  # releases the lock; nothing changed
        return _to_response(locked)

    if status == "done":
        locked.status = DeepDiveStatus.DONE
        locked.result = result
        locked.error = None
        record_modal_invocation(function=DEEP_DIVE_FUNCTION, outcome="success")
        logger.info("Deep-Dive completed: deep_dive_id=%s", locked.id)
    else:
        locked.status = DeepDiveStatus.FAILED
        locked.result = null()
        locked.error = error
        record_modal_invocation(function=DEEP_DIVE_FUNCTION, outcome="failed")
        logger.warning("Deep-Dive failed: deep_dive_id=%s", locked.id)

    db.commit()
    db.refresh(locked)
    return _to_response(locked)

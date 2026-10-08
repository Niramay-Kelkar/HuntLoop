"""
GET /resumes, POST /resumes/upload, PATCH /resumes/{id}/activate -
resume-version management, layered on top of the existing
resume_versions table and Phase 3's extraction/embedding logic
(huntloop.resume_ingestion.extract_text(), huntloop.embeddings.embed_text())
- both reused unchanged, not reimplemented.

As of the Modal integration (see CLAUDE.md), extraction+embedding
(upload) and embedding alone (activate's backfill-missing-embedding
branch) are no longer called directly here - both routes go through
huntloop.modal_resume_processing.process_resume()/embed_resume_text(),
which try a Modal remote function first and fall back to running
extract_text()/embed_text() locally (the original, pre-Modal behavior,
unchanged) on any failure - not configured, monthly invocation budget
exhausted, or the remote call erroring. Neither function raises, so
these two endpoints don't need their own fallback logic - see that
module's docstring for the full design (budget ledger, locking,
deployment).

huntloop.modal_resume_processing itself imports `modal` at module level
but defers huntloop.embeddings/pdfplumber-dependent work (the fallback
path, and everything inside the remote function bodies) to lazy imports
- so importing it here does NOT require a working torch install, same
constraint as before (this project's local dev venv can't run torch -
see huntloop.embeddings' own docstring). huntloop.api.main imports every
router together, so a module-level import of anything torch-dependent
here would still break importing the whole API on this machine - that's
why the actual extract_text()/embed_text() calls stay inside lazily-
imported fallback code paths, not why the module import itself is
guarded. Real, end-to-end verification of the embedding-dependent local
fallback path runs inside the app/api Docker image, same convention
scripts/backfill_embeddings.py established (see CLAUDE.md/SESSIONS.md);
the real Modal path is verified against a real Modal deployment+token
(see CLAUDE.md's Modal integration write-up).

The GET /jobs match-score query (huntloop.api.routers.jobs) already
resolves "the active resume" dynamically at query time - a plain
`SELECT ... WHERE is_active = true` issued fresh on every request (see
_active_resume() there), not a cached/hardcoded resume id -
confirmed by reading that code again as part of this step, not assumed.
That's precisely what makes activating a different version here actually
change every job's live match_score on the very next /jobs request, with
no change needed to the jobs router itself.

SECURITY: both endpoints below are unauthenticated (same as every other
endpoint in this API today).

Historical note, since the reasoning below predates it: this docstring
originally described a full-table UPDATE across job_postings.
matched_skills/missing_skills on every upload/activate call (a client
could repeatedly re-upload the same file and force the whole ~97k-row
table back into the days-long Groq/Gemini re-matching queue on every
single call - a trivial unauthenticated DoS). That vector is gone by
construction now that skill-match results live in resume_skill_matches,
keyed by (job_posting_id, resume_version_id) - see huntloop.db_models.
ResumeSkillMatch: activating or uploading a resume never writes to or
deletes from that table at all. A new resume_versions row simply starts
with zero resume_skill_matches rows for its own id (nothing to reset),
and every other version's rows - including the one that was just
deactivated - are left completely alone. Re-uploading or reactivating
repeatedly can still cost real resources (Modal/embedding compute per
call, and a fresh multi-day backfill cycle for each genuinely-new
version id), so the mitigations below are kept for that reason, not for
the (no longer possible) data-wipe:

1. Content-hash check (_content_hash / _active_resume_content_hash) -
   still used by both endpoints below purely for logging/no-op detection
   (a byte-identical re-upload, or reactivating a version whose
   extracted text already matches what's currently active, is logged as
   a content-unchanged no-op) - hashing the *extracted text*, not the
   raw PDF bytes, since two different PDF files that extract to
   identical text represent the same resume content either way.
2. Per-IP rate limiting on both endpoints, reusing the exact same
   _RateLimiter class huntloop.api.routers.drafting already built and
   validated for exactly this problem (an unauthenticated endpoint that
   needs bounding) - not reimplemented here. Same documented limitation
   as that endpoint: per-process memory, resets on restart, not shared
   across multiple workers - fine for today's single-process deployment.
3. A hard upload-size cap (MAX_UPLOAD_BYTES), enforced by
   MaxUploadSizeMiddleware at the ASGI layer - BEFORE FastAPI's own
   multipart form parsing ever buffers the body - not merely checked
   against the client-supplied (and spoofable/optional) Content-Length
   header. See MaxUploadSizeMiddleware's own docstring below for why
   this has to live at that layer rather than inside upload_resume()
   itself: by the time `file: UploadFile` is available to a FastAPI
   route function, Starlette has already fully consumed and buffered the
   request body to build it - a check inside the handler would always be
   too late for a large-body attack.
"""
import asyncio
import hashlib
import logging
import threading

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from huntloop.api.dependencies import get_db
from huntloop.api.routers.drafting import _RateLimiter
from huntloop.api.schemas.resume_ats_report import AtsReportResponse
from huntloop.api.schemas.resumes import ResumeVersionSummary
from huntloop.db_models import ResumeAtsReport, ResumeVersion
from huntloop.modal_resume_processing import embed_resume_text, process_resume
from huntloop.resume_ingestion import save_uploaded_pdf
from huntloop.text_cleaning import clean_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resumes", tags=["resumes"])

# Upload and activation are unauthenticated writes that reset the whole
# skills-matching backlog and (outside demo mode) load the embedding
# model - unsafe to expose on a public demo (see huntloop.demo_mode).
# Kept on a separate router so huntloop.api.main can simply not mount it
# when DEMO_MODE is on, rather than mounting it and guarding each
# endpoint individually.
unsafe_router = APIRouter(prefix="/resumes", tags=["resumes"])

_PREVIEW_LENGTH = 200

# A real resume PDF is almost always well under 1MB of text-and-light-
# formatting content; 15MB gives generous headroom for a scanned/
# image-heavy PDF while staying far short of anything that could pressure
# server memory/disk from a single upload. Enforced by
# MaxUploadSizeMiddleware (see below and huntloop.api.main), not here -
# this constant is imported by main.py to register that middleware.
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


class MaxUploadSizeMiddleware:
    """Raw ASGI middleware (not a Starlette BaseHTTPMiddleware, which
    buffers the whole body itself before your code sees it - defeating
    the point) that rejects a request to `path` once the cumulative size
    of `http.request` message bodies it has relayed exceeds `max_bytes`,
    WHILE the body is still streaming in - not after FastAPI's multipart
    parser has already fully consumed it. Deliberately does not trust
    Content-Length: that header is optional (chunked transfer-encoding
    omits it entirely) and, even when present, is just a client-supplied
    claim - nothing stops a client from sending a small Content-Length
    and then a much larger body. Counting actual bytes as they arrive via
    the ASGI `receive()` callable is the only way to enforce this for
    real, and it's why this has to be ASGI middleware (which sits in
    front of Starlette's own request-parsing) rather than a check inside
    the route handler (which only runs after that parsing has already
    happened).

    Runs the downstream app as a cancellable asyncio.Task instead of
    raising an exception out of `receive()` and letting it unwind through
    the app's own call stack - confirmed live, during development, that
    the latter doesn't work reliably: FastAPI's multipart form parser
    catches a broad range of exceptions raised from `receive()` itself
    (treating any of them the same as a truncated/malformed body) and
    translates them into its own generic 400 response instead of letting
    them propagate to us, which both produces the wrong status code and
    risks a double "http.response.start" if we then also tried to send
    our own response. Cancelling the app's task directly, the moment the
    cap is crossed, sidesteps that entirely - it doesn't depend on
    cooperating with whatever internal exception handling any given
    downstream body parser happens to have."""

    def __init__(self, app, path: str, max_bytes: int) -> None:
        self.app = app
        self.path = path
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["path"] != self.path:
            await self.app(scope, receive, send)
            return

        total = 0
        response_started = False
        exceeded = asyncio.Event()

        async def tracking_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        async def capped_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    exceeded.set()
                    # Never hand the over-cap chunk to the app - block
                    # here instead of returning, so the app can't race
                    # ahead and finish processing it (which, for a body
                    # delivered in one shot rather than many small
                    # chunks, could otherwise complete synchronously
                    # before the outer wait() below ever gets scheduled
                    # to notice `exceeded` and cancel it - confirmed live
                    # during development). The outer wait() cancels this
                    # task shortly after `exceeded` is set, unwinding this
                    # await with CancelledError.
                    await asyncio.Event().wait()
            return message

        app_task = asyncio.ensure_future(self.app(scope, capped_receive, tracking_send))
        exceeded_task = asyncio.ensure_future(exceeded.wait())
        try:
            done, _ = await asyncio.wait({app_task, exceeded_task}, return_when=asyncio.FIRST_COMPLETED)

            if app_task in done:
                # The app finished (normally or with its own error) before
                # the cap was ever crossed - nothing to do here, just
                # surface whatever it did (including re-raising any real
                # exception it produced, unrelated to size).
                exceeded_task.cancel()
                app_task.result()
                return

            # The cap was crossed while the app was still mid-request -
            # stop it immediately rather than letting it keep consuming
            # (and possibly acting on) an oversized body.
            app_task.cancel()
            try:
                await app_task
            except (asyncio.CancelledError, Exception):
                pass

            if not response_started:
                response = JSONResponse(
                    {"detail": f"Uploaded file exceeds the {self.max_bytes}-byte limit."},
                    status_code=413,
                )
                await response(scope, receive, send)
            else:
                logger.warning(
                    f"Upload to {self.path} exceeded {self.max_bytes} bytes but a response "
                    f"was already in flight when this was detected - could not send a 413."
                )
        finally:
            if not exceeded_task.done():
                exceeded_task.cancel()


_upload_rate_limiter = _RateLimiter(max_requests=5, window_seconds=60.0)
_activate_rate_limiter = _RateLimiter(max_requests=10, window_seconds=60.0)


def _enforce_rate_limit(limiter: _RateLimiter, request: Request, label: str) -> None:
    client_ip = request.client.host if request.client else "unknown"
    if not limiter.allow(client_ip):
        raise HTTPException(
            429,
            f"Rate limit exceeded for {label} ({limiter.max_requests} requests per "
            f"{int(limiter.window_seconds)}s per client). Try again shortly.",
        )


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _active_resume_content_hash(db: Session) -> str | None:
    """None if there's no active resume yet (nothing to compare against -
    never a no-op in that case)."""
    active = db.query(ResumeVersion).filter_by(is_active=True).first()
    if active is None:
        return None
    return _content_hash(active.extracted_text)


def _to_summary(resume: ResumeVersion) -> ResumeVersionSummary:
    cleaned = clean_text(resume.extracted_text)
    preview = cleaned[:_PREVIEW_LENGTH] + ("…" if len(cleaned) > _PREVIEW_LENGTH else "")
    return ResumeVersionSummary(
        id=resume.id,
        version_number=resume.version_number,
        uploaded_at=resume.uploaded_at,
        is_active=resume.is_active,
        text_preview=preview,
    )


@router.get("", response_model=list[ResumeVersionSummary])
def list_resumes(db: Session = Depends(get_db)) -> list[ResumeVersionSummary]:
    """In demo mode this always returns just the fictional resume the
    demo dataset builder writes (scripts/build_demo_dataset.py) - not
    because this handler filters anything, but because upload and
    activation are unmounted in demo mode (see unsafe_router below), so
    no other row can ever be added to a demo database."""
    resumes = db.query(ResumeVersion).order_by(ResumeVersion.version_number.desc()).all()
    return [_to_summary(r) for r in resumes]


# Guards the "compute + cache" path below so two concurrent requests
# that both find no cached row never both call the LLM and both try to
# insert - see get_active_ats_report()'s docstring. A single process-
# wide lock is fine here (not a per-resume-version lock): there is only
# ever one active resume at a time, so at most one resume_version_id is
# ever being computed for concurrently in practice, and this endpoint is
# low-traffic enough that briefly serializing requests behind a cache
# miss is not a real cost.
_ats_report_lock = threading.Lock()


@router.get("/active/ats-report", response_model=AtsReportResponse)
def get_active_ats_report(db: Session = Depends(get_db)) -> AtsReportResponse:
    """Returns the cached ATS-compatibility report for the currently
    active resume version, computing and caching it on first request.

    Read-only with respect to resume_versions/job_postings - this never
    changes is_active, never touches matched_skills/missing_skills, and
    is mounted on the safe `router` (available in demo mode too, unlike
    unsafe_router's upload/activate endpoints), since it only reads the
    resume the demo already ships with.

    Caching: huntloop.db_models.ResumeAtsReport has a unique constraint
    on resume_version_id, so a report is computed at most once per
    resume version and served from that cached row on every later call,
    regardless of how many visitors hit this endpoint. Two layers guard
    the "first request after a resume becomes active" race, where
    several requests could all see no cached row at once:
      1. an in-process lock (_ats_report_lock) serializes the whole
         check-compute-insert sequence, so only one request per process
         ever calls the LLM for a given cache miss;
      2. the unique constraint itself is the real backstop (across
         multiple processes/workers, which the lock alone can't cover)
         - a losing concurrent insert hits IntegrityError, which is
         caught by re-querying and returning the row the winner just
         wrote, never a 500.
    No per-visitor rate limiting is added beyond this - unlike
    /resumes/upload or /jobs/{id}/draft-answer, a cache hit does no LLM
    work at all, so there is no meaningful unauthenticated-cost surface
    to bound once the first report exists."""
    resume = db.query(ResumeVersion).filter_by(is_active=True).first()
    if resume is None:
        raise HTTPException(404, "No active resume is on file.")

    cached = db.query(ResumeAtsReport).filter_by(resume_version_id=resume.id).first()
    if cached is not None:
        return _ats_report_to_response(cached)

    with _ats_report_lock:
        # Re-check now that we hold the lock - another request may have
        # computed and cached it while this one was waiting.
        cached = db.query(ResumeAtsReport).filter_by(resume_version_id=resume.id).first()
        if cached is not None:
            return _ats_report_to_response(cached)

        # Imported lazily, not at module level: huntloop.resume_ats_report
        # requires GROQ_API_KEY at import time (same fail-fast convention
        # as huntloop.skills_matching/huntloop.feedback_triage), and
        # huntloop.api.main imports every router together - a module-level
        # import here would break importing the whole API in any
        # environment without that key set, not just this one endpoint.
        from huntloop.resume_ats_report import generate_ats_report

        report = generate_ats_report(resume.extracted_text)
        if report is None:
            raise HTTPException(
                502, "Could not generate an ATS report right now - every configured LLM provider failed."
            )

        row = ResumeAtsReport(
            resume_version_id=resume.id,
            score=report["score"],
            keyword_feedback=report["keyword_feedback"],
            wording_feedback=report["wording_feedback"],
            formatting_feedback=report["formatting_feedback"],
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            # Lost a race with another process/worker that inserted the
            # same resume_version_id first (the lock above only covers
            # this one process) - discard our own result and serve theirs.
            db.rollback()
            cached = db.query(ResumeAtsReport).filter_by(resume_version_id=resume.id).first()
            if cached is None:
                raise HTTPException(502, "ATS report generation failed unexpectedly - please retry.")
            return _ats_report_to_response(cached)
        db.refresh(row)
        return _ats_report_to_response(row)


def _ats_report_to_response(row: ResumeAtsReport) -> AtsReportResponse:
    return AtsReportResponse(
        resume_version_id=row.resume_version_id,
        score=row.score,
        keyword_feedback=row.keyword_feedback,
        wording_feedback=row.wording_feedback,
        formatting_feedback=row.formatting_feedback,
        created_at=row.created_at,
    )


@unsafe_router.post("/upload", response_model=ResumeVersionSummary, status_code=201)
async def upload_resume(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)) -> ResumeVersionSummary:
    _enforce_rate_limit(_upload_rate_limiter, request, "resume upload")

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF uploads are supported.")

    content = await file.read()
    if not content:
        raise HTTPException(400, "Uploaded file is empty.")

    current_max = db.query(func.max(ResumeVersion.version_number)).scalar()
    next_version = (current_max or 0) + 1

    # Stored the same way scripts/ingest_resume.py has always expected
    # resumes to live - data/resumes/ - just written by the API instead
    # of placed there manually first.
    saved_path = save_uploaded_pdf(next_version, file.filename, content)

    processed = await process_resume(db, saved_path)
    extracted_text = processed.extracted_text
    if not extracted_text.strip():
        raise HTTPException(
            422,
            f"No text could be extracted from {file.filename} - it may be a scanned image "
            f"with no text layer, or an empty/corrupt PDF.",
        )

    embedding = processed.embedding

    # Read BEFORE the is_active swap below, purely for the log line -
    # compared against this upload's extracted text (not the raw bytes),
    # see the module docstring's content-hash-check paragraph for why.
    previous_active_hash = _active_resume_content_hash(db)
    content_unchanged = previous_active_hash is not None and previous_active_hash == _content_hash(extracted_text)

    db.query(ResumeVersion).filter_by(is_active=True).update({"is_active": False})

    resume = ResumeVersion(
        version_number=next_version,
        file_path=saved_path,
        extracted_text=extracted_text,
        is_active=True,
        embedding=embedding,
    )
    db.add(resume)
    db.commit()
    db.refresh(resume)

    processed_via = "Modal" if processed.used_modal else "local"
    content_note = (
        " (content identical to the previously active version)" if content_unchanged else ""
    )
    logger.info(
        f"Uploaded resume version {next_version} ({len(extracted_text)} chars extracted via "
        f"{processed_via}), marked active{content_note}. It starts with zero "
        f"resume_skill_matches rows of its own - the daily skills-matching backfill "
        f"(scripts/backfill_skills_matching.py) will populate them over time; no existing "
        f"version's rows were touched."
    )
    return _to_summary(resume)


@unsafe_router.patch("/{resume_id}/activate", response_model=ResumeVersionSummary)
def activate_resume(resume_id: int, request: Request, db: Session = Depends(get_db)) -> ResumeVersionSummary:
    _enforce_rate_limit(_activate_rate_limiter, request, "resume activation")

    resume = db.get(ResumeVersion, resume_id)
    if resume is None:
        raise HTTPException(404, f"No resume_versions row with id={resume_id}")

    if resume.is_active:
        # Already the active version - nothing actually changes, so
        # don't touch other rows or wipe matched/missing_skills for no
        # real reason.
        return _to_summary(resume)

    if resume.embedding is None:
        embedding, used_modal = embed_resume_text(db, resume.extracted_text)
        resume.embedding = embedding
        via = "Modal" if used_modal else "local"
        logger.info(
            f"Computed missing embedding for resume version {resume.version_number} via {via} "
            f"before activating"
        )

    # Same content-hash no-op check as upload_resume() above, purely for
    # the log line - read before the is_active swap below.
    previous_active_hash = _active_resume_content_hash(db)
    content_unchanged = previous_active_hash is not None and previous_active_hash == _content_hash(resume.extracted_text)

    db.query(ResumeVersion).filter_by(is_active=True).update({"is_active": False})
    resume.is_active = True
    db.commit()
    db.refresh(resume)

    content_note = (
        " (content identical to the previously active version)" if content_unchanged else ""
    )
    logger.info(
        f"Activated resume version {resume.version_number}{content_note}. Existing "
        f"resume_skill_matches rows for every version, including the one just deactivated, "
        f"were left untouched; this version's own rows (if any from a prior activation) "
        f"stay as they were."
    )
    return _to_summary(resume)

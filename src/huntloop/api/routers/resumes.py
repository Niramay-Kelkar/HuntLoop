"""
GET /resumes, POST /resumes/upload, PATCH /resumes/{id}/activate -
resume-version management, layered on top of the existing
resume_versions table and Phase 3's extraction/embedding logic
(huntloop.resume_ingestion.extract_text(), huntloop.embeddings.embed_text())
- both reused unchanged here, not reimplemented.

huntloop.embeddings is imported lazily, inside the two functions that
actually need it, not at module level. It requires a working torch
install, which this project's local dev venv doesn't have (see
huntloop.embeddings' own docstring - confirmed by trying:
`ModuleNotFoundError: No module named 'sentence_transformers'` on this
machine). huntloop.api.main imports every router together, so a
module-level import here would make importing the whole API - GET
/health, /jobs, /dashboard/stats included - fail on this machine, not
just the two endpoints that actually need the model. Real, end-to-end
verification of the embedding-dependent paths (upload, and activating a
version with no stored embedding) runs inside the app/api Docker image
instead - the same convention scripts/backfill_embeddings.py already
established for exactly this constraint (see CLAUDE.md/SESSIONS.md).

The GET /jobs match-score query (huntloop.api.routers.jobs) already
resolves "the active resume" dynamically at query time - a plain
`SELECT ... WHERE is_active = true` issued fresh on every request (see
_active_resume_embedding() there), not a cached/hardcoded resume id -
confirmed by reading that code again as part of this step, not assumed.
That's precisely what makes activating a different version here actually
change every job's live match_score on the very next /jobs request, with
no change needed to the jobs router itself.

SECURITY: both endpoints below are unauthenticated (same as every other
endpoint in this API today) and each call to _reset_skills_matching()
below is a full-table UPDATE across job_postings - at 2026-09 scale
that's ~97k rows. Before this hardening, a client could repeatedly
re-upload (or re-activate) the same file and trigger that full wipe on
every single call, with no cap on request rate or file size - a trivial
unauthenticated DoS against the skills-matching backlog (every wipe
forces the whole table back into the days-long Groq/Gemini re-matching
queue, see CLAUDE.md's skills-matching section). Fixed three ways below,
same order as the actual risk:

1. Content-hash check (_content_hash / _active_resume_content_hash) -
   the real root cause. A byte-identical re-upload, or reactivating a
   version whose extracted text already matches what's currently active,
   is now a safe no-op with respect to the skills-matching table: the
   reset is skipped entirely. Hashing the *extracted text*, not the raw
   PDF bytes, is the logically correct comparison - two different PDF
   files (different metadata, re-saved from a different tool, etc.) that
   extract to identical text represent the same resume *content* as far
   as skills-matching cares, and re-triggering a reset for those would
   be exactly the same wasted-wipe problem this fix targets. A genuinely
   different resume (different extracted text) still triggers the reset
   normally - this only short-circuits the true no-op case.
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

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy import func, null, update
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from huntloop.api.dependencies import get_db
from huntloop.api.routers.drafting import _RateLimiter
from huntloop.api.schemas.resumes import ResumeVersionSummary
from huntloop.db_models import JobPosting, ResumeVersion
from huntloop.resume_ingestion import extract_text, save_uploaded_pdf
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


def _reset_skills_matching(db: Session) -> int:
    """Clears matched_skills/missing_skills on every job_postings row -
    called on every resume-activation change (new upload or
    reactivation), so the daily cron's skills-matching stage
    (huntloop.skills_matching, scripts/backfill_skills_matching.py, see
    CLAUDE.md) naturally reprocesses every job against whichever resume
    is now active instead of continuing to show matches computed against
    the old one. Returns the number of rows cleared (for logging).

    Uses sqlalchemy.null(), not plain Python None, as the bind value -
    confirmed live against real Postgres (not assumed) that
    `update(JobPosting).values(matched_skills=None, ...)` on this JSON
    column stores the literal JSON scalar `null` (`matched_skills IS
    NULL` false, `matched_skills::text` = `'null'`), not a real SQL
    NULL - the ORM's round-trip back to Python still reads that as
    `None` (json.loads('null') == None), so a plain
    `assert row.matched_skills is None` doesn't catch it either. That
    silently breaks backfill_skills_matching.py's own
    `.filter(JobPosting.matched_skills.is_(None))` reprocessing query -
    those rows would never be picked up again. null() forces a genuine
    SQL NULL bind instead."""
    result = db.execute(update(JobPosting).values(matched_skills=null(), missing_skills=null()))
    return result.rowcount


@router.get("", response_model=list[ResumeVersionSummary])
def list_resumes(db: Session = Depends(get_db)) -> list[ResumeVersionSummary]:
    """In demo mode this always returns just the fictional resume the
    demo dataset builder writes (scripts/build_demo_dataset.py) - not
    because this handler filters anything, but because upload and
    activation are unmounted in demo mode (see unsafe_router below), so
    no other row can ever be added to a demo database."""
    resumes = db.query(ResumeVersion).order_by(ResumeVersion.version_number.desc()).all()
    return [_to_summary(r) for r in resumes]


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

    extracted_text = extract_text(saved_path)
    if not extracted_text.strip():
        raise HTTPException(
            422,
            f"No text could be extracted from {file.filename} - it may be a scanned image "
            f"with no text layer, or an empty/corrupt PDF.",
        )

    from huntloop.embeddings import embed_text  # lazy - see module docstring

    embedding = embed_text(extracted_text)

    # Must be read BEFORE the is_active swap below, and compared against
    # this upload's extracted text (not the raw bytes) - see the module
    # docstring's content-hash-check paragraph for why.
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
    if content_unchanged:
        cleared = 0
    else:
        cleared = _reset_skills_matching(db)
    db.commit()
    db.refresh(resume)

    if content_unchanged:
        logger.info(
            f"Uploaded resume version {next_version} ({len(extracted_text)} chars extracted), "
            f"marked active - content identical to the previously active version, "
            f"skipped the matched/missing_skills reset"
        )
    else:
        logger.info(
            f"Uploaded resume version {next_version} ({len(extracted_text)} chars extracted), "
            f"marked active, cleared matched/missing_skills on {cleared} job_postings rows"
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
        from huntloop.embeddings import embed_text  # lazy - see module docstring

        resume.embedding = embed_text(resume.extracted_text)
        logger.info(f"Computed missing embedding for resume version {resume.version_number} before activating")

    # Same content-hash no-op check as upload_resume() above - read
    # before the is_active swap below.
    previous_active_hash = _active_resume_content_hash(db)
    content_unchanged = previous_active_hash is not None and previous_active_hash == _content_hash(resume.extracted_text)

    db.query(ResumeVersion).filter_by(is_active=True).update({"is_active": False})
    resume.is_active = True
    if content_unchanged:
        cleared = 0
    else:
        cleared = _reset_skills_matching(db)
    db.commit()
    db.refresh(resume)

    logger.info(
        f"Activated resume version {resume.version_number}, "
        f"cleared matched/missing_skills on {cleared} job_postings rows"
    )
    return _to_summary(resume)

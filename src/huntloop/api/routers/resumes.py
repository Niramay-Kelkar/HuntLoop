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
"""
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, null, update
from sqlalchemy.orm import Session

from huntloop.api.dependencies import get_db
from huntloop.api.schemas.resumes import ResumeVersionSummary
from huntloop.db_models import JobPosting, ResumeVersion
from huntloop.resume_ingestion import extract_text, save_uploaded_pdf
from huntloop.text_cleaning import clean_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resumes", tags=["resumes"])

_PREVIEW_LENGTH = 200


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
    resumes = db.query(ResumeVersion).order_by(ResumeVersion.version_number.desc()).all()
    return [_to_summary(r) for r in resumes]


@router.post("/upload", response_model=ResumeVersionSummary, status_code=201)
async def upload_resume(file: UploadFile = File(...), db: Session = Depends(get_db)) -> ResumeVersionSummary:
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

    db.query(ResumeVersion).filter_by(is_active=True).update({"is_active": False})

    resume = ResumeVersion(
        version_number=next_version,
        file_path=saved_path,
        extracted_text=extracted_text,
        is_active=True,
        embedding=embedding,
    )
    db.add(resume)
    cleared = _reset_skills_matching(db)
    db.commit()
    db.refresh(resume)

    logger.info(
        f"Uploaded resume version {next_version} ({len(extracted_text)} chars extracted), "
        f"marked active, cleared matched/missing_skills on {cleared} job_postings rows"
    )
    return _to_summary(resume)


@router.patch("/{resume_id}/activate", response_model=ResumeVersionSummary)
def activate_resume(resume_id: int, db: Session = Depends(get_db)) -> ResumeVersionSummary:
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

    db.query(ResumeVersion).filter_by(is_active=True).update({"is_active": False})
    resume.is_active = True
    cleared = _reset_skills_matching(db)
    db.commit()
    db.refresh(resume)

    logger.info(
        f"Activated resume version {resume.version_number}, "
        f"cleared matched/missing_skills on {cleared} job_postings rows"
    )
    return _to_summary(resume)

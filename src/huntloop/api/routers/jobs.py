"""
GET /jobs, GET /jobs/{id}, PATCH /jobs/{id}/application - the first real
endpoints built on top of the skeleton from the previous step.

Match score is computed at query time via pgvector's `<=>` operator
against the currently active resume's embedding (same approach used for
every ad hoc score query in earlier sessions - see SESSIONS.md), not
stored. matched_skills/missing_skills are precomputed and stored on
job_postings (see huntloop.skills_matching,
scripts/backfill_skills_matching.py) - read here, not recomputed.
"""
import dataclasses
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, func, literal, select
from sqlalchemy.orm import Session

from huntloop.api.dependencies import get_db
from huntloop.api.schemas.jobs import (
    ApplicationStatusResponse,
    ApplicationStatusUpdate,
    JobDetail,
    JobListResponse,
    JobSummary,
    SalaryEstimate,
    SponsorSummary,
)
from huntloop.api.sponsor_summary import get_sponsorship_summary
from huntloop.db_models import ApplicationStatus, Company, JobApplication, JobPosting, ResumeVersion

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Sentinel accepted by the `department` query param to mean "postings with
# no department set" - department is real free text from each ATS source
# (see CLAUDE.md), so an empty/unspecified value can't be confused with a
# real one; this string is deliberately unlikely to collide with a real
# department name.
UNSPECIFIED_DEPARTMENT = "__unspecified__"


def _active_resume_embedding(db: Session):
    """The active resume's embedding, or None if there's no active resume
    or it hasn't been embedded yet (see scripts/backfill_embeddings.py).
    Callers degrade gracefully to a null match_score in either case."""
    resume = db.query(ResumeVersion).filter_by(is_active=True).first()
    return resume.embedding if resume is not None else None


def _score_and_status_columns(resume_embedding):
    """The two computed columns every /jobs query needs: match_score
    (null if there's no active resume) and application_status (defaults
    to not_applied when no job_applications row exists - see
    ApplicationStatus)."""
    if resume_embedding is not None:
        score_expr = (1 - JobPosting.embedding.cosine_distance(resume_embedding)).label("match_score")
    else:
        score_expr = literal(None, type_=Float).label("match_score")

    status_expr = func.coalesce(JobApplication.status, ApplicationStatus.NOT_APPLIED.value).label(
        "application_status"
    )
    return score_expr, status_expr


def _row_to_summary(row) -> JobSummary:
    job = row.JobPosting
    return JobSummary(
        id=job.id,
        job_title=job.job_title,
        company_name=row.company_name,
        job_url=job.job_url,
        department=job.department,
        date_posted=job.date_posted,
        match_score=row.match_score,
        matched_skills=job.matched_skills,
        missing_skills=job.missing_skills,
        locations=[loc.location_name for loc in job.locations],
        application_status=row.application_status,
        has_sponsor_history=row.matched_sponsor_employer_name is not None,
    )


@router.get("/departments", response_model=list[str])
def list_departments(db: Session = Depends(get_db)) -> list[str]:
    """The real, distinct department values currently in use across
    job_postings (non-null only) - lets the frontend populate its
    department filter from real data instead of a hardcoded list.
    Department is free text from each ATS source (see CLAUDE.md) and is
    deliberately not normalized/canonicalized here."""
    rows = db.execute(
        select(JobPosting.department).where(JobPosting.department.isnot(None)).distinct().order_by(JobPosting.department)
    )
    return [row[0] for row in rows]


@router.get("", response_model=JobListResponse)
def list_jobs(
    company: str | None = Query(None, description="Filter by company name (case-insensitive)."),
    department: str | None = Query(
        None,
        description=(
            "Filter by exact department value, or "
            f"'{UNSPECIFIED_DEPARTMENT}' to return only postings with no department set. "
            "Unset (default) returns postings regardless of department, including those with none."
        ),
    ),
    min_score: float | None = Query(
        None, ge=0, le=1, description="Minimum match score (0-1). Requires an active resume."
    ),
    sort: str = Query(
        "-score",
        description="'score' (ascending) or '-score' (descending, default - best matches first).",
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> JobListResponse:
    if sort not in ("score", "-score"):
        raise HTTPException(400, f"Invalid sort {sort!r} - expected 'score' or '-score'")

    resume_embedding = _active_resume_embedding(db)
    if min_score is not None and resume_embedding is None:
        raise HTTPException(400, "min_score filter requires an active resume with a computed embedding")

    score_expr, status_expr = _score_and_status_columns(resume_embedding)

    query = (
        select(
            JobPosting,
            Company.name.label("company_name"),
            Company.matched_sponsor_employer_name,
            score_expr,
            status_expr,
        )
        .join(Company, JobPosting.company_id == Company.id)
        .outerjoin(JobApplication, JobApplication.job_posting_id == JobPosting.id)
    )

    if company:
        query = query.where(func.lower(Company.name) == company.lower())
    if department is not None:
        if department == UNSPECIFIED_DEPARTMENT:
            query = query.where(JobPosting.department.is_(None))
        else:
            query = query.where(JobPosting.department == department)
    if min_score is not None:
        query = query.where(score_expr >= min_score)

    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()

    if resume_embedding is not None:
        # nulls_last() explicitly on both directions - Postgres's default
        # is NULLS FIRST for DESC, which would put jobs with no embedding
        # yet (score NULL) at the *top* of the "best matches first" sort.
        # Not-yet-scored jobs should sort to the bottom either way.
        query = query.order_by(
            score_expr.desc().nulls_last() if sort == "-score" else score_expr.asc().nulls_last()
        )
    else:
        # No active resume - nothing real to sort by, fall back to a
        # stable, deterministic order rather than erroring on the
        # default sort value.
        query = query.order_by(JobPosting.id.asc())

    query = query.limit(limit).offset(offset)
    rows = db.execute(query).all()

    return JobListResponse(
        items=[_row_to_summary(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{job_id}", response_model=JobDetail)
def get_job(job_id: int, db: Session = Depends(get_db)) -> JobDetail:
    resume_embedding = _active_resume_embedding(db)
    score_expr, status_expr = _score_and_status_columns(resume_embedding)

    query = (
        select(JobPosting, Company, score_expr, status_expr)
        .join(Company, JobPosting.company_id == Company.id)
        .outerjoin(JobApplication, JobApplication.job_posting_id == JobPosting.id)
        .where(JobPosting.id == job_id)
    )
    row = db.execute(query).first()
    if row is None:
        raise HTTPException(404, f"No job posting with id={job_id}")

    job = row.JobPosting
    company = row.Company

    sponsor = get_sponsorship_summary(db, company)
    sponsor_response = SponsorSummary(**dataclasses.asdict(sponsor)) if sponsor is not None else None
    salary_estimate = (
        SalaryEstimate(amount=sponsor.median_wage) if sponsor is not None and sponsor.median_wage is not None else None
    )

    return JobDetail(
        id=job.id,
        job_title=job.job_title,
        company_name=company.name,
        job_url=job.job_url,
        department=job.department,
        date_posted=job.date_posted,
        match_score=row.match_score,
        matched_skills=job.matched_skills,
        missing_skills=job.missing_skills,
        locations=[loc.location_name for loc in job.locations],
        application_status=row.application_status,
        has_sponsor_history=company.matched_sponsor_employer_name is not None,
        job_description=job.job_description,
        ats_platform=company.ats_platform,
        sponsor=sponsor_response,
        salary_estimate=salary_estimate,
    )


@router.patch("/{job_id}/application", response_model=ApplicationStatusResponse)
def update_application_status(
    job_id: int, payload: ApplicationStatusUpdate, db: Session = Depends(get_db)
) -> ApplicationStatusResponse:
    job = db.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(404, f"No job posting with id={job_id}")

    application = db.query(JobApplication).filter_by(job_posting_id=job_id).first()

    if application is None:
        application = JobApplication(job_posting_id=job_id, status=payload.status, notes=payload.notes)
        db.add(application)
    else:
        application.status = payload.status
        application.notes = payload.notes

    # applied_at is set the first time status moves away from
    # not_applied, and never overwritten afterward - it represents when
    # the user first applied, not the most recent status change (that's
    # status_updated_at, bumped automatically by the column's onupdate).
    if payload.status != ApplicationStatus.NOT_APPLIED and application.applied_at is None:
        application.applied_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(application)
    return application

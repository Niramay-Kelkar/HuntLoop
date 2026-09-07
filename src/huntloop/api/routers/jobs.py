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
from sqlalchemy import func, select
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
from huntloop.api.sponsor_summary import _ANNUAL_WAGE_UNIT, get_sponsorship_summary
from huntloop.db_models import (
    ApplicationStatus,
    Company,
    JobApplication,
    JobLocation,
    JobPosting,
    LcaDisclosure,
    ResumeVersion,
)
from huntloop.match_scoring import match_score_expr, match_score_order_by

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Sentinel accepted by the `department` query param to mean "postings with
# no department set" - department is real free text from each ATS source
# (see CLAUDE.md), so an empty/unspecified value can't be confused with a
# real one; this string is deliberately unlikely to collide with a real
# department name.
UNSPECIFIED_DEPARTMENT = "__unspecified__"

# Same idea for `employment_type` - even though its values are normalized
# into a small fixed set (see huntloop.employment_type), not every source/
# posting has one at all, so "unset filter" and "filter to NULL" still
# need to be distinguishable the same way they are for department.
UNSPECIFIED_EMPLOYMENT_TYPE = "__unspecified__"

# Sentinel accepted by the `location` query param to mean "postings with
# no location scraped at all" (no job_locations rows) - same distinction
# as the department/employment_type sentinels between "filter unset" and
# "filter to the absent case".
UNSPECIFIED_LOCATION = "__unspecified__"

# Frequency floor for GET /jobs/locations. job_locations.location_name is
# raw text from each ATS source and is extremely messy - ~15.6k distinct
# values across the table, with the same place written many ways ("San
# Francisco" / "San Francisco, CA" / "San Francisco, California, United
# States") and many multi-location postings storing one joined string
# ("Boston, MA; New York, NY; ..."). An exact-distinct dropdown would be
# unusable, so this endpoint only offers values that appear on at least
# this many postings, and the `location` filter does a substring match
# (see list_jobs) rather than exact equality so "San Francisco" also
# matches "San Francisco, CA".
_LOCATION_MIN_POSTINGS = 100

# Location filtering here is a plain case-insensitive substring match. It
# is deliberately NOT radius / geocoding / "near me" search - that
# remains out of scope (see CLAUDE.md).


def _salary_estimate_expr():
    """Correlated scalar subquery for a posting's estimated salary: the
    median 'Year'-unit DOL wage filed by the company's resolved sponsor
    employer (huntloop.api.sponsor_summary), or NULL when the company has
    no resolved sponsor match or no annual-wage filings. This is the same
    number GET /jobs/{id} exposes as `salary_estimate.amount` - an
    employer-level estimate, never a real posted salary for the job."""
    return (
        select(func.percentile_cont(0.5).within_group(LcaDisclosure.wage_rate_of_pay_from))
        .where(
            LcaDisclosure.employer_name_normalized == Company.matched_sponsor_employer_name,
            LcaDisclosure.wage_unit_of_pay == _ANNUAL_WAGE_UNIT,
        )
        .correlate(Company)
        .scalar_subquery()
    )


def _active_resume_embedding(db: Session):
    """The active resume's embedding, or None if there's no active resume
    or it hasn't been embedded yet (see scripts/backfill_embeddings.py).
    Callers degrade gracefully to a null match_score in either case."""
    resume = db.query(ResumeVersion).filter_by(is_active=True).first()
    return resume.embedding if resume is not None else None


def _score_and_status_columns(resume_embedding):
    """The two computed columns every /jobs query needs: match_score
    (null if there's no active resume - see huntloop.match_scoring) and
    application_status (defaults to not_applied when no job_applications
    row exists - see ApplicationStatus)."""
    score_expr = match_score_expr(resume_embedding)
    status_expr = func.coalesce(JobApplication.status, ApplicationStatus.NOT_APPLIED.value).label(
        "application_status"
    )
    return score_expr, status_expr


# The application-row columns (notes + last-updated timestamp) that both
# the list and detail responses now surface. Kept alongside status_expr
# rather than folded into it so callers can select exactly what they need.
_APPLICATION_DETAIL_COLUMNS = (
    JobApplication.notes.label("application_notes"),
    JobApplication.status_updated_at.label("application_status_updated_at"),
)


def _row_to_summary(row) -> JobSummary:
    job = row.JobPosting
    return JobSummary(
        id=job.id,
        job_title=job.job_title,
        company_name=row.company_name,
        job_url=job.job_url,
        department=job.department,
        employment_type=job.employment_type,
        date_posted=job.date_posted,
        match_score=row.match_score,
        matched_skills=job.matched_skills,
        missing_skills=job.missing_skills,
        locations=[loc.location_name for loc in job.locations],
        application_status=row.application_status,
        application_notes=row.application_notes,
        status_updated_at=row.application_status_updated_at,
        has_sponsor_history=row.matched_sponsor_employer_name is not None,
        salary_estimate=(
            SalaryEstimate(amount=row.salary_estimate_amount)
            if row.salary_estimate_amount is not None
            else None
        ),
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


@router.get("/employment-types", response_model=list[str])
def list_employment_types(db: Session = Depends(get_db)) -> list[str]:
    """The real, distinct employment_type values currently in use across
    job_postings (non-null only) - same reasoning as list_departments:
    populate the frontend filter from real data, not a hardcoded list,
    even though the values are already normalized into a small fixed set
    (see huntloop.employment_type) - not every one of those 5 values is
    necessarily present in the live data at any given time."""
    rows = db.execute(
        select(JobPosting.employment_type)
        .where(JobPosting.employment_type.isnot(None))
        .distinct()
        .order_by(JobPosting.employment_type)
    )
    return [row[0] for row in rows]


@router.get("/locations", response_model=list[str])
def list_locations(db: Session = Depends(get_db)) -> list[str]:
    """The real, distinct location values in use across job_postings that
    appear on at least `_LOCATION_MIN_POSTINGS` postings, most common
    first. See `_LOCATION_MIN_POSTINGS` for why this is frequency-capped
    rather than the full distinct set, and `list_jobs` for how the
    matching `location` filter does a substring (not exact) match."""
    rows = db.execute(
        select(JobLocation.location_name)
        .group_by(JobLocation.location_name)
        .having(func.count() >= _LOCATION_MIN_POSTINGS)
        .order_by(func.count().desc(), JobLocation.location_name)
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
    employment_type: str | None = Query(
        None,
        description=(
            "Filter by exact normalized employment type (Full-time/Part-time/Contract/"
            f"Internship/Other), or '{UNSPECIFIED_EMPLOYMENT_TYPE}' to return only postings "
            "with none set. Unset (default) returns postings regardless of employment type."
        ),
    ),
    min_score: float | None = Query(
        None, ge=0, le=1, description="Minimum match score (0-1). Requires an active resume."
    ),
    location: str | None = Query(
        None,
        description=(
            "Filter to postings with a location matching this text (case-insensitive "
            "substring, so 'San Francisco' also matches 'San Francisco, CA'), or "
            f"'{UNSPECIFIED_LOCATION}' to return only postings with no location scraped. "
            "Not radius/geocoding search. Unset (default) returns postings regardless of location."
        ),
    ),
    salary_min: float | None = Query(
        None,
        ge=0,
        description=(
            "Minimum estimated salary (see GET /jobs/{id} salary_estimate - an employer-level "
            "estimate from DOL wage filings, never a real posted salary). Postings whose company "
            "has no salary estimate are excluded when this is set."
        ),
    ),
    salary_max: float | None = Query(
        None,
        ge=0,
        description="Maximum estimated salary. Postings with no salary estimate are excluded when this is set.",
    ),
    salary_unspecified: bool = Query(
        False,
        description=(
            "When true, return only postings whose company has no salary estimate available "
            "(overrides salary_min/salary_max) - the '__unspecified__'-style option for this filter."
        ),
    ),
    tracked: bool = Query(
        False,
        description=(
            "When true, return only postings the user has actively tracked - i.e. with a "
            "job_applications row whose status is not 'not_applied'. This is the applications "
            "tracker's data source; the full job list is unaffected when unset (default)."
        ),
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
    salary_expr = _salary_estimate_expr()

    query = (
        select(
            JobPosting,
            Company.name.label("company_name"),
            Company.matched_sponsor_employer_name,
            score_expr,
            status_expr,
            *_APPLICATION_DETAIL_COLUMNS,
            salary_expr.label("salary_estimate_amount"),
        )
        .join(Company, JobPosting.company_id == Company.id)
        .outerjoin(JobApplication, JobApplication.job_posting_id == JobPosting.id)
    )

    if tracked:
        query = query.where(JobApplication.status != ApplicationStatus.NOT_APPLIED.value)
    if company:
        query = query.where(func.lower(Company.name) == company.lower())
    if department is not None:
        if department == UNSPECIFIED_DEPARTMENT:
            query = query.where(JobPosting.department.is_(None))
        else:
            query = query.where(JobPosting.department == department)
    if employment_type is not None:
        if employment_type == UNSPECIFIED_EMPLOYMENT_TYPE:
            query = query.where(JobPosting.employment_type.is_(None))
        else:
            query = query.where(JobPosting.employment_type == employment_type)
    if min_score is not None:
        query = query.where(score_expr >= min_score)
    if location is not None:
        if location == UNSPECIFIED_LOCATION:
            query = query.where(
                ~select(JobLocation.id).where(JobLocation.job_id == JobPosting.id).exists()
            )
        else:
            query = query.where(
                select(JobLocation.id)
                .where(
                    JobLocation.job_id == JobPosting.id,
                    JobLocation.location_name.ilike(f"%{location}%"),
                )
                .exists()
            )
    if salary_unspecified:
        query = query.where(salary_expr.is_(None))
    else:
        if salary_min is not None:
            query = query.where(salary_expr >= salary_min)
        if salary_max is not None:
            query = query.where(salary_expr <= salary_max)

    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()

    # match_score_order_by handles both the NULLS LAST ordering (so
    # not-yet-embedded jobs never sort to the top) and the no-active-resume
    # fallback to a stable deterministic order - see huntloop.match_scoring.
    query = query.order_by(*match_score_order_by(resume_embedding, descending=(sort == "-score")))

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
        select(JobPosting, Company, score_expr, status_expr, *_APPLICATION_DETAIL_COLUMNS)
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
        employment_type=job.employment_type,
        date_posted=job.date_posted,
        match_score=row.match_score,
        matched_skills=job.matched_skills,
        missing_skills=job.missing_skills,
        locations=[loc.location_name for loc in job.locations],
        application_status=row.application_status,
        application_notes=row.application_notes,
        status_updated_at=row.application_status_updated_at,
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

    fields = payload.model_fields_set
    if "status" not in fields and "notes" not in fields:
        raise HTTPException(422, "Send at least one of 'status' or 'notes'.")

    application = db.query(JobApplication).filter_by(job_posting_id=job_id).first()

    if application is None:
        application = JobApplication(job_posting_id=job_id)
        db.add(application)

    # Each field is written only when it was actually present in the
    # request body - so a status-only change never clears an existing
    # note, and a notes-only change never touches the status.
    if "status" in fields and payload.status is not None:
        application.status = payload.status

        # applied_at is set the first time status moves away from
        # not_applied, and never overwritten afterward - it represents
        # when the user first applied, not the most recent status change
        # (that's status_updated_at, bumped by the column's onupdate).
        if payload.status != ApplicationStatus.NOT_APPLIED and application.applied_at is None:
            application.applied_at = datetime.now(timezone.utc)

    if "notes" in fields:
        application.notes = payload.notes

    db.commit()
    db.refresh(application)
    return application

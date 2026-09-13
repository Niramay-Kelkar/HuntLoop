"""
Pydantic response/request schemas for the /jobs endpoints
(huntloop.api.routers.jobs). Real schemas, not raw dicts - this is what
makes /docs' OpenAPI schema section actually describe the response
shape instead of just "object".
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from huntloop.db_models import ApplicationStatus


class SalaryEstimate(BaseModel):
    """A rough estimate derived from the sponsor's median wage - never a
    real posted salary for this specific job, hence the mandatory label."""

    amount: float
    basis: str = "Estimated from DOL wage filings for this employer, not job-specific"


class LocationGroup(BaseModel):
    """One country's worth of canonical location groups for GET
    /jobs/locations. `country` is the country every label in `locations`
    belongs to (or "Other" for the handful of labels with no resolved
    country, e.g. a bare "Remote"); `locations` is alphabetized. The
    groups themselves are returned country-alphabetical, "Other" last."""

    country: str
    locations: list[str]


class JobSummary(BaseModel):
    """One row in GET /jobs' paginated list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    job_title: str
    company_name: str
    job_url: str
    department: str | None = Field(
        default=None,
        description="The raw department string as scraped from the source ATS, kept for transparency.",
    )
    department_category: str | None = Field(
        default=None,
        description=(
            "Canonical category the raw department is mapped onto (see "
            "huntloop.department_categorization) - one of ~18 controlled values or 'Other'. "
            "null when there is no raw department or it isn't categorized yet."
        ),
    )
    employment_type: str | None = Field(
        default=None,
        description=(
            "Normalized employment type (Full-time/Part-time/Contract/Internship/Other) - "
            "see huntloop.employment_type. null when the source genuinely exposes no "
            "employment-type signal for this posting."
        ),
    )
    date_posted: datetime | None = None
    match_score: float | None = Field(
        default=None,
        description=(
            "Composite match score in [0, 1] against the active resume (see "
            "huntloop.match_scoring): a calibrated blend of embedding cosine "
            "similarity and, when available, the matched/missing skills ratio. "
            "Postings without a usable skills signal fall back to the calibrated "
            "embedding term alone (see score_basis). null if there's no active "
            "resume or the job has no embedding. Computed at query time, not stored."
        ),
    )
    score_basis: Literal["full", "partial"] | None = Field(
        default=None,
        description=(
            "'full' when match_score is the full composite (embedding + skills), "
            "'partial' when it's the embedding-only fallback because the job has no "
            "usable skills analysis yet - the frontend shows a neutral 'score "
            "provisional - skills analysis pending' marker for these. null when "
            "there's no score at all (no active resume)."
        ),
    )
    matched_skills: list[str] | None = None
    missing_skills: list[str] | None = None
    locations: list[str] = Field(
        default_factory=list,
        description="From job_postings.locations (job_locations table) - empty list if none scraped.",
    )
    application_status: ApplicationStatus = Field(
        description="Defaults to not_applied when no job_applications row exists yet."
    )
    application_notes: str | None = Field(
        default=None,
        description=(
            "Free-text notes the user has saved for this application "
            "(job_applications.notes). null when there is no application row or no note."
        ),
    )
    status_updated_at: datetime | None = Field(
        default=None,
        description=(
            "When the application row was last touched (job_applications.status_updated_at). "
            "null when no application row exists. This is a single timestamp, not a history "
            "of past statuses."
        ),
    )
    has_sponsor_history: bool = Field(
        description=(
            "Whether this job's company has a resolved DOL sponsor match "
            "(companies.matched_sponsor_employer_name is set - see "
            "scripts/resolve_sponsor_matches.py). A cheap presence check, not "
            "the full sponsor aggregate - see JobDetail.sponsor for that."
        )
    )
    salary_estimate: SalaryEstimate | None = Field(
        default=None,
        description=(
            "The same employer-level DOL-wage estimate GET /jobs/{id} returns - "
            "an estimate, never a real posted salary. null when the company has "
            "no resolved sponsor match or no annual-wage filings."
        ),
    )


class SponsorSummary(BaseModel):
    """GET /jobs/{id}'s sponsor aggregate - huntloop.api.sponsor_summary,
    built from the persisted match only, no live fuzzy-matching per
    request."""

    matched_employer_name: str
    most_recent_fiscal_year: int
    total_lcas_most_recent_fiscal_year: int = Field(
        description="LCAs filed by this employer in most_recent_fiscal_year only - a subset of its all-time total."
    )
    median_wage: float | None = Field(
        default=None,
        description="Median wage_rate_of_pay_from across this employer's WAGE_UNIT_OF_PAY='Year' filings only.",
    )
    most_frequent_job_title: str | None = None
    latest_case_status: str | None = Field(
        default=None, description="case_status of this employer's most recently received LCA filing."
    )


class JobDetail(JobSummary):
    """GET /jobs/{id} - JobSummary plus the full job description, the
    company's detected ATS platform, and (if resolved) its DOL sponsor
    summary. salary_estimate is inherited from JobSummary."""

    job_description: str | None = None
    ats_platform: str | None = None
    sponsor: SponsorSummary | None = None
    sponsor_check_status: Literal["confirmed", "checked_no_match", "not_checked"] = Field(
        description=(
            "Three honest states for this job's company, distinct from the bare "
            "has_sponsor_history boolean: 'confirmed' (companies.matched_sponsor_employer_name "
            "is set - a real resolved match, independent of whether the sponsor "
            "aggregate happens to have LCA rows to show), 'checked_no_match' "
            "(scripts/resolve_sponsor_matches.py "
            "has run against this company - companies.sponsor_checked_at is set - "
            "but found nothing above threshold), or 'not_checked' (the matcher has "
            "never run against this company at all, sponsor_checked_at is NULL - "
            "true for most companies onboarded after the matcher's one historical "
            "run). Callers must not treat 'not_checked' as 'no sponsor history'."
        )
    )


class JobListResponse(BaseModel):
    """GET /jobs' top-level paginated response envelope."""

    items: list[JobSummary]
    total: int
    limit: int
    offset: int


class ApplicationStatusUpdate(BaseModel):
    """PATCH /jobs/{id}/application request body.

    Both fields are optional individually, but at least one must be sent.
    `notes` is only written when the key is present in the request body
    (so a status-only update never wipes an existing note, and a
    notes-only update never touches the status)."""

    status: ApplicationStatus | None = None
    notes: str | None = None


class ApplicationStatusResponse(BaseModel):
    """PATCH /jobs/{id}/application response."""

    model_config = ConfigDict(from_attributes=True)

    job_posting_id: int
    status: ApplicationStatus
    applied_at: datetime | None = None
    status_updated_at: datetime
    notes: str | None = None

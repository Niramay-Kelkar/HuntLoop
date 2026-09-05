"""
Pydantic response/request schemas for the /jobs endpoints
(huntloop.api.routers.jobs). Real schemas, not raw dicts - this is what
makes /docs' OpenAPI schema section actually describe the response
shape instead of just "object".
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from huntloop.db_models import ApplicationStatus


class JobSummary(BaseModel):
    """One row in GET /jobs' paginated list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    job_title: str
    company_name: str
    job_url: str
    department: str | None = None
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
            "Cosine similarity (1 - pgvector cosine distance) between this job's "
            "embedding and the currently active resume's embedding. null if either "
            "embedding is missing (e.g. not yet backfilled) or there's no active resume."
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
    has_sponsor_history: bool = Field(
        description=(
            "Whether this job's company has a resolved DOL sponsor match "
            "(companies.matched_sponsor_employer_name is set - see "
            "scripts/resolve_sponsor_matches.py). A cheap presence check, not "
            "the full sponsor aggregate - see JobDetail.sponsor for that."
        )
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


class SalaryEstimate(BaseModel):
    """A rough estimate derived from the sponsor's median wage - never a
    real posted salary for this specific job, hence the mandatory label."""

    amount: float
    basis: str = "Estimated from DOL wage filings for this employer, not job-specific"


class JobDetail(JobSummary):
    """GET /jobs/{id} - JobSummary plus the full job description, the
    company's detected ATS platform, and (if resolved) its DOL sponsor
    summary and a derived salary estimate."""

    job_description: str | None = None
    ats_platform: str | None = None
    sponsor: SponsorSummary | None = None
    salary_estimate: SalaryEstimate | None = None


class JobListResponse(BaseModel):
    """GET /jobs' top-level paginated response envelope."""

    items: list[JobSummary]
    total: int
    limit: int
    offset: int


class ApplicationStatusUpdate(BaseModel):
    """PATCH /jobs/{id}/application request body."""

    status: ApplicationStatus
    notes: str | None = None


class ApplicationStatusResponse(BaseModel):
    """PATCH /jobs/{id}/application response."""

    model_config = ConfigDict(from_attributes=True)

    job_posting_id: int
    status: ApplicationStatus
    applied_at: datetime | None = None
    status_updated_at: datetime
    notes: str | None = None

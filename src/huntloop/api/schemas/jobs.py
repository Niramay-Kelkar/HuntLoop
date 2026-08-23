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


class JobDetail(JobSummary):
    """GET /jobs/{id} - JobSummary plus the full job description."""

    job_description: str | None = None


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

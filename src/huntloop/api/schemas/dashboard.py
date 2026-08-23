"""
Pydantic response schema for GET /dashboard/stats
(huntloop.api.routers.dashboard).
"""
from pydantic import BaseModel, Field


class ApplicationStatusCounts(BaseModel):
    """One count per ApplicationStatus value. Mirrors the same
    coalesce-to-not_applied convention GET /jobs already uses for a
    single job's application_status - not_applied here includes every
    job_postings row with no job_applications row at all, not just rows
    explicitly stored as not_applied."""

    not_applied: int
    applied: int
    interviewing: int
    rejected: int
    offer: int


class DashboardStats(BaseModel):
    """GET /dashboard/stats' response - simple aggregate counts across
    job_postings/companies/job_applications, no per-item detail."""

    total_jobs: int = Field(description="Total row count in job_postings.")
    total_companies: int = Field(description="Total row count in companies.")
    applications_by_status: ApplicationStatusCounts = Field(
        description="Every job_postings row bucketed by its application status - counts sum to total_jobs."
    )
    new_jobs_last_7_days: int = Field(
        description="job_postings rows with scraped_at within the last 7 days of the current time."
    )

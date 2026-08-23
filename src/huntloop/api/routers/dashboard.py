"""
GET /dashboard/stats - simple aggregate counts across job_postings,
companies, and job_applications. API only, per this step's scope - no
frontend page consumes this yet.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from huntloop.api.dependencies import get_db
from huntloop.api.schemas.dashboard import ApplicationStatusCounts, DashboardStats
from huntloop.db_models import ApplicationStatus, Company, JobApplication, JobPosting

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_NEW_JOBS_WINDOW = timedelta(days=7)


@router.get("/stats", response_model=DashboardStats)
def get_dashboard_stats(db: Session = Depends(get_db)) -> DashboardStats:
    total_jobs = db.execute(select(func.count()).select_from(JobPosting)).scalar_one()
    total_companies = db.execute(select(func.count()).select_from(Company)).scalar_one()

    # Same coalesce-to-not_applied convention as huntloop.api.routers.jobs
    # - a job_postings row with no job_applications row counts as
    # not_applied, same as it does everywhere else in the API, so this
    # breakdown's counts sum to total_jobs.
    status_expr = func.coalesce(JobApplication.status, ApplicationStatus.NOT_APPLIED.value).label("status")
    status_rows = db.execute(
        select(status_expr, func.count().label("count"))
        .select_from(JobPosting)
        .outerjoin(JobApplication, JobApplication.job_posting_id == JobPosting.id)
        .group_by(status_expr)
    ).all()
    counts = {row.status: row.count for row in status_rows}
    applications_by_status = ApplicationStatusCounts(
        not_applied=counts.get(ApplicationStatus.NOT_APPLIED.value, 0),
        applied=counts.get(ApplicationStatus.APPLIED.value, 0),
        interviewing=counts.get(ApplicationStatus.INTERVIEWING.value, 0),
        rejected=counts.get(ApplicationStatus.REJECTED.value, 0),
        offer=counts.get(ApplicationStatus.OFFER.value, 0),
    )

    cutoff = datetime.now(timezone.utc) - _NEW_JOBS_WINDOW
    new_jobs_last_7_days = db.execute(
        select(func.count()).select_from(JobPosting).where(JobPosting.scraped_at >= cutoff)
    ).scalar_one()

    return DashboardStats(
        total_jobs=total_jobs,
        total_companies=total_companies,
        applications_by_status=applications_by_status,
        new_jobs_last_7_days=new_jobs_last_7_days,
    )

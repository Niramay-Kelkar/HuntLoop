"""
Integration tests for GET /dashboard/stats (huntloop.api.routers.dashboard),
against the isolated test schema (see tests/conftest.py's api_client
fixture) - never touches real production data.
"""
from datetime import datetime, timedelta, timezone

from huntloop.db_models import ApplicationStatus, Company, JobApplication, JobPosting, JobSource


def _make_job(company, source, title, url, scraped_at):
    return JobPosting(
        job_title=title,
        job_url=url,
        job_description="a real job description",
        company_id=company.id,
        source_id=source.id,
        scraped_at=scraped_at,
    )


def test_dashboard_stats_empty_db_returns_zeros(api_client, db_session):
    response = api_client.get("/dashboard/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["total_jobs"] == 0
    assert body["total_companies"] == 0
    assert body["applications_by_status"] == {
        "not_applied": 0,
        "applied": 0,
        "interviewing": 0,
        "rejected": 0,
        "offer": 0,
    }
    assert body["new_jobs_last_7_days"] == 0


def test_dashboard_stats_reflects_real_counts(api_client, db_session):
    now = datetime.now(timezone.utc)
    recent = now - timedelta(days=1)
    stale = now - timedelta(days=30)

    palantir = Company(name="palantir")
    checkr = Company(name="checkr")
    db_session.add_all([palantir, checkr])
    db_session.commit()

    source = JobSource(name="lever_api")
    db_session.add(source)
    db_session.commit()

    # 3 jobs scraped in the last 7 days, 1 scraped 30 days ago.
    job_a = _make_job(palantir, source, "Job A", "https://example.com/a", recent)
    job_b = _make_job(palantir, source, "Job B", "https://example.com/b", recent)
    job_c = _make_job(checkr, source, "Job C", "https://example.com/c", recent)
    job_d = _make_job(checkr, source, "Job D (stale)", "https://example.com/d", stale)
    db_session.add_all([job_a, job_b, job_c, job_d])
    db_session.commit()

    # Application statuses: A -> applied, B -> interviewing, C and D left
    # with no job_applications row at all (default not_applied).
    db_session.add_all(
        [
            JobApplication(job_posting_id=job_a.id, status=ApplicationStatus.APPLIED),
            JobApplication(job_posting_id=job_b.id, status=ApplicationStatus.INTERVIEWING),
        ]
    )
    db_session.commit()

    response = api_client.get("/dashboard/stats")
    assert response.status_code == 200
    body = response.json()

    assert body["total_jobs"] == 4
    assert body["total_companies"] == 2
    assert body["applications_by_status"] == {
        "not_applied": 2,
        "applied": 1,
        "interviewing": 1,
        "rejected": 0,
        "offer": 0,
    }
    # Sum of the breakdown always equals total_jobs.
    assert sum(body["applications_by_status"].values()) == body["total_jobs"]
    # Only the 3 jobs scraped within the last 7 days count - the stale one
    # (30 days ago) is excluded.
    assert body["new_jobs_last_7_days"] == 3


def test_dashboard_stats_not_applied_counts_explicit_status_too(api_client, db_session):
    """A job_applications row explicitly stored as not_applied (e.g. a
    user reverting a status change) must count the same as a job with no
    row at all - both are 'not applied', not double-counted or dropped."""
    now = datetime.now(timezone.utc)
    company = Company(name="checkr")
    db_session.add(company)
    db_session.commit()
    source = JobSource(name="greenhouse_api")
    db_session.add(source)
    db_session.commit()

    job_explicit = _make_job(company, source, "Explicit not_applied", "https://example.com/e1", now)
    job_implicit = _make_job(company, source, "Implicit not_applied", "https://example.com/e2", now)
    db_session.add_all([job_explicit, job_implicit])
    db_session.commit()

    db_session.add(JobApplication(job_posting_id=job_explicit.id, status=ApplicationStatus.NOT_APPLIED))
    db_session.commit()

    response = api_client.get("/dashboard/stats")
    body = response.json()
    assert body["applications_by_status"]["not_applied"] == 2
    assert body["total_jobs"] == 2

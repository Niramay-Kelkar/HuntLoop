"""
Integration tests for DEMO_MODE (huntloop.demo_mode, huntloop.api.main),
against the isolated test schema. Uses the demo_api_client fixture
(tests/conftest.py), which builds a fresh demo-mode app via
huntloop.api.main.create_app() so each test gets its own rate limiter
and never disturbs the plain api_client fixture other test files use.

Blocked-route and no-op coverage lives here rather than in
test_api_jobs.py/test_api_resumes.py/test_api_drafting.py, which keep
testing the non-demo behavior those modules already had.
"""
import sys

import pytest

from huntloop.db_models import Company, JobApplication, JobPosting, ResumeVersion


def _seed(db_session):
    company = Company(name="acme", ats_platform="greenhouse")
    db_session.add(company)
    db_session.commit()

    job = JobPosting(
        job_title="Backend Engineer",
        job_url="https://example.com/jobs/demo-1",
        company_id=company.id,
    )
    db_session.add(job)
    db_session.commit()
    return job


def test_demo_mode_off_mounts_all_twelve_routes(api_client):
    from huntloop.api.main import app as default_app

    paths = default_app.openapi()["paths"]
    assert len(paths) == 12
    assert "/jobs/{job_id}/draft-answer" in paths
    assert "/resumes/upload" in paths
    assert "/resumes/{resume_id}/activate" in paths
    assert "/demo-info" not in paths


def test_demo_mode_on_blocks_draft_answer(demo_api_client, db_session):
    job = _seed(db_session)
    resp = demo_api_client.post(
        f"/jobs/{job.id}/draft-answer",
        json={"prompt": "Draft something.", "provider": "groq", "api_key": "gsk_some_key"},
    )
    assert resp.status_code == 404


def test_demo_mode_on_blocks_resume_upload(demo_api_client):
    resp = demo_api_client.post("/resumes/upload", files={"file": ("resume.pdf", b"%PDF-1.4", "application/pdf")})
    assert resp.status_code == 404


def test_demo_mode_on_blocks_resume_activate(demo_api_client, db_session):
    resume = ResumeVersion(version_number=1, file_path="x.pdf", extracted_text="fictional resume text", is_active=False)
    db_session.add(resume)
    db_session.commit()

    resp = demo_api_client.patch(f"/resumes/{resume.id}/activate")
    assert resp.status_code == 404


def test_demo_mode_on_resumes_list_still_works(demo_api_client, db_session):
    resume = ResumeVersion(version_number=1, file_path="x.pdf", extracted_text="fictional resume text", is_active=True)
    db_session.add(resume)
    db_session.commit()

    resp = demo_api_client.get("/resumes")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["is_active"] is True


def test_demo_mode_application_patch_is_noop(demo_api_client, db_session):
    job = _seed(db_session)

    resp = demo_api_client.patch(f"/jobs/{job.id}/application", json={"status": "applied", "notes": "test note"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["demo"] is True
    assert body["status"] == "applied"
    assert body["notes"] == "test note"

    # Nothing was actually written.
    assert db_session.query(JobApplication).filter_by(job_posting_id=job.id).first() is None


def test_demo_mode_application_patch_404_for_missing_job(demo_api_client):
    resp = demo_api_client.patch("/jobs/999999/application", json={"status": "applied"})
    assert resp.status_code == 404


def test_demo_mode_application_patch_422_without_fields(demo_api_client, db_session):
    job = _seed(db_session)
    resp = demo_api_client.patch(f"/jobs/{job.id}/application", json={})
    assert resp.status_code == 422


def test_demo_info_route_with_no_demo_meta_row(demo_api_client):
    resp = demo_api_client.get("/demo-info")
    assert resp.status_code == 200
    body = resp.json()
    assert body["snapshot_date"] is None
    assert isinstance(body["message"], str) and len(body["message"]) > 0


def test_demo_info_route_with_demo_meta_row(demo_api_client, db_session):
    from huntloop.db_models import DemoMeta

    db_session.add(DemoMeta(snapshot_date="2026-10-01"))
    db_session.commit()

    resp = demo_api_client.get("/demo-info")
    assert resp.status_code == 200
    assert resp.json()["snapshot_date"] == "2026-10-01"


def test_demo_info_route_not_mounted_outside_demo_mode(api_client):
    resp = api_client.get("/demo-info")
    assert resp.status_code == 404


def test_demo_mode_reads_still_work(demo_api_client, db_session):
    _seed(db_session)
    resp = demo_api_client.get("/jobs")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp = demo_api_client.get("/dashboard/stats")
    assert resp.status_code == 200

    resp = demo_api_client.get("/health")
    assert resp.status_code == 200


def test_demo_mode_rate_limit(monkeypatch, db_session):
    """A low, test-specific limit so this doesn't need 120 real requests
    to exercise - demo_rate_limit_max_requests()/window are read at
    create_app() time, so setting the env vars before building the app
    is enough."""
    from fastapi.testclient import TestClient

    from huntloop.api.dependencies import get_db
    from huntloop.api.main import create_app

    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("DEMO_RATE_LIMIT_MAX_REQUESTS", "3")
    monkeypatch.setenv("DEMO_RATE_LIMIT_WINDOW_SECONDS", "60")
    demo_app = create_app()

    def _override_get_db():
        yield db_session

    demo_app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(demo_app)

    statuses = [client.get("/health").status_code for _ in range(3)]
    assert all(s == 200 for s in statuses), statuses

    fourth = client.get("/health")
    assert fourth.status_code == 429


def test_demo_mode_never_imports_torch_or_sentence_transformers(demo_api_client, db_session):
    """Exercises every mounted demo route, then asserts neither module
    was ever imported into this process - the real guarantee DEMO_MODE
    makes about runtime footprint (see CLAUDE.md's memory investigation)."""
    job = _seed(db_session)
    resume = ResumeVersion(version_number=1, file_path="x.pdf", extracted_text="fictional resume", is_active=True)
    db_session.add(resume)
    db_session.commit()

    demo_api_client.get("/health")
    demo_api_client.get("/jobs")
    demo_api_client.get(f"/jobs/{job.id}")
    demo_api_client.get("/jobs/departments")
    demo_api_client.get("/jobs/employment-types")
    demo_api_client.get("/jobs/locations")
    demo_api_client.get("/dashboard/stats")
    demo_api_client.get("/resumes")
    demo_api_client.get("/demo-info")
    demo_api_client.patch(f"/jobs/{job.id}/application", json={"status": "applied"})
    demo_api_client.post(f"/jobs/{job.id}/draft-answer", json={"prompt": "x", "provider": "groq", "api_key": "k"})
    demo_api_client.post("/resumes/upload", files={"file": ("r.pdf", b"%PDF-1.4", "application/pdf")})

    assert "torch" not in sys.modules
    assert "sentence_transformers" not in sys.modules

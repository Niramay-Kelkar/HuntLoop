"""
Integration tests for the /jobs API endpoints (huntloop.api.routers.jobs),
against the isolated test schema (see tests/conftest.py's api_client
fixture) - never touches real production data.

Embeddings here are small synthetic 384-dim vectors chosen for
deterministic cosine similarity, not real sentence-transformers output -
this machine's local dev venv can't run torch/sentence-transformers
anyway (see SESSIONS.md, 2026-08-22), and the point of these tests is
verifying the API's query/filter/sort/upsert logic, not the embedding
model itself (that's covered by the real end-to-end verification done
manually against real data - see SESSIONS.md).
"""
import pytest

from huntloop.db_models import Company, JobApplication, JobLocation, JobPosting, JobSource, ResumeVersion

EMBEDDING_DIM = 384


def _vec(*active_indices_and_values):
    """A 384-dim vector, mostly zeros, with the given (index, value) pairs
    set - lets tests construct vectors with known cosine similarity to
    each other without needing a real embedding model."""
    v = [0.0] * EMBEDDING_DIM
    for index, value in active_indices_and_values:
        v[index] = value
    return v


RESUME_EMBEDDING = _vec((0, 1.0))
HIGH_MATCH_EMBEDDING = _vec((0, 1.0))  # identical to resume -> cosine similarity 1.0
MID_MATCH_EMBEDDING = _vec((0, 0.70710678), (1, 0.70710678))  # 45 degrees -> ~0.7071
LOW_MATCH_EMBEDDING = _vec((1, 1.0))  # orthogonal -> cosine similarity 0.0


def _seed(db_session):
    """A resume, two companies, and four jobs spanning high/mid/low match
    scores plus one with no embedding at all (to confirm that degrades to
    a null match_score, not an error)."""
    resume = ResumeVersion(
        version_number=1,
        file_path="test.pdf",
        extracted_text="test resume",
        is_active=True,
        embedding=RESUME_EMBEDDING,
    )
    palantir = Company(name="palantir")
    checkr = Company(name="checkr")
    source = JobSource(name="lever_api")
    db_session.add_all([resume, palantir, checkr, source])
    db_session.commit()

    high = JobPosting(
        job_title="High Match Job",
        job_url="https://example.com/jobs/high",
        job_description="full description of the high match job",
        company_id=palantir.id,
        source_id=source.id,
        embedding=HIGH_MATCH_EMBEDDING,
    )
    mid = JobPosting(
        job_title="Mid Match Job",
        job_url="https://example.com/jobs/mid",
        job_description="full description of the mid match job",
        company_id=palantir.id,
        source_id=source.id,
        embedding=MID_MATCH_EMBEDDING,
        matched_skills=["Python", "AWS"],
        missing_skills=["Go"],
    )
    low = JobPosting(
        job_title="Low Match Job",
        job_url="https://example.com/jobs/low",
        job_description="full description of the low match job",
        company_id=checkr.id,
        source_id=source.id,
        embedding=LOW_MATCH_EMBEDDING,
    )
    no_embedding = JobPosting(
        job_title="Not Yet Embedded Job",
        job_url="https://example.com/jobs/none",
        job_description="full description of the not-yet-embedded job",
        company_id=checkr.id,
        source_id=source.id,
        embedding=None,
    )
    db_session.add_all([high, mid, low, no_embedding])
    db_session.commit()

    db_session.add_all(
        [
            JobLocation(job_id=mid.id, location_name="Remote - US"),
            JobLocation(job_id=mid.id, location_name="New York, NY"),
        ]
    )
    db_session.commit()

    return {"high": high, "mid": mid, "low": low, "no_embedding": no_embedding}


def test_list_jobs_returns_scores_sorted_descending_by_default(api_client, db_session):
    _seed(db_session)

    response = api_client.get("/jobs")
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 4
    titles_in_order = [item["job_title"] for item in body["items"]]
    # High > Mid > Low; the not-yet-embedded job has a null score and
    # sorts last under NULLS LAST-by-default descending order.
    assert titles_in_order == ["High Match Job", "Mid Match Job", "Low Match Job", "Not Yet Embedded Job"]

    scores = {item["job_title"]: item["match_score"] for item in body["items"]}
    assert scores["High Match Job"] == pytest.approx(1.0, abs=1e-6)
    assert scores["Mid Match Job"] == pytest.approx(0.70710678, abs=1e-4)
    assert scores["Low Match Job"] == pytest.approx(0.0, abs=1e-6)
    assert scores["Not Yet Embedded Job"] is None


def test_list_jobs_ascending_sort(api_client, db_session):
    _seed(db_session)

    response = api_client.get("/jobs", params={"sort": "score"})
    assert response.status_code == 200
    titles_in_order = [item["job_title"] for item in response.json()["items"]]
    assert titles_in_order == ["Low Match Job", "Mid Match Job", "High Match Job", "Not Yet Embedded Job"]


def test_list_jobs_filters_by_company(api_client, db_session):
    _seed(db_session)

    response = api_client.get("/jobs", params={"company": "checkr"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["company_name"] for item in body["items"]} == {"checkr"}

    # Case-insensitive.
    response = api_client.get("/jobs", params={"company": "CHECKR"})
    assert response.json()["total"] == 2


def test_list_jobs_filters_by_min_score(api_client, db_session):
    _seed(db_session)

    response = api_client.get("/jobs", params={"min_score": 0.5})
    assert response.status_code == 200
    body = response.json()
    titles = {item["job_title"] for item in body["items"]}
    # High (1.0) and Mid (~0.707) pass; Low (0.0) and the null-score job
    # (excluded - null comparisons never satisfy >=) do not.
    assert titles == {"High Match Job", "Mid Match Job"}


def test_list_jobs_default_application_status_is_not_applied(api_client, db_session):
    _seed(db_session)

    response = api_client.get("/jobs")
    for item in response.json()["items"]:
        assert item["application_status"] == "not_applied"


def test_get_job_detail_includes_full_description(api_client, db_session):
    seeded = _seed(db_session)

    response = api_client.get(f"/jobs/{seeded['mid'].id}")
    assert response.status_code == 200
    body = response.json()
    assert body["job_title"] == "Mid Match Job"
    assert body["job_description"] == "full description of the mid match job"
    assert body["matched_skills"] == ["Python", "AWS"]
    assert body["missing_skills"] == ["Go"]
    assert body["locations"] == ["Remote - US", "New York, NY"]


def test_list_jobs_includes_locations_and_empty_list_when_none(api_client, db_session):
    seeded = _seed(db_session)

    response = api_client.get("/jobs")
    items_by_title = {item["job_title"]: item for item in response.json()["items"]}
    assert items_by_title["Mid Match Job"]["locations"] == ["Remote - US", "New York, NY"]
    assert items_by_title["High Match Job"]["locations"] == []


def test_get_job_detail_404_for_missing_job(api_client, db_session):
    _seed(db_session)

    response = api_client.get("/jobs/999999")
    assert response.status_code == 404


def test_patch_application_creates_new_row_and_sets_applied_at(api_client, db_session):
    seeded = _seed(db_session)
    job_id = seeded["high"].id

    response = api_client.patch(f"/jobs/{job_id}/application", json={"status": "applied", "notes": "Applied today"})
    assert response.status_code == 200
    body = response.json()
    assert body["job_posting_id"] == job_id
    assert body["status"] == "applied"
    assert body["notes"] == "Applied today"
    assert body["applied_at"] is not None

    application = db_session.query(JobApplication).filter_by(job_posting_id=job_id).one()
    assert application.status.value == "applied"


def test_patch_application_upserts_not_duplicates(api_client, db_session):
    seeded = _seed(db_session)
    job_id = seeded["high"].id

    first = api_client.patch(f"/jobs/{job_id}/application", json={"status": "applied"})
    applied_at_after_first = first.json()["applied_at"]

    second = api_client.patch(
        f"/jobs/{job_id}/application", json={"status": "interviewing", "notes": "Phone screen scheduled"}
    )
    assert second.status_code == 200
    body = second.json()
    assert body["status"] == "interviewing"
    # applied_at is set once, on the first real application, and never
    # overwritten by later status changes.
    assert body["applied_at"] == applied_at_after_first

    count = db_session.query(JobApplication).filter_by(job_posting_id=job_id).count()
    assert count == 1


def test_get_jobs_reflects_application_status_after_patch(api_client, db_session):
    seeded = _seed(db_session)
    job_id = seeded["low"].id

    api_client.patch(f"/jobs/{job_id}/application", json={"status": "rejected"})

    response = api_client.get("/jobs")
    item = next(i for i in response.json()["items"] if i["id"] == job_id)
    assert item["application_status"] == "rejected"


def test_patch_application_404_for_missing_job(api_client, db_session):
    _seed(db_session)

    response = api_client.patch("/jobs/999999/application", json={"status": "applied"})
    assert response.status_code == 404


def test_patch_application_422_for_invalid_status(api_client, db_session):
    seeded = _seed(db_session)

    response = api_client.patch(f"/jobs/{seeded['high'].id}/application", json={"status": "bogus"})
    assert response.status_code == 422


def test_min_score_without_active_resume_returns_400(api_client, db_session):
    # No _seed() call - no ResumeVersion at all, active or otherwise.
    response = api_client.get("/jobs", params={"min_score": 0.5})
    assert response.status_code == 400

"""
Integration tests for JobDataPipeline.process_item(), against the isolated
test schema set up in conftest.py. No real network calls and no contact
with the real job_postings table - see tests/conftest.py for how isolation
is done.
"""
from huntloop.db_models import JobPosting
from huntloop.items import JobPostingItem


def make_item(job_id="12345", job_url="https://boards.greenhouse.io/testco/jobs/12345",
              job_title="Backend Engineer"):
    """A minimal, Greenhouse-API-shaped item, built the same way the spider builds one."""
    item = JobPostingItem()
    item["job_id"] = job_id
    item["job_title"] = job_title
    item["job_url"] = job_url
    item["job_description"] = "<p>Do backend things.</p>"
    item["date_posted"] = "2026-01-01T00:00:00-05:00"
    item["company_name"] = "TestCo"
    item["name"] = "greenhouse_api"
    item["job_locations"] = ["Remote - US"]
    item["job_skills"] = []
    item["metadata_json"] = "[]"
    return item


def test_process_item_inserts_job_posting(pipeline, db_session):
    item = make_item()

    pipeline.process_item(item, spider=None)

    row = db_session.query(JobPosting).filter_by(job_url=item["job_url"]).one()

    assert row.job_title == item["job_title"]
    assert row.gh_job_id == str(item["job_id"])
    assert row.company.name == "TestCo"
    assert [loc.location_name for loc in row.locations] == ["Remote - US"]
    # is_relevant AND embedding are both computed at insert time (see
    # JobDataPipeline._classify_and_embed) - this test env has no torch/
    # sentence-transformers installed (see CLAUDE.md), so the pipeline
    # degrades gracefully rather than raising: both stay NULL rather than
    # the insert failing outright.
    assert row.is_relevant is None
    assert row.embedding is None


def test_duplicate_job_url_hits_integrity_error_handler(pipeline, db_session, caplog):
    dup_url = "https://boards.greenhouse.io/testco/jobs/dup"
    item1 = make_item(job_id="1", job_url=dup_url)
    item2 = make_item(job_id="2", job_url=dup_url)  # different gh_job_id so the
    # pipeline's own gh_job_id dedup check doesn't short-circuit before the
    # job_url uniqueness constraint gets a chance to fire.

    pipeline.process_item(item1, spider=None)

    with caplog.at_level("ERROR", logger="huntloop.pipelines"):
        pipeline.process_item(item2, spider=None)

    assert "Integrity error" in caplog.text

    count = db_session.query(JobPosting).filter_by(job_url=dup_url).count()
    assert count == 1

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
    # locations are canonicalized at insert time (rules + offline
    # gazetteer, no network) - same auto-compute-at-insert pattern as
    # is_relevant / employment_type / department_category.
    loc = row.locations[0]
    assert loc.location_is_remote is True
    assert loc.location_country == "United States"
    assert loc.location_canonical == "Remote - United States"


def test_process_item_splits_joined_multi_location_string(pipeline, db_session):
    item = make_item(
        job_id="multi-1", job_url="https://boards.greenhouse.io/testco/jobs/multi-1"
    )
    item["job_locations"] = ["San Francisco, CA; New York, NY"]

    pipeline.process_item(item, spider=None)

    row = db_session.query(JobPosting).filter_by(job_url=item["job_url"]).one()
    locs = sorted(row.locations, key=lambda l: l.id)
    # First row keeps the original raw string unchanged; the second place
    # gets its own row so filtering groups each city correctly.
    assert locs[0].location_name == "San Francisco, CA; New York, NY"
    assert locs[0].location_canonical == "San Francisco, CA, United States"
    assert locs[1].location_name == "New York, NY"
    assert locs[1].location_canonical == "New York City, NY, United States"
    # is_relevant AND embedding are both computed at insert time (see
    # JobDataPipeline._classify_and_embed) - this test env has no torch/
    # sentence-transformers installed (see CLAUDE.md), so the pipeline
    # degrades gracefully rather than raising: both stay NULL rather than
    # the insert failing outright.
    assert row.is_relevant is None
    assert row.embedding is None


def test_process_item_stores_department(pipeline, db_session):
    item = make_item(job_id="dept-1", job_url="https://boards.greenhouse.io/testco/jobs/dept-1")
    item["department"] = "Engineering"

    pipeline.process_item(item, spider=None)

    row = db_session.query(JobPosting).filter_by(job_url=item["job_url"]).one()
    assert row.department == "Engineering"


def test_process_item_categorizes_department_at_insert(pipeline, db_session):
    # The raw string is kept verbatim; department_category carries the
    # canonical (rule-based) category computed at insert time.
    item = make_item(job_id="dept-cat-1", job_url="https://boards.greenhouse.io/testco/jobs/dept-cat-1")
    item["department"] = "SW Eng - Core Identity"
    pipeline.process_item(item, spider=None)

    row = db_session.query(JobPosting).filter_by(job_url=item["job_url"]).one()
    assert row.department == "SW Eng - Core Identity"
    assert row.department_category == "Engineering"


def test_process_item_leaves_department_category_null_when_rules_cannot_place_it(pipeline, db_session):
    item = make_item(job_id="dept-cat-2", job_url="https://boards.greenhouse.io/testco/jobs/dept-cat-2")
    item["department"] = "Woven City"
    pipeline.process_item(item, spider=None)

    row = db_session.query(JobPosting).filter_by(job_url=item["job_url"]).one()
    assert row.department == "Woven City"
    # Rules don't guess - the LLM backfill pass handles this tail.
    assert row.department_category is None


def test_repost_backfills_null_department_only(pipeline, db_session):
    """A repost (same gh_job_id) that carries a real department value should
    fill it in when the existing row's department is NULL - and touch
    nothing else on that row."""
    item1 = make_item(job_id="repost-1", job_url="https://boards.greenhouse.io/testco/jobs/repost-1")
    pipeline.process_item(item1, spider=None)

    row = db_session.query(JobPosting).filter_by(job_url=item1["job_url"]).one()
    assert row.department is None
    original_title = row.job_title
    original_is_relevant = row.is_relevant
    original_embedding = row.embedding

    item2 = make_item(job_id="repost-1", job_url="https://boards.greenhouse.io/testco/jobs/repost-1")
    item2["department"] = "Engineering"
    item2["job_title"] = "Some Different Title"  # should NOT overwrite
    pipeline.process_item(item2, spider=None)

    db_session.expire_all()
    row = db_session.query(JobPosting).filter_by(job_url=item1["job_url"]).one()
    assert row.department == "Engineering"
    assert row.department_category == "Engineering"  # backfilled alongside the raw value
    assert row.job_title == original_title
    assert row.is_relevant == original_is_relevant
    assert row.embedding == original_embedding

    count = db_session.query(JobPosting).filter_by(job_url=item1["job_url"]).count()
    assert count == 1


def test_repost_does_not_overwrite_existing_department(pipeline, db_session):
    item1 = make_item(job_id="repost-2", job_url="https://boards.greenhouse.io/testco/jobs/repost-2")
    item1["department"] = "Engineering"
    pipeline.process_item(item1, spider=None)

    item2 = make_item(job_id="repost-2", job_url="https://boards.greenhouse.io/testco/jobs/repost-2")
    item2["department"] = "Sales"
    pipeline.process_item(item2, spider=None)

    db_session.expire_all()
    row = db_session.query(JobPosting).filter_by(job_url=item1["job_url"]).one()
    assert row.department == "Engineering"


def test_process_item_stores_normalized_employment_type(pipeline, db_session):
    # The item carries a source's real raw label (Greenhouse's messy
    # per-company free text, here) - the pipeline normalizes it via
    # huntloop.employment_type before storing, not the raw string.
    item = make_item(job_id="emp-1", job_url="https://boards.greenhouse.io/testco/jobs/emp-1")
    item["employment_type"] = "Full-Time: Experienced"

    pipeline.process_item(item, spider=None)

    row = db_session.query(JobPosting).filter_by(job_url=item["job_url"]).one()
    assert row.employment_type == "Full-time"


def test_process_item_leaves_employment_type_null_when_source_gives_none(pipeline, db_session):
    item = make_item(job_id="emp-2", job_url="https://boards.greenhouse.io/testco/jobs/emp-2")
    # No item["employment_type"] set at all - the source genuinely gave no signal.

    pipeline.process_item(item, spider=None)

    row = db_session.query(JobPosting).filter_by(job_url=item["job_url"]).one()
    assert row.employment_type is None


def test_repost_backfills_null_employment_type_only(pipeline, db_session):
    item1 = make_item(job_id="repost-3", job_url="https://boards.greenhouse.io/testco/jobs/repost-3")
    pipeline.process_item(item1, spider=None)

    row = db_session.query(JobPosting).filter_by(job_url=item1["job_url"]).one()
    assert row.employment_type is None

    item2 = make_item(job_id="repost-3", job_url="https://boards.greenhouse.io/testco/jobs/repost-3")
    item2["employment_type"] = "Contractor"
    pipeline.process_item(item2, spider=None)

    db_session.expire_all()
    row = db_session.query(JobPosting).filter_by(job_url=item1["job_url"]).one()
    assert row.employment_type == "Contract"

    count = db_session.query(JobPosting).filter_by(job_url=item1["job_url"]).count()
    assert count == 1


def test_repost_does_not_overwrite_existing_employment_type(pipeline, db_session):
    item1 = make_item(job_id="repost-4", job_url="https://boards.greenhouse.io/testco/jobs/repost-4")
    item1["employment_type"] = "Full-time"
    pipeline.process_item(item1, spider=None)

    item2 = make_item(job_id="repost-4", job_url="https://boards.greenhouse.io/testco/jobs/repost-4")
    item2["employment_type"] = "Part-time"
    pipeline.process_item(item2, spider=None)

    db_session.expire_all()
    row = db_session.query(JobPosting).filter_by(job_url=item1["job_url"]).one()
    assert row.employment_type == "Full-time"


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

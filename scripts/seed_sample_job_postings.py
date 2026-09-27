"""
Seed script: loads a small, curated, real sample of job_postings data
(data/samples/{companies,job_sources,job_postings,job_locations}_sample.csv.gz)
so a fresh `docker compose up` shows a populated app immediately instead of
an empty dashboard - see data/samples/README.md for exactly what's in this
sample and how it was generated.

This is a real subset of production data (28 companies across all 7
implemented ATS platforms, ~8 recent postings each) - not synthetic, not
fabricated, no PII (public ATS job-posting data only). It is NOT a
substitute for real scraping: it exists purely so a stranger's first run
has something real to look at before they onboard their own companies and
run their own scrape (see main.py / README.md).

Run automatically as part of the `seed` service in docker-compose.yml
(`alembic upgrade head && python scripts/seed_sample_job_postings.py`), or
manually via:

    python scripts/seed_sample_job_postings.py

Expects DATABASE_URL configured via .env, same as the rest of the app, and
job_postings/companies/job_sources/job_locations to already exist
(`alembic upgrade head`).

IMPORTANT - this uses a TABLE-LEVEL pre-check, not a per-row one, unlike
scripts/seed_sample_lca_disclosures.py. That script's per-row
ON CONFLICT (case_number) DO NOTHING is correct for LCA data because real
DOL disclosures and the sample are meant to coexist permanently side by
side. Sample job postings are different: they're a placeholder meant to
disappear in spirit the moment a stranger's own real scrape exists, so
before touching anything this script checks `SELECT COUNT(*) FROM
job_postings` - if that's non-zero for ANY reason (a previous run of this
same script, or the stranger's own real scrape), it logs one line and
exits 0 without inserting a single row. ON CONFLICT DO NOTHING is still
used on the actual inserts below as a cheap secondary defense (protects a
manual re-run before anything else has been inserted), but it is not the
primary safety mechanism - the table-level count check is.

Sample rows carry natural keys (company name, job_source name, job_url),
not the original production integer ids - inserting production ids
directly would collide with this database's own auto-incrementing
sequences the moment a real scrape tries to insert its own new rows.
Company/source/job ids are resolved fresh here via ON CONFLICT ...
DO NOTHING + a follow-up SELECT, the same "upsert by natural key, then
look up the real id" pattern huntloop.pipelines.JobDataPipeline already
uses for a real scrape.

job_locations has no unique constraint (only a non-unique index on
(job_id, location_name) - see huntloop.db_models.JobLocation), so its
inserts cannot use ON CONFLICT DO NOTHING like the other three tables can.
That's fine here: the outer table-level job_postings count check is what
actually prevents this script from ever running twice against real data,
not per-row conflict handling.
"""

import csv
import gzip
import json
import logging
import os
import sys
import time

from sqlalchemy import create_engine, func, null, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from huntloop.settings import DATABASE_URL  # noqa: E402
from huntloop.db_models import Company, JobSource, JobPosting, JobLocation  # noqa: E402
from huntloop.logging_config import setup_logging  # noqa: E402

setup_logging()
logger = logging.getLogger(__name__)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES_DIR = os.path.join(REPO_ROOT, "data", "samples")
COMPANIES_PATH = os.path.join(SAMPLES_DIR, "companies_sample.csv.gz")
JOB_SOURCES_PATH = os.path.join(SAMPLES_DIR, "job_sources_sample.csv.gz")
JOB_POSTINGS_PATH = os.path.join(SAMPLES_DIR, "job_postings_sample.csv.gz")
JOB_LOCATIONS_PATH = os.path.join(SAMPLES_DIR, "job_locations_sample.csv.gz")


def _empty_to_none(value):
    return value if value not in (None, "") else None


def _to_bool(value):
    value = _empty_to_none(value)
    if value is None:
        return None
    return value.lower() in ("t", "true", "1")


def _to_json(value):
    """matched_skills/missing_skills are stored as a JSON *array*
    (JobPosting.matched_skills is a JSON column) - the CSV field holds
    that array's already-serialized text (e.g. '["Python", "SQL"]').
    Passing the raw string straight to a JSON column double-encodes it
    (SQLAlchemy stores the STRING ITSELF as a JSON scalar, not the array
    it represents) - confirmed live: this crashed GET /jobs with a real
    pydantic ValidationError ("Input should be a valid list") the first
    time this script ran against a real database. json.loads() here
    parses it back into a real Python list before insert, exactly the
    object type a JSON column expects.

    Returns sqlalchemy.null(), not plain Python None, for an empty
    value - confirmed live (second real bug, same run) that binding
    plain None here stores the literal JSON scalar `null` on this JSON
    column (`matched_skills IS NULL` false, `matched_skills::text` =
    'null'), not a real SQL NULL - the exact same pitfall
    huntloop.api.routers.resumes._reset_skills_matching() already
    documents and works around. Silently breaks
    scripts/backfill_skills_matching.py's own
    `.filter(JobPosting.matched_skills.is_(None))` reprocessing query
    otherwise - those seeded rows would never be picked up by a real
    backfill run."""
    value = _empty_to_none(value)
    if value is None:
        return null()
    return json.loads(value)


def _read_csv_rows(path):
    with gzip.open(path, mode="rt", newline="") as f:
        return list(csv.DictReader(f))


def seed_companies(session):
    rows = _read_csv_rows(COMPANIES_PATH)
    values = [
        {
            "name": _empty_to_none(r["name"]),
            "website": _empty_to_none(r["website"]),
            "h1b_sponsorship": _to_bool(r["h1b_sponsorship"]),
            "ats_platform": _empty_to_none(r["ats_platform"]),
            "ats_token": _empty_to_none(r["ats_token"]),
            "careers_url": _empty_to_none(r["careers_url"]),
            "matched_sponsor_employer_name": _empty_to_none(r["matched_sponsor_employer_name"]),
        }
        for r in rows
    ]
    stmt = pg_insert(Company).values(values).on_conflict_do_nothing(index_elements=["name"])
    result = session.execute(stmt)
    session.commit()
    inserted = result.rowcount

    names = [v["name"] for v in values]
    id_by_name = dict(
        session.execute(select(Company.name, Company.id).where(Company.name.in_(names))).all()
    )
    logger.info(f"Sample seed: companies - {len(values)} read, {inserted} inserted.")
    return id_by_name


def seed_job_sources(session):
    rows = _read_csv_rows(JOB_SOURCES_PATH)
    values = [{"name": _empty_to_none(r["name"])} for r in rows]
    stmt = pg_insert(JobSource).values(values).on_conflict_do_nothing(index_elements=["name"])
    result = session.execute(stmt)
    session.commit()
    inserted = result.rowcount

    names = [v["name"] for v in values]
    id_by_name = dict(
        session.execute(select(JobSource.name, JobSource.id).where(JobSource.name.in_(names))).all()
    )
    logger.info(f"Sample seed: job_sources - {len(values)} read, {inserted} inserted.")
    return id_by_name


def seed_job_postings(session, company_id_by_name, source_id_by_name):
    rows = _read_csv_rows(JOB_POSTINGS_PATH)
    values = []
    skipped_unresolved = 0
    for r in rows:
        company_id = company_id_by_name.get(r["company_name"])
        source_id = source_id_by_name.get(r["source_name"])
        if company_id is None or source_id is None:
            skipped_unresolved += 1
            continue
        values.append(
            {
                "company_id": company_id,
                "source_id": source_id,
                "job_title": _empty_to_none(r["job_title"]),
                "job_url": _empty_to_none(r["job_url"]),
                "department": _empty_to_none(r["department"]),
                "job_description": _empty_to_none(r["job_description"]),
                "employment_type": _empty_to_none(r["employment_type"]),
                "date_posted": _empty_to_none(r["date_posted"]),
                "is_active": _to_bool(r["is_active"]),
                "gh_job_id": _empty_to_none(r["gh_job_id"]),
                "is_relevant": _to_bool(r["is_relevant"]),
                "department_category": _empty_to_none(r["department_category"]),
                "matched_skills": _to_json(r["matched_skills"]),
                "missing_skills": _to_json(r["missing_skills"]),
            }
        )

    if skipped_unresolved:
        logger.warning(
            f"Sample seed: job_postings - {skipped_unresolved} row(s) skipped "
            f"(company/source name not found - should not happen against the "
            f"bundled companies/job_sources sample files)."
        )

    stmt = pg_insert(JobPosting).values(values).on_conflict_do_nothing(index_elements=["job_url"])
    result = session.execute(stmt)
    session.commit()
    inserted = result.rowcount

    urls = [v["job_url"] for v in values]
    id_by_url = dict(
        session.execute(select(JobPosting.job_url, JobPosting.id).where(JobPosting.job_url.in_(urls))).all()
    )
    logger.info(f"Sample seed: job_postings - {len(values)} read, {inserted} inserted.")
    return id_by_url


def seed_job_locations(session, job_id_by_url):
    rows = _read_csv_rows(JOB_LOCATIONS_PATH)
    values = []
    skipped_unresolved = 0
    for r in rows:
        job_id = job_id_by_url.get(r["job_url"])
        if job_id is None:
            skipped_unresolved += 1
            continue
        values.append(
            {
                "job_id": job_id,
                "location_name": _empty_to_none(r["location_name"]),
                "location_city": _empty_to_none(r["location_city"]),
                "location_region": _empty_to_none(r["location_region"]),
                "location_country": _empty_to_none(r["location_country"]),
                "location_is_remote": _to_bool(r["location_is_remote"]),
                "location_canonical": _empty_to_none(r["location_canonical"]),
            }
        )

    if skipped_unresolved:
        logger.warning(
            f"Sample seed: job_locations - {skipped_unresolved} row(s) skipped "
            f"(job_url not found among seeded job_postings)."
        )

    # No unique constraint on job_locations (see module docstring) - a
    # plain insert is correct here; the outer job_postings count check is
    # what actually prevents this from ever running twice against real
    # data, not per-row conflict handling.
    if values:
        session.bulk_insert_mappings(JobLocation, values)
        session.commit()
    logger.info(f"Sample seed: job_locations - {len(values)} read, {len(values)} inserted.")


def main():
    t0 = time.perf_counter()

    for path in (COMPANIES_PATH, JOB_SOURCES_PATH, JOB_POSTINGS_PATH, JOB_LOCATIONS_PATH):
        if not os.path.exists(path):
            logger.error(f"Sample dataset file not found at {path}.")
            return

    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        existing = session.execute(select(func.count()).select_from(JobPosting)).scalar_one()
        if existing > 0:
            logger.info(
                f"job_postings already has {existing} row(s) - skipping sample seed "
                f"(real data, or a previous seed run, already exists)."
            )
            return

        company_id_by_name = seed_companies(session)
        source_id_by_name = seed_job_sources(session)
        job_id_by_url = seed_job_postings(session, company_id_by_name, source_id_by_name)
        seed_job_locations(session, job_id_by_url)
    finally:
        session.close()

    elapsed = time.perf_counter() - t0
    logger.info(f"Sample job_postings seed complete. Elapsed: {elapsed:.1f}s")


if __name__ == "__main__":
    main()

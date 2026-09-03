"""
One-off script: backfill job_postings.department for existing Lever
(`lever_api`) rows from the raw `categories` object already stored in
job_metadata.metadata_json - no re-scrape needed.

Context (see CLAUDE.md's department-column investigation): department was
NULL for every row across all 7 ATS sources because JobDataPipeline never
passed item["department"] into the JobPosting(...) insert, even though
most spiders (Lever included) already extracted it correctly from real
raw-API fields. That pipeline bug is fixed going forward (see
JobDataPipeline.process_item), so every new Lever scrape now gets
department populated at insert time. This script only repairs rows
scraped before that fix.

Lever is the one source where this repair doesn't need a re-scrape:
LeverScraper's parse() already stores the job's full `categories` dict
(which contains "department" when Lever has one for that posting) inside
metadata_json, e.g.:

    {"categories": {"department": "Engineering", ...}, "workplaceType": ...}

Every other source's stored metadata_json does not carry a raw department
value (Greenhouse never stored the `departments` field at all;
SmartRecruiters/Ashby/iCIMS/Gem only stored adjacent fields, not the
department itself) - those sources need a fresh re-scrape to backfill,
not this kind of repair.

Only rows with department IS NULL and a lever_api source are touched.
Processed in batches of BATCH_SIZE, committing after each - safe to
interrupt and re-run (same pattern as scripts/backfill_relevance.py /
scripts/backfill_embeddings.py). Rows whose stored categories genuinely
have no "department" key are left NULL - that's a true gap in that
specific job's source data, not a bug.
"""
import json
import logging
import os
import sys
import time

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import JobMetadata, JobPosting, JobSource
from huntloop.settings import DATABASE_URL

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
SOURCE_NAME = "lever_api"


def _department_from_metadata(metadata_json) -> str | None:
    """metadata_json is stored double-encoded (JobDataPipeline inserts a
    Python str produced by json.dumps(), and SQLAlchemy's JSON column
    encodes that str again) - so a value read back via the ORM is itself
    a JSON string that must be parsed once more."""
    if metadata_json is None:
        return None
    try:
        inner = json.loads(metadata_json) if isinstance(metadata_json, str) else metadata_json
    except (TypeError, ValueError):
        return None
    if not isinstance(inner, dict):
        return None
    categories = inner.get("categories") or {}
    if not isinstance(categories, dict):
        return None
    dept = categories.get("department")
    return dept or None


def main():
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    start = time.perf_counter()
    total_scanned = 0
    total_filled = 0

    try:
        source = session.query(JobSource).filter_by(name=SOURCE_NAME).first()
        if source is None:
            logger.info(f"No {SOURCE_NAME} source row found - nothing to backfill")
            return

        # Keyset pagination by id, not a repeated `department IS NULL`
        # filter - a row whose stored categories genuinely have no
        # "department" key stays NULL after being scanned, which would
        # make a repeated IS NULL query pick it up forever.
        last_id = 0
        while True:
            rows = (
                session.query(JobPosting, JobMetadata)
                .join(JobMetadata, JobMetadata.job_id == JobPosting.id)
                .filter(
                    JobPosting.source_id == source.id,
                    JobPosting.department.is_(None),
                    JobPosting.id > last_id,
                )
                .order_by(JobPosting.id)
                .limit(BATCH_SIZE)
                .all()
            )
            if not rows:
                break

            for job_post, job_meta in rows:
                dept = _department_from_metadata(job_meta.metadata_json)
                if dept:
                    job_post.department = dept
                    total_filled += 1
            session.commit()

            last_id = rows[-1][0].id
            total_scanned += len(rows)
            logger.info(f"Scanned {len(rows)} lever_api rows (running total scanned: {total_scanned}, filled: {total_filled})")
    finally:
        session.close()

    elapsed = time.perf_counter() - start
    logger.info(
        f"Lever department backfill complete - {total_scanned} rows scanned, "
        f"{total_filled} had a real department in stored metadata and were filled, "
        f"{total_scanned - total_filled} genuinely had none, in {elapsed:.1f}s"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

"""
One-off script: backfill job_postings.employment_type for existing
Greenhouse (`greenhouse_api`), Lever (`lever_api`), and Workday
(`workday_api`) rows entirely from the raw data already stored in
job_metadata.metadata_json - no re-scrape needed for any of these three.

Context (see CLAUDE.md's employment_type investigation): every one of the
7 spiders already extracted SOME raw employment-type signal into
item["employment_type"] where its source genuinely exposes one, but
JobDataPipeline.process_item() never persisted it (the same class of bug
the department column had - see scripts/backfill_department_lever.py).
That's fixed going forward; this script (and its SmartRecruiters/Ashby/
Gem/iCIMS siblings) repairs rows scraped before the fix.

These three sources are backfillable from already-stored metadata_json
because each one's spider happens to store the raw field this column
needs, as a side effect of storing broader per-job metadata for other
reasons:

  - Greenhouse stores its ENTIRE raw `metadata` array (job.get("metadata")),
    which is where a company-configured "Employment Type"/"LEGACY -
    Employment Type" custom field lives, when one exists at all - most
    companies don't configure one (confirmed live: only ~22% of sampled
    rows have any "employment"-named field at all).
  - Lever stores its whole `categories` dict, which includes `commitment`
    directly.
  - Workday stores `timeType` directly.

SmartRecruiters/Ashby/Gem/iCIMS do NOT store their raw employment-type
field in metadata_json (checked directly, not assumed) - those need a
live re-fetch, each in its own script (list-only where the source's list
endpoint carries it; per-job detail where it doesn't).

Every raw value found is passed through
huntloop.employment_type.normalize_employment_type() before being stored
- this script never writes a raw label directly to the column. Only rows
with employment_type IS NULL are touched, keyset-paginated by id (not a
repeated `employment_type IS NULL` re-query) - a row whose stored raw
data genuinely has no employment-type signal stays NULL after being
scanned, which would make a repeated IS NULL query pick it up forever,
same reasoning as backfill_department_lever.py.
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
from huntloop.employment_type import normalize_employment_type
from huntloop.settings import DATABASE_URL

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


def _decode_metadata(metadata_json):
    """metadata_json is stored double-encoded (JobDataPipeline inserts a
    Python str produced by json.dumps(), and SQLAlchemy's JSON column
    encodes that str again) - so a value read back via the ORM is itself
    a JSON string that must be parsed once more."""
    if metadata_json is None:
        return None
    try:
        return json.loads(metadata_json) if isinstance(metadata_json, str) else metadata_json
    except (TypeError, ValueError):
        return None


def _raw_from_greenhouse_metadata(metadata_json) -> str | None:
    entries = _decode_metadata(metadata_json)
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").lower()
        if "employment type" in name:
            value = entry.get("value")
            if value:
                return str(value)
    return None


def _raw_from_lever_metadata(metadata_json) -> str | None:
    inner = _decode_metadata(metadata_json)
    if not isinstance(inner, dict):
        return None
    categories = inner.get("categories") or {}
    if not isinstance(categories, dict):
        return None
    return categories.get("commitment") or None


def _raw_from_workday_metadata(metadata_json) -> str | None:
    inner = _decode_metadata(metadata_json)
    if not isinstance(inner, dict):
        return None
    return inner.get("timeType") or None


_EXTRACTORS = {
    "greenhouse_api": _raw_from_greenhouse_metadata,
    "lever_api": _raw_from_lever_metadata,
    "workday_api": _raw_from_workday_metadata,
}


def _backfill_source(session, source_name: str, extractor) -> tuple[int, int]:
    source = session.query(JobSource).filter_by(name=source_name).first()
    if source is None:
        logger.info(f"No {source_name} source row found - nothing to backfill")
        return 0, 0

    total_scanned = 0
    total_filled = 0
    last_id = 0
    while True:
        rows = (
            session.query(JobPosting, JobMetadata)
            .join(JobMetadata, JobMetadata.job_id == JobPosting.id)
            .filter(
                JobPosting.source_id == source.id,
                JobPosting.employment_type.is_(None),
                JobPosting.id > last_id,
            )
            .order_by(JobPosting.id)
            .limit(BATCH_SIZE)
            .all()
        )
        if not rows:
            break

        for job_post, job_meta in rows:
            raw = extractor(job_meta.metadata_json)
            normalized = normalize_employment_type(raw)
            if normalized:
                job_post.employment_type = normalized
                total_filled += 1
        session.commit()

        last_id = rows[-1][0].id
        total_scanned += len(rows)
        logger.info(
            f"{source_name}: scanned {len(rows)} rows (running total scanned: "
            f"{total_scanned}, filled: {total_filled})"
        )

    return total_scanned, total_filled


def main():
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    start = time.perf_counter()
    grand_scanned = 0
    grand_filled = 0

    try:
        for source_name, extractor in _EXTRACTORS.items():
            scanned, filled = _backfill_source(session, source_name, extractor)
            grand_scanned += scanned
            grand_filled += filled
            logger.info(
                f"{source_name}: done - {scanned} scanned, {filled} filled, "
                f"{scanned - filled} genuinely had no employment-type signal in stored metadata"
            )
    finally:
        session.close()

    elapsed = time.perf_counter() - start
    logger.info(
        f"Metadata-only employment_type backfill complete (greenhouse/lever/workday) - "
        f"{grand_scanned} rows scanned, {grand_filled} filled, in {elapsed:.1f}s"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

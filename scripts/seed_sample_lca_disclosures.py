"""
Seed script: loads the bundled sample LCA disclosure dataset
(data/samples/lca_disclosures_sample.csv.gz) into the lca_disclosures
table, for a quick, realistic demo without downloading anything from DOL.

This is a real subset of production lca_disclosures data (see
data/samples/README.md for how it was generated) - not synthetic. It is
NOT a substitute for the full quarterly file: it exists so a fresh
install can see sponsor-status/salary-estimate features working end to
end in minutes. For real/current/full data, use
scripts/ingest_lca_disclosures.py against a downloaded DOL file instead
(see README.md's "H-1B sponsorship data (optional)" section).

Run manually via:

    python scripts/seed_sample_lca_disclosures.py

Expects DATABASE_URL configured via .env, same as the rest of the app,
and the lca_disclosures table to already exist (`alembic upgrade head`).

Idempotent: case_number has a unique constraint, so re-running this
(or running it after scripts/ingest_lca_disclosures.py has already loaded
real data covering the same case numbers) skips duplicates via
ON CONFLICT DO NOTHING rather than erroring or duplicating rows.
"""

import csv
import gzip
import logging
import os
import sys
import time

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from huntloop.settings import DATABASE_URL  # noqa: E402
from huntloop.db_models import LcaDisclosure  # noqa: E402
from huntloop.logging_config import setup_logging  # noqa: E402

setup_logging()
logger = logging.getLogger(__name__)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_PATH = os.path.join(REPO_ROOT, "data", "samples", "lca_disclosures_sample.csv.gz")

BATCH_SIZE = 2000


def _empty_to_none(value):
    return value if value not in (None, "") else None


def _to_float(value):
    value = _empty_to_none(value)
    return float(value) if value is not None else None


def _to_int(value):
    value = _empty_to_none(value)
    return int(value) if value is not None else None


def row_to_model_kwargs(row):
    """Map one sample CSV row (already in LcaDisclosure column-name shape,
    including a precomputed employer_name_normalized) to model kwargs."""
    return {
        "case_number": _empty_to_none(row["case_number"]),
        "employer_name": _empty_to_none(row["employer_name"]),
        "employer_name_normalized": _empty_to_none(row["employer_name_normalized"]),
        "trade_name_dba": _empty_to_none(row["trade_name_dba"]),
        "case_status": _empty_to_none(row["case_status"]),
        "job_title": _empty_to_none(row["job_title"]),
        "soc_code": _empty_to_none(row["soc_code"]),
        "soc_title": _empty_to_none(row["soc_title"]),
        "worksite_city": _empty_to_none(row["worksite_city"]),
        "worksite_state": _empty_to_none(row["worksite_state"]),
        "worksite_postal_code": _empty_to_none(row["worksite_postal_code"]),
        "wage_rate_of_pay_from": _to_float(row["wage_rate_of_pay_from"]),
        "wage_rate_of_pay_to": _to_float(row["wage_rate_of_pay_to"]),
        "wage_unit_of_pay": _empty_to_none(row["wage_unit_of_pay"]),
        "received_date": _empty_to_none(row["received_date"]),
        "decision_date": _empty_to_none(row["decision_date"]),
        "fiscal_year": _to_int(row["fiscal_year"]),
        "quarter": _to_int(row["quarter"]),
        "source_file": _empty_to_none(row["source_file"]),
    }


def flush_batch(session, batch):
    if not batch:
        return 0, 0
    stmt = pg_insert(LcaDisclosure).values(batch).on_conflict_do_nothing(
        index_elements=["case_number"]
    )
    result = session.execute(stmt)
    session.commit()
    inserted = result.rowcount
    skipped = len(batch) - inserted
    return inserted, skipped


def main():
    t0 = time.perf_counter()

    if not os.path.exists(SAMPLE_PATH):
        logger.error(f"Sample dataset not found at {SAMPLE_PATH}.")
        return

    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    inserted_count = 0
    skipped_count = 0
    total_rows = 0
    batch = []

    try:
        with gzip.open(SAMPLE_PATH, mode="rt", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                total_rows += 1
                batch.append(row_to_model_kwargs(row))
                if len(batch) >= BATCH_SIZE:
                    ins, skip = flush_batch(session, batch)
                    inserted_count += ins
                    skipped_count += skip
                    batch = []
        ins, skip = flush_batch(session, batch)
        inserted_count += ins
        skipped_count += skip
    finally:
        session.close()

    elapsed = time.perf_counter() - t0
    logger.info(
        f"Sample LCA seed complete: {total_rows} rows read, "
        f"{inserted_count} inserted, {skipped_count} already present (skipped). "
        f"Elapsed: {elapsed:.1f}s"
    )
    logger.info(
        "Next step (if you have companies scraped): run "
        "scripts/resolve_sponsor_matches.py to link them to this sample data."
    )


if __name__ == "__main__":
    main()

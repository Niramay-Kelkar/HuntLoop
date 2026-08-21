"""
One-off backfill script: populates employer_name_normalized for existing
lca_disclosures rows that predate normalize_employer_name being wired into
scripts/ingest_lca_disclosures.py's insert path.

Not part of the app's ongoing pipeline - run manually, once, via:

    python scripts/backfill_employer_name_normalized.py

Only rows with employer_name_normalized IS NULL are selected, so this is
safe to interrupt and re-run - a partial run just resumes where it left
off (and running it again after a full pass is a no-op).
"""

import logging
import os
import sys
import time

from sqlalchemy import bindparam, create_engine, select, update
from sqlalchemy.orm import sessionmaker

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from huntloop.settings import DATABASE_URL  # noqa: E402
from huntloop.db_models import LcaDisclosure  # noqa: E402
from huntloop.matching.normalize import normalize_employer_name  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Same reasoning as the ingestion script's batch size: enough rows per
# round trip for real throughput, small enough that one bad batch doesn't
# roll back a huge chunk of already-good work.
BATCH_SIZE = 5000


def main():
    t0 = time.perf_counter()

    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        pending = session.execute(
            select(LcaDisclosure.id, LcaDisclosure.employer_name).where(
                LcaDisclosure.employer_name_normalized.is_(None)
            )
        ).all()
        total_pending = len(pending)
        logger.info(f"{total_pending} row(s) need employer_name_normalized backfilled.")

        # Core-level (table, not ORM-mapped-class) update - a plain
        # executemany-style batched UPDATE, not an ORM bulk-update-by-
        # primary-key operation.
        table = LcaDisclosure.__table__
        update_stmt = (
            update(table)
            .where(table.c.id == bindparam("_id"))
            .values(employer_name_normalized=bindparam("normalized"))
        )

        updated_count = 0
        for i in range(0, total_pending, BATCH_SIZE):
            chunk = pending[i : i + BATCH_SIZE]
            batch = [
                {"_id": row.id, "normalized": normalize_employer_name(row.employer_name)}
                for row in chunk
            ]
            session.execute(update_stmt, batch)
            session.commit()
            updated_count += len(batch)
            logger.info(f"Updated {updated_count}/{total_pending} rows so far.")
    finally:
        session.close()

    elapsed = time.perf_counter() - t0
    logger.info("=" * 60)
    logger.info(f"Rows backfilled: {updated_count}")
    logger.info(f"Elapsed time:    {elapsed:.1f}s")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

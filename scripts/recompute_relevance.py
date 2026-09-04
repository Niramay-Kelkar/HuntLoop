"""
One-off: recompute ``job_postings.is_relevant`` for EVERY row using the
current ``huntloop.relevance_filter.classify_relevance`` (the 2026-09-03
title-only denylist redesign - see
``huntloop-architecture-decisions.md`` and SESSIONS.md).

Unlike ``scripts/backfill_relevance.py`` this re-evaluates ALL rows, not
just ``is_relevant IS NULL`` ones, because the classification logic itself
changed - rows that were ``True`` under the old hybrid embedding+keyword
filter must be re-checked too.

Title-only: needs no torch / sentence-transformers / embedding model, so
it runs in the plain local ``.venv``:

    PYTHONPATH=src .venv/bin/python scripts/recompute_relevance.py

Flags only - never deletes or filters rows out of ``job_postings``.
Processed and committed in batches of ``BATCH_SIZE``; safe to interrupt
and re-run (it is idempotent - a second run reports 0 changes).
"""
import logging
import os
import sys

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import JobPosting
from huntloop.relevance_filter import classify_relevance
from huntloop.settings import DATABASE_URL

logger = logging.getLogger(__name__)

BATCH_SIZE = 2000


def main():
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        total = session.query(func.count(JobPosting.id)).scalar()
        before_true = (
            session.query(func.count(JobPosting.id))
            .filter(JobPosting.is_relevant.is_(True))
            .scalar()
        )
        before_false = (
            session.query(func.count(JobPosting.id))
            .filter(JobPosting.is_relevant.is_(False))
            .scalar()
        )
        before_null = (
            session.query(func.count(JobPosting.id))
            .filter(JobPosting.is_relevant.is_(None))
            .scalar()
        )
        logger.info(
            "BEFORE: total=%d  is_relevant True=%d  False=%d  NULL=%d",
            total, before_true, before_false, before_null,
        )

        changed = 0
        true_to_false = 0
        false_to_true = 0
        null_resolved = 0
        seen = 0
        last_id = 0

        while True:
            rows = (
                session.query(JobPosting)
                .filter(JobPosting.id > last_id)
                .order_by(JobPosting.id)
                .limit(BATCH_SIZE)
                .all()
            )
            if not rows:
                break

            for row in rows:
                last_id = row.id
                seen += 1
                new_val = classify_relevance(row.job_title)
                old_val = row.is_relevant
                if old_val is None:
                    null_resolved += 1
                if old_val is not new_val:
                    if old_val is True and new_val is False:
                        true_to_false += 1
                    elif old_val is False and new_val is True:
                        false_to_true += 1
                    row.is_relevant = new_val
                    changed += 1

            session.commit()
            logger.info("processed %d/%d rows (%d changed so far)", seen, total, changed)

        after_true = (
            session.query(func.count(JobPosting.id))
            .filter(JobPosting.is_relevant.is_(True))
            .scalar()
        )
        after_false = (
            session.query(func.count(JobPosting.id))
            .filter(JobPosting.is_relevant.is_(False))
            .scalar()
        )
        after_null = (
            session.query(func.count(JobPosting.id))
            .filter(JobPosting.is_relevant.is_(None))
            .scalar()
        )
        logger.info(
            "AFTER:  total=%d  is_relevant True=%d  False=%d  NULL=%d",
            total, after_true, after_false, after_null,
        )
        logger.info(
            "CHANGED: %d rows  (True->False=%d, False->True=%d, of which previously-NULL resolved=%d)",
            changed, true_to_false, false_to_true, null_resolved,
        )
    finally:
        session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()

"""Backfill (and keep current) job_postings.department_category.

Maps the raw, free-text job_postings.department string onto the canonical
taxonomy in huntloop.department_categorization: a rule-based keyword pass
first, then an LLM pass (Groq gpt-oss-120b -> Groq gpt-oss-20b -> Gemini,
the same provider chain as skills matching) for the residual distinct
values rules can't confidently place, then "Other" for whatever is left.

Cost is bounded by the number of DISTINCT department strings that still
need categorizing (~thousands), NOT the row count (~99k) - each distinct
string is resolved once and applied to every posting that carries it.

Idempotent and safe to re-run: it only looks at rows where
`department IS NOT NULL AND department_category IS NULL`. New postings
get a rule-based category at insert time (huntloop.pipelines); this
script's job is the LLM tail plus any rows a torch-less / pre-wiring
scrape left uncategorized. It is wired as a stage of the daily
orchestrator (scripts/run_orchestrator_cron.sh) so new data never
silently regresses to permanently-NULL.

Usage:
    python scripts/backfill_department_category.py [--no-llm] [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import JobPosting
from huntloop.department_categorization import categorize_values
from huntloop.logging_config import setup_logging
from huntloop.settings import DATABASE_URL

logger = logging.getLogger("huntloop.backfill_department_category")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-llm", action="store_true", help="rule-based pass only; leave the tail NULL")
    parser.add_argument(
        "--force-other",
        action="store_true",
        help="map every value the rules AND the LLM can't place to 'Other' now, instead of leaving "
        "it NULL for a later run to retry (use only for a deliberate final sweep)",
    )
    parser.add_argument("--limit", type=int, default=None, help="cap the number of distinct values processed")
    parser.add_argument("--dry-run", action="store_true", help="compute the mapping but write nothing")
    args = parser.parse_args()

    setup_logging()

    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Distinct raw department values that still need a category.
        rows = session.execute(
            select(JobPosting.department, func.count())
            .where(JobPosting.department.isnot(None))
            .where(JobPosting.department_category.is_(None))
            .group_by(JobPosting.department)
            .order_by(func.count().desc())
        ).all()

        if not rows:
            logger.info("Nothing to do: every non-NULL-department posting already has a department_category.")
            return 0

        distinct_values = [r[0] for r in rows]
        total_postings = sum(r[1] for r in rows)
        if args.limit is not None:
            distinct_values = distinct_values[: args.limit]
        logger.info(
            "%d distinct uncategorized department values covering %d postings (processing %d)",
            len(rows), total_postings, len(distinct_values),
        )

        mapping = categorize_values(
            distinct_values,
            use_llm=not args.no_llm,
            # Leave still-unresolved values NULL for a later run unless a
            # deliberate final sweep was asked for. With --no-llm the tail
            # is always left NULL (the LLM hasn't had its say yet).
            other_for_unresolved=args.force_other and not args.no_llm,
        )

        if args.dry_run:
            from collections import Counter

            dist = Counter(mapping.values())
            logger.info("DRY RUN - would write categories: %s", dict(dist))
            for raw in list(distinct_values)[:40]:
                logger.info("  %-45s -> %s", raw[:45], mapping.get(raw, "(left NULL)"))
            return 0

        # One UPDATE joined against a VALUES list - a single pass over
        # job_postings instead of one seq scan per distinct value.
        pairs = list(mapping.items())
        updated_rows = 0
        CHUNK = 500
        for start in range(0, len(pairs), CHUNK):
            chunk = pairs[start : start + CHUNK]
            values_sql = ", ".join(f"(:r{i}, :c{i})" for i in range(len(chunk)))
            params = {}
            for i, (raw, cat) in enumerate(chunk):
                params[f"r{i}"] = raw
                params[f"c{i}"] = cat
            res = session.execute(
                text(
                    f"UPDATE job_postings AS jp SET department_category = m.cat "
                    f"FROM (VALUES {values_sql}) AS m(raw, cat) "
                    f"WHERE jp.department = m.raw AND jp.department_category IS NULL"
                ),
                params,
            )
            updated_rows += res.rowcount or 0
        session.commit()
        logger.info("Wrote department_category for %d postings across %d distinct values.", updated_rows, len(mapping))
        return 0
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())

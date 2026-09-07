"""Backfill job_locations.location_{city,region,country,is_remote,canonical}
from the existing free-text location_name.

- Runs in the plain .venv - rules + offline gazetteer only, no torch, no
  network.
- Interrupt-safe / resumable: only touches rows whose location_canonical
  IS NULL, one job at a time, committing per batch.
- A row whose location_name is a joined multi-location string
  ("A, CA; B, NY") is normalized to its FIRST piece in place (its raw
  location_name is left untouched) and gets new sibling job_locations
  rows for the remaining pieces - matching what the pipeline now does at
  insert time.

Usage:  PYTHONPATH=src .venv/bin/python scripts/backfill_location_normalization.py [--limit N]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import JobLocation
from huntloop.location_normalization import normalize_location, split_location_string
from huntloop.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

BATCH = 500


def _apply(session, row: JobLocation) -> int:
    """Normalize one existing row; return number of sibling rows added."""
    pieces = split_location_string(row.location_name) or [row.location_name]
    first = normalize_location(pieces[0])
    row.location_city = first.city
    row.location_region = first.region
    row.location_country = first.country
    row.location_is_remote = first.is_remote
    row.location_canonical = first.canonical[:255]
    added = 0
    for piece in pieces[1:]:
        name = piece[:255]
        exists = (
            session.query(JobLocation.id)
            .filter(JobLocation.job_id == row.job_id, JobLocation.location_name == name)
            .first()
        )
        if exists:
            continue
        norm = normalize_location(piece)
        session.add(
            JobLocation(
                job_id=row.job_id,
                location_name=name,
                location_city=norm.city,
                location_region=norm.region,
                location_country=norm.country,
                location_is_remote=norm.is_remote,
                location_canonical=norm.canonical[:255],
            )
        )
        added += 1
    return added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    url = os.environ["DATABASE_URL"]
    engine = create_engine(url)
    Session = sessionmaker(bind=engine)

    total = Session().execute(
        select(func.count()).select_from(JobLocation).where(JobLocation.location_canonical.is_(None))
    ).scalar_one()
    logger.info("location backfill: %d rows to normalize", total)

    done = added_total = 0
    while True:
        session = Session()
        rows = (
            session.query(JobLocation)
            .filter(JobLocation.location_canonical.is_(None))
            .order_by(JobLocation.id)
            .limit(BATCH)
            .all()
        )
        if not rows:
            session.close()
            break
        for row in rows:
            added_total += _apply(session, row)
        session.commit()
        done += len(rows)
        session.close()
        logger.info("  %d/%d done (+%d split rows)", done, total, added_total)
        if args.limit and done >= args.limit:
            break

    logger.info("location backfill complete: %d normalized, %d split rows added", done, added_total)


if __name__ == "__main__":
    main()

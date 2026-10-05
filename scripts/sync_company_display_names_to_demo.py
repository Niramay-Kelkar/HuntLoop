"""Sync companies.display_name from local production Postgres to the
Neon demo database.

Standalone, one-off sync script - same pattern as
scripts/sync_company_research_to_demo.py. The display_name backfill
(scripts/backfill_company_display_names.py) was only ever run against
local Postgres; this script carries those values over to the companies
that already exist in the separately-seeded Neon demo database, matched
by company name (the only reliable join key - local and Neon company
ids do not correspond to the same company).

Reads from SOURCE_DATABASE_URL (local, read-only - this script never
writes to it) and writes to NEON_DEMO_DATABASE_URL, same environment-
variable convention as sync_company_research_to_demo.py/
build_demo_dataset.py: both are read fresh from os.environ, no fallback
to DATABASE_URL.

The only Neon column this script ever writes is companies.display_name.
It never inserts, deletes, or reseeds any row on either side, and never
touches companies.name or companies.id. A Neon company with no matching
local name, or whose local display_name is null, is left with
display_name = NULL (same fallback-to-raw-slug behavior the frontend
already has for local).

Idempotent / safe to re-run: each company's display_name is updated by
its Neon id, overwriting whatever was there before with the current
local value (or leaving it NULL if local has none) - a straight
one-column sync, not an additive merge.

Usage:
    SOURCE_DATABASE_URL="postgresql+psycopg2://...@localhost:5432/jobsight" \\
    NEON_DEMO_DATABASE_URL="postgresql+psycopg2://...neon.tech/neondb?sslmode=require" \\
    python scripts/sync_company_display_names_to_demo.py [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine, select, text

from huntloop.db_models import Company
from huntloop.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} must be set in the environment.")
    return value


def _local_display_names_by_company_name(source_url: str) -> dict[str, str | None]:
    """Read-only: every local company's display_name, keyed by name."""
    engine = create_engine(source_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET default_transaction_read_only = on"))
            rows = conn.execute(select(Company.name, Company.display_name)).all()
        return {name: display_name for name, display_name in rows}
    finally:
        engine.dispose()


def _neon_companies(neon_url: str) -> list[tuple[int, str, str | None]]:
    """Read-only: Neon's own (id, name, display_name) rows."""
    engine = create_engine(neon_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET default_transaction_read_only = on"))
            return conn.execute(select(Company.id, Company.name, Company.display_name)).all()
    finally:
        engine.dispose()


def main(source_url: str | None = None, neon_url: str | None = None, dry_run: bool = False) -> None:
    """source_url/neon_url let a caller already holding these values (e.g.
    scripts/build_demo_dataset.py, which reads SOURCE_DATABASE_URL/
    TARGET_DATABASE_URL under those exact names) pass them in directly
    instead of this script re-reading the environment under its own
    NEON_DEMO_DATABASE_URL name. The CLI entrypoint below still reads
    both from the environment, unchanged."""
    source_url = source_url or _require_env("SOURCE_DATABASE_URL")
    neon_url = neon_url or _require_env("NEON_DEMO_DATABASE_URL")

    local_display_names = _local_display_names_by_company_name(source_url)
    logger.info("%d local companies read", len(local_display_names))

    neon_companies = _neon_companies(neon_url)
    logger.info("%d companies exist in the Neon demo database", len(neon_companies))

    to_update: list[tuple[int, str, str, str | None]] = []  # (id, name, old, new)
    missing_in_local = []
    local_null = []
    for company_id, name, old_display_name in neon_companies:
        new_display_name = local_display_names.get(name)
        if name not in local_display_names:
            missing_in_local.append(name)
            continue
        if new_display_name is None:
            local_null.append(name)
        if new_display_name != old_display_name:
            to_update.append((company_id, name, old_display_name, new_display_name))

    if missing_in_local:
        logger.info(
            "%d Neon companies have no matching local company name and are left as-is: %s",
            len(missing_in_local), ", ".join(missing_in_local),
        )
    if local_null:
        logger.info(
            "%d Neon companies matched locally but local display_name is null (will be set to null): %s",
            len(local_null), ", ".join(local_null),
        )
    logger.info("%d Neon companies will have display_name changed", len(to_update))

    if dry_run:
        for company_id, name, old, new in to_update:
            logger.info("[dry-run] %s (neon id=%d): %r -> %r", name, company_id, old, new)
        logger.info("Dry run finished: %d would be updated", len(to_update))
        return

    neon_engine = create_engine(neon_url)
    updated = 0
    try:
        with neon_engine.begin() as conn:
            for company_id, name, old, new in to_update:
                conn.execute(
                    Company.__table__.update()
                    .where(Company.id == company_id)
                    .values(display_name=new)
                )
                updated += 1
                logger.info("Updated %s (neon id=%d): %r -> %r", name, company_id, old, new)
    finally:
        neon_engine.dispose()

    logger.info("Sync finished: %d companies' display_name updated on Neon", updated)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Log what would be synced without writing to Neon.")
    args = parser.parse_args()
    main(dry_run=args.dry_run)

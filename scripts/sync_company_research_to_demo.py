"""Sync company_research snapshots from local production Postgres to the
Neon demo database.

Standalone, one-off sync script - not part of scripts/backfill_company_research.py
and not part of scripts/build_demo_dataset.py/deploy_demo_data.py's full
snapshot rebuild. Those scripts build the ENTIRE demo dataset from scratch;
this script does one narrow thing on top of an already-built demo: carry
over company_research rows for companies that already exist in both
databases, matched by company name (the only reliable key - local and
Neon company ids do not correspond to the same company, since Neon's
dataset is a sampled subset built independently).

Reads from SOURCE_DATABASE_URL (local, read-only - this script never
writes to it) and writes to NEON_DEMO_DATABASE_URL, same environment-variable
convention as build_demo_dataset.py/deploy_demo_data.py: both are read
fresh from os.environ, no fallback to DATABASE_URL. The only Neon table
this script ever writes to is company_research - companies, job_postings,
and everything else on the Neon side are read-only lookups (to resolve
Neon's own company_id for a given name) and are never modified.

Idempotent / safe to re-run (e.g. for a future refresh after the local
company_research snapshots are updated): each company's row is upserted
by Neon's own company_id (ON CONFLICT (company_id) DO UPDATE), matching
the "a refresh REPLACES the row" semantics CompanyResearch already has
locally.

Usage:
    SOURCE_DATABASE_URL="postgresql://...@localhost:5432/jobsight" \\
    NEON_DEMO_DATABASE_URL="postgresql://...neon.tech/neondb?sslmode=require" \\
    python scripts/sync_company_research_to_demo.py [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import Company, CompanyResearch
from huntloop.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} must be set in the environment.")
    return value


def _local_research_by_company_name(source_url: str) -> dict[str, dict]:
    """Read-only: every local company that has a company_research row,
    keyed by company name (the only key that means anything on the Neon
    side too)."""
    engine = create_engine(source_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET default_transaction_read_only = on"))
            rows = conn.execute(
                select(
                    Company.name,
                    CompanyResearch.summary,
                    CompanyResearch.recent_news,
                    CompanyResearch.funding_signal,
                    CompanyResearch.hiring_signal,
                    CompanyResearch.fetched_at,
                ).join(CompanyResearch, CompanyResearch.company_id == Company.id)
            ).all()
        result = {}
        for name, summary, recent_news, funding_signal, hiring_signal, fetched_at in rows:
            result[name] = {
                "summary": summary,
                "recent_news": recent_news,
                "funding_signal": funding_signal,
                "hiring_signal": hiring_signal,
                "fetched_at": fetched_at,
            }
        return result
    finally:
        engine.dispose()


def _neon_company_ids_by_name(neon_url: str) -> dict[str, int]:
    """Read-only lookup of Neon's own company_id per company name."""
    engine = create_engine(neon_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(select(Company.id, Company.name)).all()
        return {name: company_id for company_id, name in rows}
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

    local_research = _local_research_by_company_name(source_url)
    logger.info("%d local companies have a company_research snapshot", len(local_research))

    neon_ids = _neon_company_ids_by_name(neon_url)
    logger.info("%d companies exist in the Neon demo database", len(neon_ids))

    matched_names = sorted(set(local_research) & set(neon_ids))
    logger.info("%d companies exist in both and will be synced", len(matched_names))

    missing_in_neon = sorted(set(local_research) - set(neon_ids))
    if missing_in_neon:
        logger.info(
            "%d local companies with research are not in Neon's company set and will be skipped: %s",
            len(missing_in_neon), ", ".join(missing_in_neon),
        )

    if dry_run:
        for name in matched_names:
            logger.info("[dry-run] would upsert company_research for %s (neon company_id=%d)", name, neon_ids[name])
        logger.info("Dry run finished: %d would be synced", len(matched_names))
        return

    neon_engine = create_engine(neon_url)
    synced = 0
    try:
        with neon_engine.begin() as conn:
            for name in matched_names:
                data = local_research[name]
                neon_company_id = neon_ids[name]
                stmt = pg_insert(CompanyResearch.__table__).values(
                    company_id=neon_company_id,
                    summary=data["summary"],
                    recent_news=data["recent_news"],
                    funding_signal=data["funding_signal"],
                    hiring_signal=data["hiring_signal"],
                    fetched_at=data["fetched_at"],
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["company_id"],
                    set_={
                        "summary": stmt.excluded.summary,
                        "recent_news": stmt.excluded.recent_news,
                        "funding_signal": stmt.excluded.funding_signal,
                        "hiring_signal": stmt.excluded.hiring_signal,
                        "fetched_at": stmt.excluded.fetched_at,
                    },
                )
                conn.execute(stmt)
                synced += 1
                logger.info("Synced company_research for %s (neon company_id=%d)", name, neon_company_id)
    finally:
        neon_engine.dispose()

    logger.info("Sync finished: %d companies synced to Neon's company_research table", synced)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Log what would be synced without writing to Neon.")
    args = parser.parse_args()
    main(dry_run=args.dry_run)

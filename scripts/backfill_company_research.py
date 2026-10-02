"""Backfill (and keep current) company-level research snapshots via Tavily.

Standalone integration - unrelated to scripts/triage_feedback.py or any
other LLM-provider backfill in this project; shares no code with the
feedback/triage pipeline. See huntloop.company_research for the actual
Tavily call + heuristic signal extraction, and CLAUDE.md for the full
design writeup.

For each company with no company_research row, or one older than
RESEARCH_STALENESS_DAYS (30 - a once-a-month-per-company refresh, not
frequent polling), makes ONE Tavily search call and upserts the result
into company_research.

Credit budget: this Tavily account has a hard 1000 credits/month, and
TAVILY_MONTHLY_REQUEST_BUDGET (env var, default 600) caps how many real
Tavily calls this script may make in a given CALENDAR MONTH - well under
the hard cap, to leave headroom for company growth and manual testing
outside this script. This is tracked durably in the `tavily_usage` table
(one row per month, incremented by 1 after each real call that gets a
response), NOT an in-memory counter, so it survives the script being
re-run or the process restarting. The budget is checked before EVERY
call, not just once at startup, so a run that would otherwise cross the
cap mid-run stops cleanly exactly at the cap instead of overshooting it.

Never called inline from any user-facing request path - backfill-only,
same principle as huntloop.skills_matching and
huntloop.department_categorization.

--only-neon-companies restricts the run to exactly the companies whose
name currently exists in the Neon demo database's `companies` table
(read-only connection via NEON_DEMO_DATABASE_URL), in that same priority
order (never-researched first, then stalest) - all other companies are
skipped for this invocation. Without the flag, existing behavior (process
all local companies) is unchanged. This is reusable since Neon's demo
company set may change later - it re-reads Neon's company list fresh on
every run rather than hardcoding one.

Usage:
    python scripts/backfill_company_research.py [--limit N] [--dry-run] [--only-neon-companies]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine, or_, select
from sqlalchemy.orm import sessionmaker

from huntloop.company_research import fetch_company_research
from huntloop.db_models import Company, CompanyResearch, TavilyUsage
from huntloop.logging_config import setup_logging
from huntloop.settings import DATABASE_URL

setup_logging()
logger = logging.getLogger(__name__)

DEFAULT_MONTHLY_BUDGET = 600
RESEARCH_STALENESS_DAYS = 30


def _monthly_budget() -> int:
    return int(os.getenv("TAVILY_MONTHLY_REQUEST_BUDGET", str(DEFAULT_MONTHLY_BUDGET)))


def _current_month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _get_or_create_usage_row(session) -> TavilyUsage:
    month = _current_month_key()
    row = session.query(TavilyUsage).filter_by(month=month).first()
    if row is None:
        row = TavilyUsage(month=month, requests_used=0)
        session.add(row)
        session.commit()
    return row


def _record_request_spent(session, usage_row: TavilyUsage) -> None:
    """Commits immediately so the count is durable even if the script is
    interrupted mid-run - see module docstring."""
    usage_row.requests_used += 1
    session.commit()


def _neon_company_names() -> set[str]:
    """Read-only lookup of the company names currently in the Neon demo
    database - NEON_DEMO_DATABASE_URL, a separate connection from this
    script's own (local) DATABASE_URL. Never writes to Neon."""
    neon_url = os.environ.get("NEON_DEMO_DATABASE_URL")
    if not neon_url:
        raise SystemExit("--only-neon-companies requires NEON_DEMO_DATABASE_URL to be set.")
    neon_engine = create_engine(neon_url)
    try:
        with neon_engine.connect() as conn:
            rows = conn.execute(select(Company.name)).all()
        return {row[0] for row in rows}
    finally:
        neon_engine.dispose()


def _companies_needing_research(session, limit: int | None, only_neon_companies: bool):
    staleness_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=RESEARCH_STALENESS_DAYS)
    query = (
        select(Company, CompanyResearch)
        .outerjoin(CompanyResearch, CompanyResearch.company_id == Company.id)
        .where(or_(CompanyResearch.id.is_(None), CompanyResearch.fetched_at < staleness_cutoff))
        # Never-researched companies first, then the stalest existing
        # snapshots - so a budget-limited run makes the most useful
        # progress first.
        .order_by(CompanyResearch.fetched_at.is_(None).desc(), CompanyResearch.fetched_at.asc())
    )
    if only_neon_companies:
        neon_names = _neon_company_names()
        logger.info("--only-neon-companies: restricting to %d company names present in Neon", len(neon_names))
        query = query.where(Company.name.in_(neon_names))
    if limit is not None:
        query = query.limit(limit)
    return session.execute(query).all()


def _upsert_research(session, company: Company, existing: CompanyResearch | None, result) -> None:
    fetched_at = datetime.now(timezone.utc).replace(tzinfo=None)
    recent_news = [
        {"title": item.title, "url": item.url, "content": item.content, "score": item.score}
        for item in result.recent_news
    ]
    if existing is not None:
        existing.summary = result.summary
        existing.recent_news = recent_news
        existing.funding_signal = result.funding_signal
        existing.hiring_signal = result.hiring_signal
        existing.fetched_at = fetched_at
    else:
        session.add(
            CompanyResearch(
                company_id=company.id,
                summary=result.summary,
                recent_news=recent_news,
                funding_signal=result.funding_signal,
                hiring_signal=result.hiring_signal,
                fetched_at=fetched_at,
            )
        )
    session.commit()


def main(limit: int | None = None, dry_run: bool = False, only_neon_companies: bool = False) -> None:
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        budget = _monthly_budget()
        usage_row = _get_or_create_usage_row(session)
        remaining = budget - usage_row.requests_used
        logger.info(
            "Tavily monthly budget: %d used / %d budget for %s (%d remaining)",
            usage_row.requests_used, budget, usage_row.month, remaining,
        )
        if remaining <= 0:
            logger.warning(
                "Tavily monthly request budget (%d) already reached for %s - making no new requests "
                "this run. Resets next calendar month.", budget, usage_row.month,
            )
            return

        candidates = _companies_needing_research(session, limit, only_neon_companies)
        logger.info("%d companies need a research snapshot (no snapshot, or older than %d days)",
                    len(candidates), RESEARCH_STALENESS_DAYS)

        fetched = 0
        skipped_budget = 0
        failed = 0
        for company, existing in candidates:
            usage_row = _get_or_create_usage_row(session)  # re-read in case the month rolled over mid-run
            remaining = budget - usage_row.requests_used
            if remaining <= 0:
                skipped_budget = len(candidates) - fetched - failed
                logger.warning(
                    "Tavily monthly request budget (%d) reached mid-run for %s - stopping, "
                    "%d companies left unprocessed this run.", budget, usage_row.month, skipped_budget,
                )
                break

            if dry_run:
                logger.info("[dry-run] would fetch research for company_id=%d (%s)", company.id, company.name)
                continue

            credit_spent, result = fetch_company_research(company.name, company.website)
            if credit_spent:
                _record_request_spent(session, usage_row)

            if result is None:
                logger.warning("Tavily fetch failed for company_id=%d (%s) - leaving existing snapshot, if any, untouched", company.id, company.name)
                failed += 1
                continue

            _upsert_research(session, company, existing, result)
            fetched += 1
            logger.info(
                "Stored research for company_id=%d (%s): summary=%s, %d news items, funding_signal=%s, hiring_signal=%s",
                company.id, company.name,
                "yes" if result.summary else "no",
                len(result.recent_news),
                "yes" if result.funding_signal else "no",
                "yes" if result.hiring_signal else "no",
            )

        logger.info(
            "Company-research run finished: %d fetched, %d failed, %d skipped (budget)",
            fetched, failed, skipped_budget,
        )
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Process at most this many companies this invocation.")
    parser.add_argument("--dry-run", action="store_true", help="Log what would be fetched without calling Tavily or writing to the DB.")
    parser.add_argument(
        "--only-neon-companies", action="store_true",
        help="Restrict this run to companies whose name exists in the Neon demo DB "
             "(NEON_DEMO_DATABASE_URL, read-only), in the same priority order.",
    )
    args = parser.parse_args()
    main(limit=args.limit, dry_run=args.dry_run, only_neon_companies=args.only_neon_companies)

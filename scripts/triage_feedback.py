"""
Out-of-band LLM triage pass for feedback.Report rows left `triage_status
= pending` by POST /feedback (huntloop.api.routers.feedback) - which
deliberately never calls an LLM inline (see that router's docstring).
This script does the actual summarization, on its own short-interval
schedule, separate from the once-daily scrape/skills-matching/
department-categorization orchestrator (scripts/run_orchestrator_cron.sh)
- see scripts/run_feedback_triage_cron.sh for the wrapper that runs this
one every few minutes.

    python scripts/triage_feedback.py [--limit N]

Budget: TRIAGE_DAILY_BUDGET env var (default 100) caps how many rows
this script will triage per CALENDAR DAY, counted as `feedback` rows
with triage_status=done AND updated_at falling on today (UTC) - not a
per-invocation cap, since this runs every few minutes and the same
day's earlier runs already used up part of the budget. Once the budget
is hit, remaining pending rows are left exactly as they are
(triage_status stays `pending`, NOT set to `skipped_budget` - per the
task, they should get triaged automatically once the budget resets
tomorrow, not be permanently abandoned). `skipped_budget` exists in the
schema for a future, more deliberate "we've decided never to triage
this one" action - this script never sets it itself.

For each row it does process: huntloop.feedback_triage.summarize_feedback()
(Groq primary, Gemini fallback - the same two providers skills-matching
uses, via its own small prompt/contract - see that module's docstring
for why it's not reusing huntloop.skills_matching_router directly). A
row whose summarization fails on every configured provider is left
untouched (still `pending`) for a later run - never marked done with an
empty/placeholder summary, and never crashes the run.

No advisory lock here (unlike backfill_skills_matching.py): a missed
overlap between two triage runs just means each run that wins a given
row's UPDATE makes forward progress on it; a short-interval, low-volume,
per-row script has much lower collision stakes than the once-daily
multi-hour skills-matching backfill lockf/advisory-lock guard exists
for, so one wasn't worth the added complexity here. If this starts
seeing real double-processing, add one the same way Step H's fix did.
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timezone

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import Feedback, FeedbackTriageStatus
from huntloop.feedback_triage import summarize_feedback
from huntloop.logging_config import setup_logging
from huntloop.settings import DATABASE_URL

setup_logging()
logger = logging.getLogger(__name__)

DEFAULT_DAILY_BUDGET = 100


def _daily_budget() -> int:
    return int(os.getenv("TRIAGE_DAILY_BUDGET", str(DEFAULT_DAILY_BUDGET)))


def _triaged_today_count(session) -> int:
    today = datetime.now(timezone.utc).date()
    return (
        session.query(func.count(Feedback.id))
        .filter(Feedback.triage_status == FeedbackTriageStatus.DONE, func.date(Feedback.updated_at) == today)
        .scalar()
    )


def main(limit: int | None = None) -> None:
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        budget = _daily_budget()
        already_today = _triaged_today_count(session)
        remaining_budget = budget - already_today
        if remaining_budget <= 0:
            logger.info(
                "Daily triage budget (%d) already reached today (%d done) - leaving pending "
                "rows untouched until the budget resets.", budget, already_today,
            )
            return

        pending = (
            session.query(Feedback)
            .filter(Feedback.triage_status == FeedbackTriageStatus.PENDING)
            .order_by(Feedback.created_at.asc())
            .all()
        )
        to_process = pending[: remaining_budget if limit is None else min(remaining_budget, limit)]
        logger.info(
            "Triaging %d/%d pending feedback rows (daily budget %d, %d already done today)",
            len(to_process), len(pending), budget, already_today,
        )

        succeeded = 0
        failed = 0
        for row in to_process:
            summary = summarize_feedback(row.category.value, row.raw_text)
            if summary is None:
                logger.warning("Triage failed for feedback id=%d (category=%s) - leaving pending", row.id, row.category.value)
                failed += 1
                continue
            row.llm_summary = summary
            row.triage_status = FeedbackTriageStatus.DONE
            session.commit()
            succeeded += 1
            logger.info("Triaged feedback id=%d: %s", row.id, summary)

        logger.info("Triage run finished: %d succeeded, %d failed/left pending", succeeded, failed)
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Process at most this many rows this invocation.")
    args = parser.parse_args()
    main(limit=args.limit)

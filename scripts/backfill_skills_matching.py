"""
Run huntloop.skills_matching.match_skills_batch() (the batch-of-5
production path validated in the measurement step, see SESSIONS.md
2026-08-22) against job_postings rows that don't already have a result,
and store matched_skills/missing_skills.

Since 2026-08-22 this is a daily recurring stage of the cron orchestrator
(scripts/run_orchestrator_cron.sh), not a one-off manual backfill - it
runs automatically after main.py's scrape in every scheduled run, so it
both works through the existing backlog over multiple days (bounded by
the real 200K-tokens-per-day cap, see below) AND keeps newly-scraped
jobs matched going forward, with no separate manual re-triggering. Can
still be run manually the same way for a one-off check:

    python scripts/backfill_skills_matching.py

Interrupt-safe: only rows where matched_skills IS NULL are selected
(oldest job_postings.scraped_at first), so each day's run - whether it
finishes the current backlog or stops early on the daily budget (below)
- naturally picks up wherever the previous run left off, with no state
to track beyond the NULL columns themselves. A failed batch logs one
warning and leaves every row in that batch's two columns NULL - never
aborts the run. A result that looks like a full-resume dump instead of
genuine matching (huntloop.skills_matching.MAX_PLAUSIBLE_MATCHED_SKILLS
- see that module) is rejected the same way, logged and left NULL for
reprocessing rather than stored as-is. Deliberately does NOT use
reasoning_effort="low" - the measurement step found a real, repeatable
quality regression from it (soft/inferred matches getting dropped to
"missing"), not worth the marginal speed gain.

PACING - two distinct real limits, both respected explicitly here, not
left to the Groq SDK's retry-with-backoff (fine for a handful of calls,
not for ~121 batches):

1. Groq's rolling 8000-tokens-per-minute (TPM) cap for openai/gpt-oss-20b
   (confirmed from a real 429 response body in Step 4). Paced with a
   sliding 60-second token-budget window (TokenPacer, same design as the
   single-job version this replaces): before each batch, sum the
   estimated tokens of every batch sent in the trailing 60s, and sleep
   until there's room if adding this batch would exceed TARGET_TPM (set
   below the real 8000 cap to absorb estimation error).
2. Groq's 8000-token hard cap on a SINGLE request, confirmed by testing
   in the measurement step: a batch of 10 real jobs was rejected
   outright (413 Request too large) even though it was a single call,
   not a rate-limit/backoff situation - too-large requests aren't
   throttled, they're refused. Batches are built by _chunk_jobs() to
   respect MAX_BATCH_ESTIMATED_TOKENS (comfortably under 8000) AND
   MAX_BATCH_SIZE (5, the size quality was actually validated at) -
   whichever limit is hit first ends the current batch, so an unusually
   long run of large job descriptions produces smaller batches rather
   than risking a 413.

Token estimation reuses the ~4-chars-per-token heuristic and a
per-job completion-token estimate calibrated against real measured Groq
usage from the batch-of-5 test (completion=1810 for 5 jobs -> ~360/job,
see SESSIONS.md) - not a guess.

3. Groq's rolling 200,000-tokens-per-day (TPD) cap - a THIRD real limit,
   separate from both of the above, discovered only by actually running
   this script at real ~600-job scale (2026-08-22, see SESSIONS.md): the
   per-minute pacer above has no way to know a per-day budget exists
   until it's exhausted, and once it is, every subsequent call fails
   with 429 for the rest of the day regardless of per-minute pacing - a
   first real run hit this after ~33 minutes and then spent the
   remaining ~3 hours retrying uselessly against an exhausted daily
   budget. Fixed here: match_skills_batch() raises
   DailyQuotaExhausted specifically for this case (not swallowed into a
   None return like other failures), and main() stops the whole run on
   that signal - a full ~605-job backfill needs the daily 200K budget
   spread across multiple days (605 jobs x ~1118 tokens/job (batched) =~
   676K tokens total needed, ~3.4 days minimum at 200K/day), so this
   script is designed to be re-run once each day's budget frees up
   (interrupt-safe, same as any partial run) rather than run to
   completion in one sitting.
"""
import collections
import logging
import os
import sys
import time

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import JobPosting, ResumeVersion
from huntloop.settings import DATABASE_URL
from huntloop.skills_matching import (
    match_skills_batch,
    DailyQuotaExhausted,
    MODEL_NAME,
    _BATCH_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 5
MAX_BATCH_ESTIMATED_TOKENS = 7000  # safety margin below Groq's real 8000-token hard per-request cap
TARGET_TPM = 6000  # safety margin below Groq's real 8000 rolling-window TPM cap
CHARS_PER_TOKEN = 4
ESTIMATED_COMPLETION_TOKENS_PER_JOB = 360
SYSTEM_PROMPT_CHARS = len(_BATCH_SYSTEM_PROMPT)


def _estimate_job_contribution(job_description: str) -> int:
    return len(job_description or "") // CHARS_PER_TOKEN + ESTIMATED_COMPLETION_TOKENS_PER_JOB


def _estimate_batch_tokens(resume_chars: int, job_contributions: list[int]) -> int:
    base = resume_chars // CHARS_PER_TOKEN + SYSTEM_PROMPT_CHARS // CHARS_PER_TOKEN
    return base + sum(job_contributions)


def _chunk_jobs(jobs: list[JobPosting], resume_chars: int) -> list[list[JobPosting]]:
    """Group jobs into batches of at most MAX_BATCH_SIZE, ending a batch
    early if adding the next job would push its estimated token cost
    past MAX_BATCH_ESTIMATED_TOKENS - real job description sizes here
    range from ~370 to ~17,500 characters, and a fixed batch-of-5
    without size-awareness can exceed Groq's hard per-request cap for
    unusually large postings (confirmed in the measurement step)."""
    batches: list[list[JobPosting]] = []
    current: list[JobPosting] = []
    current_contributions: list[int] = []

    for job in jobs:
        contribution = _estimate_job_contribution(job.job_description or "")
        would_be = _estimate_batch_tokens(resume_chars, current_contributions + [contribution])

        if current and (len(current) >= MAX_BATCH_SIZE or would_be > MAX_BATCH_ESTIMATED_TOKENS):
            batches.append(current)
            current = []
            current_contributions = []

        current.append(job)
        current_contributions.append(contribution)

    if current:
        batches.append(current)

    return batches


class TokenPacer:
    """Sliding 60-second token-budget rate limiter (see PACING notes
    above)."""

    def __init__(self, target_tpm: int = TARGET_TPM):
        self.target_tpm = target_tpm
        self._window = collections.deque()  # (monotonic_timestamp, estimated_tokens)

    def _prune(self, now: float) -> None:
        while self._window and now - self._window[0][0] > 60:
            self._window.popleft()

    def wait_for_budget(self, estimated_tokens: int) -> None:
        estimated_tokens = min(estimated_tokens, self.target_tpm)

        while True:
            now = time.monotonic()
            self._prune(now)
            used = sum(tokens for _, tokens in self._window)
            if used + estimated_tokens <= self.target_tpm:
                self._window.append((now, estimated_tokens))
                return
            sleep_for = max(60 - (now - self._window[0][0]) + 0.5, 1.0)
            logger.info(
                f"Pacing: ~{used} estimated tokens used in trailing 60s "
                f"(target {self.target_tpm}) - sleeping {sleep_for:.1f}s"
            )
            time.sleep(sleep_for)


def main():
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    start = time.perf_counter()
    succeeded = 0
    failed = 0

    try:
        active_resume = session.query(ResumeVersion).filter_by(is_active=True).first()
        if active_resume is None:
            logger.error("No active resume_versions row - nothing to match against.")
            return
        resume_text = active_resume.extracted_text

        # Oldest-scraped-first: clears the longest-standing backlog before
        # newer arrivals, and gives deterministic day-to-day ordering
        # (id as a tiebreaker for rows scraped in the same run).
        jobs = (
            session.query(JobPosting)
            .filter(JobPosting.matched_skills.is_(None))
            .order_by(JobPosting.scraped_at.asc(), JobPosting.id.asc())
            .all()
        )
        total = len(jobs)
        batches = _chunk_jobs(jobs, len(resume_text))
        logger.info(
            f"Backfilling skills match for {total} job_postings rows in {len(batches)} batches "
            f"(model={MODEL_NAME}, max_batch_size={MAX_BATCH_SIZE}, target_tpm={TARGET_TPM})"
        )

        pacer = TokenPacer()
        processed = 0
        stopped_early = False

        for batch_num, batch in enumerate(batches, start=1):
            contributions = [_estimate_job_contribution(j.job_description or "") for j in batch]
            estimate = _estimate_batch_tokens(len(resume_text), contributions)
            pacer.wait_for_budget(estimate)

            descriptions = [j.job_description or "" for j in batch]
            try:
                results = match_skills_batch(resume_text, descriptions)
            except DailyQuotaExhausted as e:
                logger.error(
                    f"Groq's daily token quota is exhausted after {processed}/{total} jobs "
                    f"processed (batch {batch_num}/{len(batches)}) - stopping this run rather "
                    f"than retrying uselessly for hours. Re-run this script once the daily "
                    f"budget resets to pick up where it left off ({e})"
                )
                stopped_early = True
                break

            for job, result in zip(batch, results):
                processed += 1
                if result is None:
                    failed += 1
                    logger.warning(
                        f"[{processed}/{total}] Skills match failed for job_postings.id={job.id} "
                        f"({job.job_title!r}) - leaving matched_skills/missing_skills NULL"
                    )
                    continue

                job.matched_skills = result["matched_skills"]
                job.missing_skills = result["missing_skills"]
                succeeded += 1
                logger.info(
                    f"[{processed}/{total}] Stored skills match for job_postings.id={job.id} "
                    f"({job.job_title!r})"
                )

            session.commit()
            logger.info(f"Batch {batch_num}/{len(batches)} complete ({len(batch)} jobs)")
    finally:
        session.close()

    elapsed = time.perf_counter() - start
    status = "stopped early (daily quota exhausted)" if stopped_early else "complete"
    logger.info(
        f"Backfill {status}: {succeeded} succeeded, {failed} failed, "
        f"{succeeded + failed} total processed in {elapsed:.1f}s ({elapsed / 60:.1f} min)"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

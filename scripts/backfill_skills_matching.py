"""
Run skills matching (huntloop.skills_matching_router.match_skills_batch())
against job_postings rows that don't already have a result, and store
matched_skills/missing_skills.

Since 2026-08-22 this is a daily recurring stage of the cron orchestrator
(scripts/run_orchestrator_cron.sh), not a one-off manual backfill - it
runs automatically after main.py's scrape in every scheduled run, so it
both works through the existing backlog over multiple days AND keeps
newly-scraped jobs matched going forward, with no separate manual
re-triggering. Can still be run manually the same way for a one-off check:

    python scripts/backfill_skills_matching.py [--limit N]

Routing (Step K, 2026-08-29, see huntloop-architecture-decisions.md): the
router runs Groq primary, Gemini fallback. Batch size and TPM pacing are
whatever the *currently active* provider needs (Groq's tiny ~7k
per-request cap -> ~1-2 jobs/batch; Gemini's 16k -> ~5), re-read from the
router every batch so the switch is automatic when Groq's daily quota
runs out mid-run. The run stops only on AllProvidersExhausted (every
provider hit its daily wall) - same "re-run tomorrow, it's interrupt-safe"
behaviour Groq's DailyQuotaExhausted had before.

Only rows where **matched_skills IS NULL AND is_relevant IS TRUE** are
selected (oldest job_postings.scraped_at first) - Step K added the
is_relevant filter: at 380-company scale ~57% of NULL rows are postings
the relevance pre-filter already flagged as not-technical, and spending
scarce Groq/Gemini quota on them was pure waste. Interrupt-safe: each
day's run - whether it finishes the current backlog or stops early on the
daily budget - naturally picks up wherever the previous run left off,
with no state to track beyond the NULL columns themselves. A failed batch
logs one
warning and leaves every row in that batch's two columns NULL - never
aborts the run. A result that looks like a full-resume dump instead of
genuine matching (huntloop.skills_matching.MAX_PLAUSIBLE_MATCHED_SKILLS
- see that module) is rejected the same way, logged and left NULL for
reprocessing rather than stored as-is. Deliberately does NOT use
reasoning_effort="low" - the measurement step found a real, repeatable
quality regression from it (soft/inferred matches getting dropped to
"missing"), not worth the marginal speed gain.

PACING - the per-request size cap and TPM target are now PER-PROVIDER,
read from huntloop.skills_matching_router.batch_limits() before every
batch (so they flip automatically when the router fails over from Groq to
Gemini mid-run):

1. TPM + RPM: a sliding 60-second window (TokenPacer) sleeps before a
   batch if the trailing-60s estimate would exceed the active provider's
   target TPM (Groq 6,000 / Gemini 200,000) OR its request cap (Groq 30 /
   Gemini 14, under the real 15). For Groq the TPM bound dominates; for
   Gemini's small ~13k-token batches the RPM bound is what actually
   holds it - without it the loop would run at ~30 req/min, over Gemini's
   15 RPM limit.
2. Per-request size: batches are built greedily up to the active
   provider's MAX_BATCH_ESTIMATED_TOKENS (Groq ~7,000, an artifact of
   its real 8,000-token hard per-request refusal; Gemini 16,000, derived
   from 250K TPM / 15 RPM) AND MAX_BATCH_SIZE (5). At the ~9.5k-char job
   descriptions common at 380-company scale, Groq batches end up ~1-2
   jobs, Gemini ~5 - that's real, not a bug.
3. Per-day: a provider raising DailyQuotaExhausted is marked spent for
   the run by the router and every further batch goes to the next
   provider; the run stops only on AllProvidersExhausted. Re-run once the
   quota windows reset - interrupt-safe, same as any partial run.

Token estimation reuses the ~4-chars-per-token heuristic and a per-job
completion-token estimate (~360/job) calibrated against real measured
Groq usage (see SESSIONS.md 2026-08-22) - not a guess.
"""
import argparse
import collections
import contextlib
import logging
import os
import sys
import time

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import JobPosting, ResumeVersion
from huntloop.settings import DATABASE_URL
from huntloop import skills_matching_router as router

logger = logging.getLogger(__name__)

# Postgres session-level advisory lock guarding against two backfill runs
# at once. The daily scheduled run (scripts/run_orchestrator_cron.sh
# stage 2) and an ad hoc manual run have already collided in production
# (2026-08-30, see SESSIONS.md) - both read the same
# `matched_skills IS NULL AND is_relevant IS TRUE` list oldest-first and
# raced the same rows, double-processing ~700 of them and burning real
# Groq/Gemini quota for nothing. A second invocation now detects the held
# lock and exits cleanly (return, not raise) before touching anything.
# The lock is held on a dedicated AUTOCOMMIT connection for the whole run
# and is auto-released by Postgres if the process dies (session-level
# advisory locks survive transaction rollback but not connection loss).
# Key: b"hlsm" as a big-endian int - stable, project-specific, unlikely
# to collide with anything else using advisory locks on this database.
_LOCK_KEY = int.from_bytes(b"hlsm", "big")  # 0x686c736d == 1_751_937_901

# --- Stale-lock detection (added 2026-08-31, see SESSIONS.md) ------------
# The lock guard above works when a second run is *alive* and blocked
# cleanly. The real gap: a run can die abnormally (an orphaned
# launchd-spawned child whose parent job exited but the child kept
# running) and sit holding the lock for hours on an idle DB connection,
# with nothing detecting it - this happened in production on 2026-08-31
# and needed a manual SIGTERM after the fact. So when acquisition fails,
# we now inspect the current holder and, if it's been holding far longer
# than any real run ever has, log a CRITICAL (distinct from the ordinary
# "already locked, skipping" WARNING) with everything a human needs to
# decide what to do.
#
# We deliberately DO NOT terminate the backend or force-unlock from here:
# killing a DB connection on a heuristic can compound an incident, and
# this project has a standing rule (from the 2026-08-22 data-wipe
# incident) against unverified automated DB operations. Clearing a stale
# holder is a separate, human-invoked action - scripts/check_lock_staleness.py.
#
# Threshold: 3x the longest real full-backlog run ever logged - 13,373s
# (~3.7h), SESSIONS.md 2026-08-22. No healthy run has ever come close to
# 3.7h; 3x leaves room for a pathologically slow-but-real run (heavy
# Gemini cold-start bursts, an unusually large backlog) while staying
# well under the 24h daily cadence, so a genuine orphan is always caught
# within one day's run.
_LONGEST_OBSERVED_RUN_SECONDS = 13_373  # 222.9 min ~= 3.7h (SESSIONS.md 2026-08-22)
# 3x the longest real run ~= 11.1h. Override with HUNTLOOP_LOCK_STALE_SECONDS
# (operationally useful for tuning without a code change; also how the
# verification runs exercise the CRITICAL path without waiting 11 hours).
STALE_LOCK_THRESHOLD_SECONDS = int(
    os.environ.get("HUNTLOOP_LOCK_STALE_SECONDS", 3 * _LONGEST_OBSERVED_RUN_SECONDS)
)  # default 40_119s ~= 11.1h

# Holder lookup: pg_locks (advisory, granted, our key) joined to
# pg_stat_activity. The single-arg pg_advisory_lock(bigint) form splits
# the key across (classid, objid) - recombined here the same way
# tests/test_backfill_lock.py does. `state_change` is when the holder
# last changed state; for this lock that's ~when it acquired the lock and
# went idle (the run does its real work on a *different* connection), so
# `now() - state_change` is a good proxy for how long the lock's been held.
_LOCK_HOLDER_QUERY = text(
    """
    SELECT a.pid,
           a.state,
           a.state_change,
           a.query_start,
           a.backend_start,
           a.xact_start,
           a.wait_event_type,
           a.application_name,
           a.client_addr,
           a.query AS last_query,
           EXTRACT(EPOCH FROM (now() - a.state_change))  AS held_seconds,
           EXTRACT(EPOCH FROM (now() - a.backend_start))  AS backend_age_seconds
    FROM pg_locks l
    JOIN pg_stat_activity a ON a.pid = l.pid
    WHERE l.locktype = 'advisory'
      AND l.granted
      AND ((l.classid::bigint << 32) | l.objid::bigint) = :k
    """
)


def lock_holder_info(engine):
    """Return a row describing the current advisory-lock holder (pid,
    state, timestamps, held duration, last query), or None if the lock
    isn't held / the holding backend has already gone. Shared by the
    in-run staleness check below and scripts/check_lock_staleness.py."""
    with engine.connect() as conn:
        return conn.execute(_LOCK_HOLDER_QUERY, {"k": _LOCK_KEY}).first()


def _fmt_duration(seconds: float) -> str:
    seconds = seconds or 0
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.2f}h"


def _held_seconds(row) -> float:
    """How long the lock's been held - state_change (~lock acquisition,
    since the holder goes idle right after) with backend_start as fallback."""
    return row.held_seconds if row.held_seconds is not None else (row.backend_age_seconds or 0)


def _ts(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S %Z") if value is not None else "?"


def _format_holder(row) -> str:
    return (
        f"pid={row.pid} state={row.state!r} "
        f"held={_fmt_duration(_held_seconds(row))} (state_change {_ts(row.state_change)}) "
        f"backend_age={_fmt_duration(row.backend_age_seconds)} (backend_start {_ts(row.backend_start)}) "
        f"query_start={_ts(row.query_start)} "
        f"wait_event_type={row.wait_event_type} "
        f"application_name={row.application_name!r} client_addr={row.client_addr} "
        f"last_query={row.last_query!r}"
    )


def check_lock_holder_staleness(engine, threshold_seconds: int = STALE_LOCK_THRESHOLD_SECONDS) -> None:
    """Inspect whoever currently holds the advisory lock and, if they've
    held it past `threshold_seconds`, log a CRITICAL alert (distinct from
    the ordinary 'already locked, skipping' WARNING). Never terminates
    anything. Best-effort: any inspection failure is logged and swallowed
    so it can't turn a benign concurrent run into a crash."""
    try:
        row = lock_holder_info(engine)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not inspect advisory-lock holder (key %s): %s", _LOCK_KEY, exc)
        return

    try:
        _emit_staleness(row, threshold_seconds)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Advisory-lock staleness check failed (key %s): %s", _LOCK_KEY, exc)


def _emit_staleness(row, threshold_seconds: int) -> None:
    if row is None:
        logger.info(
            "Advisory lock %s reported busy but no live holder is in pg_stat_activity "
            "- the holder likely just exited; the next run should acquire it cleanly.",
            _LOCK_KEY,
        )
        return

    held_seconds = _held_seconds(row)
    if held_seconds >= threshold_seconds:
        logger.critical(
            "STALE ADVISORY LOCK ALERT: the backfill_skills_matching single-instance "
            "lock (key %s) has been held for %s, past the %s staleness threshold "
            "(3x the longest real full-backlog run, ~3.7h). The holder is very likely an "
            "orphaned/hung run, not a healthy concurrent one. NO automated action has "
            "been taken (standing rule: no unverified automated DB operations). To "
            "inspect and, after confirmation, terminate it, run: "
            "`PYTHONPATH=src python scripts/check_lock_staleness.py --terminate`. "
            "Holder: %s",
            _LOCK_KEY, _fmt_duration(held_seconds), _fmt_duration(threshold_seconds),
            _format_holder(row),
        )
    else:
        # Under threshold: a healthy concurrent run. Stay quiet at INFO so
        # the only line an ordinary overlap produces is the existing
        # "already locked, skipping" WARNING - no false alert.
        logger.debug(
            "Advisory lock %s held %s by pid %s, under the %s threshold - healthy.",
            _LOCK_KEY, _fmt_duration(held_seconds), row.pid, _fmt_duration(threshold_seconds),
        )


@contextlib.contextmanager
def _backfill_lock(engine):
    """Yield True if this process got the advisory lock, False if another
    backfill instance already holds it. Always releases on exit."""
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    got = False
    try:
        got = conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}
        ).scalar()
        yield bool(got)
    finally:
        if got:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
        conn.close()

MODEL_NAME = router.MODEL_NAME
CHARS_PER_TOKEN = 4
ESTIMATED_COMPLETION_TOKENS_PER_JOB = 360
SYSTEM_PROMPT_CHARS = len(router._BATCH_SYSTEM_PROMPT)


def _estimate_job_contribution(job_description: str) -> int:
    return len(job_description or "") // CHARS_PER_TOKEN + ESTIMATED_COMPLETION_TOKENS_PER_JOB


def _estimate_batch_tokens(resume_chars: int, job_contributions: list[int]) -> int:
    base = resume_chars // CHARS_PER_TOKEN + SYSTEM_PROMPT_CHARS // CHARS_PER_TOKEN
    return base + sum(job_contributions)


def _next_batch(jobs: list[JobPosting], start_idx: int, resume_chars: int,
                max_size: int, max_tokens: int) -> list[JobPosting]:
    """Greedily take the next batch from jobs[start_idx:], stopping at
    max_size jobs or when adding the next job would push the estimated
    request cost past max_tokens (always takes at least one job). Called
    once per batch so max_size/max_tokens can be the *current* active
    provider's limits - they flip mid-run when the router fails over."""
    batch: list[JobPosting] = []
    contributions: list[int] = []
    for job in jobs[start_idx:]:
        contribution = _estimate_job_contribution(job.job_description or "")
        if batch and (
            len(batch) >= max_size
            or _estimate_batch_tokens(resume_chars, contributions + [contribution]) > max_tokens
        ):
            break
        batch.append(job)
        contributions.append(contribution)
    return batch


class TokenPacer:
    """Sliding 60-second rate limiter on BOTH estimated tokens/min and
    requests/min (see PACING notes above). `target_tpm` / `max_rpm` are
    mutable - the caller sets them to the active provider's limits before
    each batch, so they flip on a mid-run provider switch (Groq
    6,000 TPM / 30 RPM -> Gemini 200,000 TPM / 14 RPM; for Groq the TPM
    bound dominates, for Gemini's small batches the RPM bound does)."""

    def __init__(self, target_tpm: int, max_rpm: int):
        self.target_tpm = target_tpm
        self.max_rpm = max_rpm
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
            token_ok = used + estimated_tokens <= self.target_tpm
            rpm_ok = len(self._window) < self.max_rpm
            if token_ok and rpm_ok:
                self._window.append((now, estimated_tokens))
                return
            sleep_for = max(60 - (now - self._window[0][0]) + 0.5, 1.0)
            reason = "tokens" if not token_ok else "requests"
            logger.info(
                "Pacing (%s): ~%d tok / %d req in trailing 60s "
                "(targets %d tok, %d req) - sleeping %.1fs",
                reason, used, len(self._window), self.target_tpm, self.max_rpm, sleep_for,
            )
            time.sleep(sleep_for)


def _log_backlog(session) -> int:
    """Log the skills-matching backlog: relevant job_postings rows with no
    result yet. This one INFO line per run (the backfill runs daily via
    the orchestrator) is the cheap leading indicator for a future
    capacity regression - Gemini's own free-tier limits already changed
    once mid-project without warning, and re-deriving the whole volume
    analysis to notice is expensive. Returns the count."""
    relevant_backlog = (
        session.query(JobPosting)
        .filter(JobPosting.matched_skills.is_(None), JobPosting.is_relevant.is_(True))
        .count()
    )
    skipped_irrelevant = (
        session.query(JobPosting)
        .filter(JobPosting.matched_skills.is_(None), JobPosting.is_relevant.isnot(True))
        .count()
    )
    logger.info(
        "skills-matching backlog: %d relevant rows awaiting a result "
        "(%d non-relevant NULL rows deliberately skipped)",
        relevant_backlog, skipped_irrelevant,
    )
    return relevant_backlog


def main(limit: int | None = None):
    """Acquire the single-instance advisory lock, then run the backfill.
    A second concurrent invocation logs one clear line and returns 0
    without racing the first."""
    lock_engine = create_engine(DATABASE_URL, echo=False)
    try:
        with _backfill_lock(lock_engine) as got_lock:
            if not got_lock:
                logger.warning(
                    "Another backfill_skills_matching instance already holds the "
                    "advisory lock (key %s) - exiting immediately without processing "
                    "anything. This is the concurrency guard working as intended "
                    "(e.g. the daily scheduled run overlapping a manual run); "
                    "nothing raced and no Groq/Gemini quota was spent.",
                    _LOCK_KEY,
                )
                # ...but a "held" lock can also mean a run died holding it.
                # Inspect the holder; CRITICAL if it's been held longer
                # than any real run ever has. Runs on the daily
                # orchestrator path (this script is stage 2 of
                # run_orchestrator_cron.sh), so a future orphan surfaces
                # in the normal run logs instead of needing someone to
                # spot a stuck process by hand.
                check_lock_holder_staleness(lock_engine)
                return
            logger.info("Acquired backfill single-instance lock (key %s)", _LOCK_KEY)
            _run_backfill(limit=limit)
    finally:
        lock_engine.dispose()


def _run_backfill(limit: int | None = None):
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    start = time.perf_counter()
    succeeded = 0
    failed = 0
    state = router.make_run_state()
    stopped_early = False

    try:
        active_resume = session.query(ResumeVersion).filter_by(is_active=True).first()
        if active_resume is None:
            logger.error("No active resume_versions row - nothing to match against.")
            return
        resume_text = active_resume.extracted_text
        resume_chars = len(resume_text)

        _log_backlog(session)

        # Oldest-scraped-first: clears the longest-standing backlog before
        # newer arrivals, deterministic day-to-day ordering (id tiebreak).
        # is_relevant filter (Step K): don't spend scarce quota on
        # postings the relevance pre-filter already flagged as not-technical.
        jobs = (
            session.query(JobPosting)
            .filter(JobPosting.matched_skills.is_(None), JobPosting.is_relevant.is_(True))
            .order_by(JobPosting.scraped_at.asc(), JobPosting.id.asc())
            .all()
        )
        if limit is not None:
            jobs = jobs[:limit]
        total = len(jobs)
        logger.info(
            "Backfilling skills match for %d relevant job_postings rows "
            "(providers=%s, primary model=%s)%s",
            total, ",".join(router.PROVIDER_CHAIN), MODEL_NAME,
            f" [--limit {limit}]" if limit is not None else "",
        )

        _tpm0, _rpm0 = router.batch_limits(state)[2], router.batch_limits(state)[3]
        pacer = TokenPacer(target_tpm=_tpm0, max_rpm=_rpm0)
        idx = 0
        processed = 0
        batch_num = 0

        while idx < total:
            try:
                max_size, max_tokens, target_tpm, max_rpm = router.batch_limits(state)
            except router.AllProvidersExhausted as e:
                logger.error(
                    "All providers exhausted after %d/%d jobs - stopping this run. "
                    "Re-run once the daily quota windows reset (%s)", processed, total, e,
                )
                stopped_early = True
                break

            batch = _next_batch(jobs, idx, resume_chars, max_size, max_tokens)
            batch_num += 1
            idx += len(batch)

            contributions = [_estimate_job_contribution(j.job_description or "") for j in batch]
            pacer.target_tpm = target_tpm
            pacer.max_rpm = max_rpm
            pacer.wait_for_budget(_estimate_batch_tokens(resume_chars, contributions))

            descriptions = [j.job_description or "" for j in batch]
            try:
                results = router.match_skills_batch(resume_text, descriptions, state)
            except router.AllProvidersExhausted as e:
                logger.error(
                    "All providers exhausted mid-batch after %d/%d jobs - stopping this run. "
                    "Re-run once the daily quota windows reset (%s)", processed, total, e,
                )
                stopped_early = True
                break

            for job, result in zip(batch, results):
                processed += 1
                if result is None:
                    failed += 1
                    logger.warning(
                        "[%d/%d] Skills match failed for job_postings.id=%s (%r) - leaving NULL",
                        processed, total, job.id, job.job_title,
                    )
                    continue
                job.matched_skills = result["matched_skills"]
                job.missing_skills = result["missing_skills"]
                succeeded += 1
                logger.info(
                    "[%d/%d] Stored skills match for job_postings.id=%s (%r)",
                    processed, total, job.id, job.job_title,
                )

            session.commit()
            logger.info(
                "Batch %d complete (%d jobs via %s, batch cap %d/%dtok)",
                batch_num, len(batch), state["last_provider"] or "none", max_size, max_tokens,
            )
    finally:
        session.close()

    elapsed = time.perf_counter() - start
    status = "stopped early (all providers exhausted)" if stopped_early else "complete"
    logger.info(
        "Backfill %s: %d succeeded, %d failed, %d processed in %.1fs (%.1f min). Routing: %s",
        status, succeeded, failed, succeeded + failed, elapsed, elapsed / 60,
        router.run_summary(state),
    )
    # Leading-indicator line again at the end - the backlog after this run.
    session = Session()
    try:
        _log_backlog(session)
    finally:
        session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None,
                    help="process at most N jobs this run (for bounded verification runs)")
    args = ap.parse_args()
    main(limit=args.limit)

#!/bin/bash
# Cron wrapper for HuntLoop's daily scheduled work: the multi-ATS scraper
# orchestrator (main.py) followed by the skills-matching backfill stage
# (scripts/backfill_skills_matching.py, added as a cron stage 2026-08-22
# - see SESSIONS.md). Both stages always run, regardless of whether the
# other succeeded - a scraping hiccup shouldn't stall skills-matching
# progress on the existing backlog, and vice versa.
#
# Not run directly by developers - `python main.py` /
# `python scripts/backfill_skills_matching.py` are still how you run
# either stage manually. This wrapper exists only so cron has a stable
# entrypoint that: cd's into the repo (cron's working directory is
# otherwise unpredictable), uses the same .venv Python and .env config as
# a manual run (both stages load .env themselves; nothing here
# duplicates DATABASE_URL, GROQ_API_KEY, or any other credential), and
# records a start/exit-code/end marker per stage in logs/cron.log so a
# scheduled run's outcome is visible after the fact without watching it
# live. Per-run detail (INFO/WARNING/ERROR lines, which companies were
# scraped or skipped, which jobs got a skills match or were rejected by
# the sanity filter, tracebacks) still goes to the app's own rotating
# logs/huntloop.log via huntloop.logging_config.setup_logging() - this
# script does not duplicate that, it just also captures each stage's
# stdout/stderr into cron.log so wrapper-level failures (e.g. a missing
# .venv) are visible too, not just app-level ones.
#
# The skills-matching stage is expected to often stop early (not
# complete every NULL row) once the day's 200K-token Groq budget is
# exhausted (huntloop.skills_matching.DailyQuotaExhausted) - that's
# normal, not a failure; it picks up automatically where it left off on
# tomorrow's run, working through the backlog and keeping newly-scraped
# jobs matched over time, with no separate manual re-triggering needed.
#
# See README.md's "Scheduled runs" section for how to install/remove the
# crontab entry that calls this script, and where to check its output.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p "$REPO_ROOT/logs"
CRON_LOG="$REPO_ROOT/logs/cron.log"

PYTHON="$REPO_ROOT/.venv/bin/python"

{
  echo "=== HuntLoop scheduled run started $(date '+%Y-%m-%d %H:%M:%S %Z') ==="

  if [ ! -x "$PYTHON" ]; then
    echo "ERROR: $PYTHON not found or not executable - .venv missing or not set up. Aborting."
    echo "=== run finished with exit code 1 at $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
    exit 1
  fi

  echo "--- stage 1/2: scraper orchestrator (main.py) ---"
  "$PYTHON" "$REPO_ROOT/main.py"
  scrape_status=$?
  echo "--- stage 1/2 finished with exit code $scrape_status ---"

  echo "--- stage 2/2: skills-matching backfill (scripts/backfill_skills_matching.py) ---"
  "$PYTHON" "$REPO_ROOT/scripts/backfill_skills_matching.py"
  skills_status=$?
  echo "--- stage 2/2 finished with exit code $skills_status ---"

  status=$scrape_status
  if [ "$skills_status" -ne 0 ]; then
    status=$skills_status
  fi

  echo "=== run finished with exit code $status (scrape=$scrape_status, skills=$skills_status) at $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  exit "$status"
} >> "$CRON_LOG" 2>&1

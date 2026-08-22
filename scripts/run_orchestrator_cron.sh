#!/bin/bash
# Cron wrapper for HuntLoop's multi-ATS orchestrator (main.py).
#
# Not run directly by developers - `python main.py` is still how you run
# the orchestrator manually. This wrapper exists only so cron has a stable
# entrypoint that: cd's into the repo (cron's working directory is
# otherwise unpredictable), uses the same .venv Python and .env config as
# a manual run (main.py -> huntloop.settings loads .env itself; nothing
# here duplicates DATABASE_URL or any credential), and records a
# start/exit-code/end marker in logs/cron.log so a scheduled run's outcome
# is visible after the fact without watching it live. Per-run detail
# (INFO/WARNING/ERROR lines, which companies were scraped or skipped,
# tracebacks) still goes to the app's own rotating logs/huntloop.log via
# huntloop.logging_config.setup_logging() - this script does not
# duplicate that, it just also captures main.py's stdout/stderr into
# cron.log so wrapper-level failures (e.g. a missing .venv) are visible
# too, not just app-level ones.
#
# See README.md's "Scheduled runs" section for how to install/remove the
# crontab entry that calls this script, and where to check its output.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p "$REPO_ROOT/logs"
CRON_LOG="$REPO_ROOT/logs/cron.log"

PYTHON="$REPO_ROOT/.venv/bin/python"

{
  echo "=== HuntLoop scheduled orchestrator run started $(date '+%Y-%m-%d %H:%M:%S %Z') ==="

  if [ ! -x "$PYTHON" ]; then
    echo "ERROR: $PYTHON not found or not executable - .venv missing or not set up. Aborting."
    echo "=== run finished with exit code 1 at $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
    exit 1
  fi

  "$PYTHON" "$REPO_ROOT/main.py"
  status=$?

  echo "=== run finished with exit code $status at $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  exit "$status"
} >> "$CRON_LOG" 2>&1

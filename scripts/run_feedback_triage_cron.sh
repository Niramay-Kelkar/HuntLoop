#!/bin/bash
# launchd wrapper for the feedback-triage pass (scripts/triage_feedback.py).
#
# A SEPARATE, more-frequent schedule from scripts/run_orchestrator_cron.sh
# (the once-daily scrape/skills-matching/department-categorization run) -
# feedback can arrive from a public visitor at any time, so triage runs
# every few minutes via its own launchd job
# (com.huntloop.feedback-triage, StartInterval, see
# scripts/com.huntloop.feedback-triage.plist), not bundled into the
# once-daily orchestrator.
#
# This stage has no torch dependency (triage_feedback.py only talks to
# Groq/Gemini over HTTP, same as the skills-matching backfill), so it
# runs via the local .venv, same as that stage - no Docker needed.
#
# Single-instance guard: a plain flock(2) on logs/.feedback_triage.lock,
# same lockf(1) mechanism run_orchestrator_cron.sh uses for the
# orchestrator - a run that's still going (slow LLM calls, a large
# pending backlog) must never overlap a second firing a few minutes
# later. -t 0 fails immediately (not blocking) if already held; that's
# expected (not an error) and logs one line.
#
# Install:
#   cp scripts/com.huntloop.feedback-triage.plist ~/Library/LaunchAgents/
#   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.huntloop.feedback-triage.plist
# Remove:
#   launchctl bootout gui/$(id -u)/com.huntloop.feedback-triage
#   rm ~/Library/LaunchAgents/com.huntloop.feedback-triage.plist

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p "$REPO_ROOT/logs"
LOG_FILE="$REPO_ROOT/logs/feedback_triage.log"
LOCK_FILE="$REPO_ROOT/logs/.feedback_triage.lock"

if [ -z "${HUNTLOOP_TRIAGE_LOCKED:-}" ]; then
  export HUNTLOOP_TRIAGE_LOCKED=1
  SELF="$REPO_ROOT/scripts/$(basename "${BASH_SOURCE[0]}")"
  /usr/bin/lockf -s -t 0 -k "$LOCK_FILE" "$SELF" "$@"
  rc=$?
  if [ "$rc" -eq 75 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S %Z'): feedback triage already running (lock held) - this invocation exits without running." >> "$LOG_FILE"
    exit 0
  fi
  exit "$rc"
fi

PYTHON="$REPO_ROOT/.venv/bin/python"

{
  echo "=== feedback triage run started $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  if [ ! -x "$PYTHON" ]; then
    echo "ERROR: $PYTHON not found or not executable - .venv missing or not set up. Aborting."
    echo "=== run finished with exit code 1 at $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
    exit 1
  fi
  "$PYTHON" "$REPO_ROOT/scripts/triage_feedback.py"
  status=$?
  echo "=== feedback triage run finished with exit code $status at $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  exit "$status"
} >> "$LOG_FILE" 2>&1

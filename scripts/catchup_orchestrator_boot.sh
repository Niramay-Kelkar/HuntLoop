#!/bin/bash
# Boot/login catch-up for the daily scheduled orchestrator run.
#
# WHY THIS EXISTS: com.huntloop.scraper fires scripts/run_orchestrator_cron.sh
# once a day via launchd StartCalendarInterval (Hour=3). launchd re-runs a
# StartCalendarInterval firing that was missed while the machine was
# ASLEEP, on the next wake - but NOT one missed while the machine was
# fully OFF / rebooted through 3am. On 2026-09-06 the machine was rebooted
# overnight and the whole day's run was silently skipped (see SESSIONS.md
# "Deferred verification..." and this file's companion entry). This script
# is invoked by a SEPARATE launchd job, com.huntloop.scraper-catchup, with
# RunAtLoad=true and NO StartCalendarInterval, so it runs shortly after
# every boot/login. If today's scheduled run has not already happened, it
# triggers one; otherwise it is a no-op.
#
# SAFETY / no double-run:
#   1. Marker check - scripts/run_orchestrator_cron.sh writes
#      logs/last_scheduled_run.txt (dated) the moment it acquires its
#      lock. If that marker is dated today, a run already happened (3am
#      firing, a sleep-wake catch-up, or an earlier login catch-up) and
#      this exits without doing anything.
#   2. Schedule-window check - if the current local time is within
#      [SKIP_WINDOW_START, SKIP_WINDOW_END] (around the 3am slot), the
#      normal com.huntloop.scraper firing is imminent or just happened,
#      so this defers to it rather than racing.
#   3. Hard lock - if it does trigger, it calls the SAME
#      run_orchestrator_cron.sh, which re-execs itself under lockf(1). A
#      real 3am firing and this catch-up therefore physically cannot run
#      the orchestrator concurrently: whichever gets the flock first
#      runs, the other logs one line and exits 0.
#
# This is local-machine automation only; it does not replace real
# deployment scheduling (GitHub Actions against a hosted DB), still a
# separate later step.
#
# Test hooks (env overrides, unset in normal operation):
#   HUNTLOOP_CATCHUP_WRAPPER      - path to run instead of run_orchestrator_cron.sh
#   HUNTLOOP_CATCHUP_MARKER       - path to the last-run marker file
#   HUNTLOOP_CATCHUP_LOG          - path to this script's own log
#   HUNTLOOP_CATCHUP_NOW_HM       - override "now" as HHMM (e.g. 0300)
#   HUNTLOOP_CATCHUP_TODAY        - override "today" as YYYY-MM-DD
#   HUNTLOOP_CATCHUP_SKIP_START / _SKIP_END - override the skip window (HHMM)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
mkdir -p "$REPO_ROOT/logs"

CATCHUP_LOG="${HUNTLOOP_CATCHUP_LOG:-$REPO_ROOT/logs/catchup.log}"
LAST_RUN_MARKER="${HUNTLOOP_CATCHUP_MARKER:-$REPO_ROOT/logs/last_scheduled_run.txt}"
CRON_WRAPPER="${HUNTLOOP_CATCHUP_WRAPPER:-$REPO_ROOT/scripts/run_orchestrator_cron.sh}"

# Skip window around the normal 3am StartCalendarInterval firing, HHMM
# local. Any boot/login from midnight through the end of this window
# defers to com.huntloop.scraper: if the machine stays up it fires at
# 3am normally, and if it goes down again before 3am, the *next*
# boot/login's catch-up handles the miss. The only residual exposure is
# a boot inside [03:00, SKIP_WINDOW_END] on a day the 3am firing was
# genuinely missed - recovered at the next boot/login or the next 3am.
# The lock in run_orchestrator_cron.sh is the real anti-double-run
# guarantee; this window is only an optimisation to avoid a pointless
# catch-up run minutes before the scheduled one.
SKIP_WINDOW_START="${HUNTLOOP_CATCHUP_SKIP_START:-0000}"
SKIP_WINDOW_END="${HUNTLOOP_CATCHUP_SKIP_END:-0330}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S %Z'): $*" >> "$CATCHUP_LOG"; }

today="${HUNTLOOP_CATCHUP_TODAY:-$(date '+%Y-%m-%d')}"
now_hm_raw="${HUNTLOOP_CATCHUP_NOW_HM:-$(date '+%H%M')}"
now_hm=$((10#$now_hm_raw))            # 10# so "0300" is decimal 300, not bad octal
skip_start=$((10#$SKIP_WINDOW_START))
skip_end=$((10#$SKIP_WINDOW_END))

log "catch-up check: today=$today now=$now_hm_raw marker=$LAST_RUN_MARKER"

# 1. Did today's scheduled run already happen?
if [ -f "$LAST_RUN_MARKER" ]; then
  last_run_date="$(head -c 10 "$LAST_RUN_MARKER" 2>/dev/null)"
  if [ "$last_run_date" = "$today" ]; then
    log "today's scheduled run already recorded ($last_run_date) - nothing to catch up."
    exit 0
  fi
  log "last recorded scheduled run was '$last_run_date', not today - a run may have been missed."
else
  log "no last-run marker present - treating today's run as not yet done."
fi

# 2. Are we within the normal 3am schedule window? If so the
#    StartCalendarInterval job will handle it - don't race it.
if [ "$now_hm" -ge "$skip_start" ] && [ "$now_hm" -le "$skip_end" ]; then
  log "current time $now_hm_raw is within the normal schedule window ($SKIP_WINDOW_START-$SKIP_WINDOW_END) - deferring to com.huntloop.scraper."
  exit 0
fi

# 3. Trigger. run_orchestrator_cron.sh self-locks (lockf), so this can
#    never run the orchestrator concurrently with a real 3am firing.
log "triggering a catch-up run of $CRON_WRAPPER for the missed $today firing."
"$CRON_WRAPPER"
rc=$?
log "catch-up run finished with exit code $rc."
exit "$rc"

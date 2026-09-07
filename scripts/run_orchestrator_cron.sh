#!/bin/bash
# launchd/cron wrapper for HuntLoop's daily scheduled work: the multi-ATS
# scraper orchestrator (main.py), then the skills-matching backfill stage
# (scripts/backfill_skills_matching.py, added 2026-08-22), then the
# department-categorization backfill stage
# (scripts/backfill_department_category.py, added 2026-09-07 - see
# SESSIONS.md). All stages always run, regardless of whether the others
# succeeded - a scraping hiccup shouldn't stall skills-matching progress
# on the existing backlog, and vice versa.
#
# Stage 1 (the scraper) runs via `docker compose run` (the `app` image),
# not the local .venv Python - as of 2026-08-24 (see SESSIONS.md's "Move
# the daily scrape to run via Docker" entry). Reason: stage 1 now
# classifies every newly-inserted job_postings row's is_relevant at
# insert time (huntloop.relevance_filter, see CLAUDE.md), which needs
# sentence-transformers/torch - and this machine's local .venv genuinely
# cannot run torch (macOS Intel + Python 3.13, no compatible wheel - see
# CLAUDE.md), same constraint as every other embedding-dependent script
# in this project. Stage 2 (skills-matching) only talks to Groq over
# HTTP, no torch dependency, so it's deliberately left running via the
# local .venv exactly as before - no reason to move it into Docker.
#
# `--build` is passed so a scheduled run always reflects whatever's
# currently on disk, rather than silently running a stale image from
# whenever `docker compose build app` was last run manually - this is
# now the ongoing production path for the scraper, not a one-off manual
# invocation, so staleness would otherwise be invisible.
#
# DATABASE_URL is explicitly overridden to point at
# host.docker.internal instead of docker-compose's own `db` service
# (which docker-compose.yml points `app` at by default, a separate,
# smaller instance - see CLAUDE.md's two-Postgres-instances note) -
# derived from .env's real DATABASE_URL (same real local system Postgres
# every manual run and the old .venv-based stage 1 used) by swapping
# `localhost` for `host.docker.internal`, the same substitution
# documented in scripts/backfill_embeddings.py and used for every prior
# manual `docker compose run` against this Postgres.
#
# PATH is extended with /usr/local/bin (where Docker Desktop's `docker`
# CLI is symlinked on this machine) because launchd does NOT inherit an
# interactive shell's PATH - confirmed directly via `launchctl print`,
# which shows launchd's own default PATH as only
# `/usr/bin:/bin:/usr/sbin:/sbin` for this job, missing /usr/local/bin
# entirely. Without this, `docker`/`docker compose` would not be found
# at all when launchd fires this script - the earlier .venv/torch
# question would never even be reached.
#
# Not run directly by developers - `python main.py` /
# `python scripts/backfill_skills_matching.py` are still how you run
# either stage manually (main.py still works locally too, it just can't
# classify is_relevant without torch - see huntloop.pipelines). This
# wrapper exists only so cron/launchd has a stable entrypoint that: cd's
# into the repo (working directory is otherwise unpredictable), uses the
# same .env config as a manual run (both stages load .env themselves;
# nothing here duplicates DATABASE_URL, GROQ_API_KEY, or any other
# credential beyond the one explicit Docker-networking override above),
# and records a start/exit-code/end marker per stage in logs/cron.log so
# a scheduled run's outcome is visible after the fact without watching
# it live. Per-run detail (INFO/WARNING/ERROR lines, which companies
# were scraped or skipped, which jobs got a skills match or were
# rejected by the sanity filter, tracebacks) still goes to the app's own
# rotating logs/huntloop.log via huntloop.logging_config.setup_logging()
# - this script does not duplicate that, it just also captures each
# stage's stdout/stderr into cron.log so wrapper-level failures (e.g.
# Docker Desktop not running) are visible too, not just app-level ones.
#
# The skills-matching stage runs Groq primary -> Gemini fallback
# (huntloop.skills_matching_router, Step K). It is expected to often stop
# early (not complete every NULL row) once BOTH providers' daily quotas
# are spent (router.AllProvidersExhausted: Groq's 200K TPD, then Gemini's
# 500 RPD) - that's normal, not a failure; it picks up automatically
# where it left off on tomorrow's run, working through the backlog and
# keeping newly-scraped jobs matched over time, with no manual
# re-triggering. Each run logs the relevant-backlog count (start + end)
# as the leading indicator if capacity ever falls behind demand again.
#
# See README.md's "Scheduled runs" section for how to install/remove the
# scheduling entry that calls this script, and where to check its output.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p "$REPO_ROOT/logs"
CRON_LOG="$REPO_ROOT/logs/cron.log"

# --- single-instance guard --------------------------------------------------
# Two launchd jobs land in this script: com.huntloop.scraper (the 3am
# StartCalendarInterval firing) and com.huntloop.scraper-catchup (a
# RunAtLoad boot/login catch-up that runs this once if today's 3am
# firing was missed because the machine was off - see
# scripts/catchup_orchestrator_boot.sh and the 2026-09-06 missed-run
# finding in SESSIONS.md). launchd's own "don't start a job that's
# already running" only covers repeat firings of ONE job, not two
# different jobs invoking the same script, so this is the real guard
# that a catch-up run and a 3am run can never execute concurrently.
#
# Mechanism: re-exec ourselves under lockf(1) holding an exclusive
# flock(2) on logs/.orchestrator.lock. lockf -t 0 fails immediately
# (exit 75, EX_TEMPFAIL) if the lock is already held; we then log one
# line and exit 0 (a collision is expected and harmless, not a failure).
# flock(2) locks are released by the kernel when the holder exits -
# including a kill or a reboot mid-run - so there is never a stale lock
# file to clean up. -k keeps the (empty) lock file between runs, which
# lockf(1) recommends for lock-ordering/perf.
ORCH_LOCK_FILE="$REPO_ROOT/logs/.orchestrator.lock"
if [ -z "${HUNTLOOP_ORCH_LOCKED:-}" ]; then
  export HUNTLOOP_ORCH_LOCKED=1
  SELF="$REPO_ROOT/scripts/$(basename "${BASH_SOURCE[0]}")"
  /usr/bin/lockf -s -t 0 -k "$ORCH_LOCK_FILE" "$SELF" "$@"
  rc=$?
  if [ "$rc" -eq 75 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S %Z'): orchestrator already running (lock held) - this invocation exits without running." >> "$CRON_LOG"
    exit 0
  fi
  exit "$rc"
fi

# Holding the lock now. Record that a scheduled run started today, for
# the boot catch-up job to detect. Written here - right after the lock,
# before any real work or log rotation - so a run that later crashes in
# stage 1 still counts as "today's run happened" and does not get
# re-triggered by a later login the same day.
LAST_RUN_MARKER="$REPO_ROOT/logs/last_scheduled_run.txt"
date '+%Y-%m-%d %H:%M:%S %Z' > "$LAST_RUN_MARKER"

# --- cron.log rotation (STOPGAP) -----------------------------------------
# logs/cron.log captures the full stdout/stderr of every scheduled run
# (stage 1's Docker build output + stage 2's per-job lines), so it grows
# without bound. Before this run starts, gzip the previous run's log into
# logs/archive/ with a timestamped name and truncate cron.log for a fresh
# start. Date-based rotation at the start of each run is the primary
# mechanism - there is deliberately NO mid-run rotation. If a single
# run's cron.log ever exceeds CRON_LOG_MAX_BYTES, something is spamming
# it - that's flagged as a warning here and noted as a known limitation
# in SESSIONS.md, not handled with mid-run rotation logic.
#
# After rotating, a retention cap prunes logs/archive/ oldest-first
# until BOTH: at most CRON_LOG_ARCHIVE_KEEP files remain AND the
# directory is under CRON_LOG_ARCHIVE_MAX_BYTES total (see the
# 2026-09-06 log-verbosity investigation for the 14-file / 3 GiB
# reasoning). Rotation runs once per scheduled run, so 14 files is
# roughly two weeks of history; the size cap is the real backstop if a
# single run's log ever balloons again.
#
# This is an explicit stopgap, not a considered logging architecture: it
# is expected to be superseded once scraping moves to GitHub Actions,
# which captures its own per-workflow logs. huntloop.log's separate
# RotatingFileHandler is unrelated and untouched.
CRON_LOG_ARCHIVE_DIR="$REPO_ROOT/logs/archive"
CRON_LOG_MAX_BYTES=$((500 * 1024 * 1024))  # 500 MB
CRON_LOG_ARCHIVE_KEEP=14                          # keep at most this many archives
CRON_LOG_ARCHIVE_MAX_BYTES=$((3 * 1024 * 1024 * 1024))  # and cap the dir at 3 GiB total

if [ -s "$CRON_LOG" ]; then
  mkdir -p "$CRON_LOG_ARCHIVE_DIR"
  prev_bytes=$(wc -c < "$CRON_LOG" | tr -d ' ')
  archive="$CRON_LOG_ARCHIVE_DIR/cron-$(date '+%Y%m%dT%H%M%S').log.gz"
  if gzip -c "$CRON_LOG" > "$archive"; then
    : > "$CRON_LOG"
    echo "rotated previous cron.log ($prev_bytes bytes) -> $archive" >> "$CRON_LOG"
    if [ "$prev_bytes" -gt "$CRON_LOG_MAX_BYTES" ]; then
      echo "WARNING: previous cron.log exceeded $CRON_LOG_MAX_BYTES bytes before rotation - something is likely spamming it (see SESSIONS.md 'cron.log rotation stopgap')." >> "$CRON_LOG"
    fi
  fi
fi

# --- logs/archive/ retention cap ----------------------------------------
# Prune oldest-first until BOTH limits hold: at most
# CRON_LOG_ARCHIVE_KEEP files remain, and the directory total is under
# CRON_LOG_ARCHIVE_MAX_BYTES. Enforced every run, right after the
# rotation above. Only touches the cron-*.log.gz files this script
# created. (Plain while-read loops, no bash-4 mapfile - the system bash
# here is 3.2; archive names are cron-<timestamp>.log.gz, no spaces.)
if [ -d "$CRON_LOG_ARCHIVE_DIR" ]; then
  archive_list=$(ls -1tr "$CRON_LOG_ARCHIVE_DIR"/cron-*.log.gz 2>/dev/null)  # oldest first
  archive_count=0
  archive_bytes=0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    archive_count=$((archive_count + 1))
    archive_bytes=$((archive_bytes + $(wc -c < "$f" | tr -d ' ')))
  done <<EOF
$archive_list
EOF

  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if [ "$archive_count" -le "$CRON_LOG_ARCHIVE_KEEP" ] \
       && [ "$archive_bytes" -le "$CRON_LOG_ARCHIVE_MAX_BYTES" ]; then
      break
    fi
    f_bytes=$(wc -c < "$f" | tr -d ' ')
    if rm -f "$f"; then
      archive_bytes=$((archive_bytes - f_bytes))
      archive_count=$((archive_count - 1))
      echo "retention: pruned old archive $f ($f_bytes bytes)" >> "$CRON_LOG"
    fi
  done <<EOF
$archive_list
EOF
fi

# See the PATH comment above - launchd's own environment doesn't include
# /usr/local/bin, where Docker Desktop's `docker` CLI lives on this
# machine.
export PATH="/usr/local/bin:$PATH"

PYTHON="$REPO_ROOT/.venv/bin/python"

{
  echo "=== HuntLoop scheduled run started $(date '+%Y-%m-%d %H:%M:%S %Z') ==="

  if [ ! -x "$PYTHON" ]; then
    echo "ERROR: $PYTHON not found or not executable - .venv missing or not set up. Aborting."
    echo "=== run finished with exit code 1 at $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
    exit 1
  fi

  DB_URL_FOR_DOCKER="$(grep -E '^DATABASE_URL=' "$REPO_ROOT/.env" | cut -d= -f2- | sed 's/localhost/host.docker.internal/')"

  # Root-caused 2026-09-04 (see SESSIONS.md "Investigate empty Grafana
  # dashboards"): both stages below push real per-run metrics to the
  # Pushgateway (src/huntloop/metrics.py,
  # src/huntloop/skills_matching_metrics.py), which needs somewhere to
  # push to.
  #
  # The PRIMARY mechanism for that as of 2026-09-04's follow-up fix is
  # `restart: unless-stopped` on the pushgateway/prometheus/grafana
  # services in docker-compose.yml, plus a one-time manual
  # `docker compose --profile observability up -d pushgateway prometheus
  # grafana` (see README.md/CLAUDE.md) - once started, Docker keeps them
  # running in the background continuously (surviving a crash or a
  # Docker Desktop restart) without this script re-starting anything.
  # This line below is now just a DEFENSIVE FALLBACK for the case where
  # Docker Desktop itself was fully quit (which does stop every
  # container, restart policy or not - see the docker-compose.yml
  # comments for what that leaves as a real, accepted gap) - it costs
  # nothing when pushgateway is already up (`up -d` on an already-running
  # container is a no-op), and is guarded the same way
  # `push_run_metrics()`/`push_backfill_metrics()` already are: a failure
  # here (Docker Desktop not running at all, a port conflict, a network
  # hiccup) is logged and swallowed, never allowed to abort or fail this
  # run - the actual scrape/backfill work must not depend on an
  # observability side-effect succeeding.
  echo "--- defensive check: is pushgateway running? (starting it if not - see comment above) ---"
  if docker compose --profile observability up -d pushgateway; then
    echo "pushgateway is up (or already was)"
  else
    echo "WARNING: failed to start the observability profile's pushgateway service - continuing without it. This run's metrics push(es) will fail and be logged as a warning by the app itself, but the actual scrape/backfill work below is unaffected."
  fi

  echo "--- stage 1/2: scraper orchestrator (main.py, via docker compose run) ---"
  docker compose run --rm --build -e DATABASE_URL="$DB_URL_FOR_DOCKER" app python main.py
  scrape_status=$?
  echo "--- stage 1/2 finished with exit code $scrape_status ---"

  echo "--- stage 2/3: skills-matching backfill (scripts/backfill_skills_matching.py) ---"
  "$PYTHON" "$REPO_ROOT/scripts/backfill_skills_matching.py"
  skills_status=$?
  echo "--- stage 2/3 finished with exit code $skills_status ---"

  # Stage 3: map each newly-scraped posting's raw free-text department
  # string onto the canonical taxonomy (huntloop.department_categorization).
  # New rows already get a rule-based category at insert time
  # (huntloop.pipelines); this stage runs the LLM pass over the residual
  # distinct values rules can't place, so the tail never stays
  # permanently NULL as new data arrives. No torch dependency (rules +
  # LLM over HTTP only), so it runs via the local .venv like stage 2. It
  # is bounded by the number of still-uncategorized DISTINCT department
  # strings, not the row count, and stops cleanly when the LLM providers'
  # daily quotas are spent (picking up where it left off next run).
  echo "--- stage 3/3: department categorization backfill (scripts/backfill_department_category.py) ---"
  "$PYTHON" "$REPO_ROOT/scripts/backfill_department_category.py"
  dept_cat_status=$?
  echo "--- stage 3/3 finished with exit code $dept_cat_status ---"

  status=$scrape_status
  if [ "$skills_status" -ne 0 ]; then
    status=$skills_status
  fi
  if [ "$dept_cat_status" -ne 0 ]; then
    status=$dept_cat_status
  fi

  echo "=== run finished with exit code $status (scrape=$scrape_status, skills=$skills_status, dept_cat=$dept_cat_status) at $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
  exit "$status"
} >> "$CRON_LOG" 2>&1

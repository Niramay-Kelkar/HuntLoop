"""
On-demand inspector (and, only with explicit human confirmation, manual
terminator) for the backfill_skills_matching single-instance advisory
lock (key 1751937901).

Background: scripts/backfill_skills_matching.py holds a Postgres
session-level advisory lock for the whole run so two runs can't race the
same `matched_skills IS NULL` rows and burn duplicate Groq/Gemini quota.
That guard works when the second run is alive and blocked cleanly. The
gap it doesn't cover: a run that dies abnormally (an orphaned
launchd-spawned child whose parent job already exited) can sit holding
the lock for hours on an idle DB connection. This happened in production
on 2026-08-31 and had to be cleared with a manual SIGTERM after someone
noticed the stuck process.

backfill_skills_matching.py now *detects and alerts* on that (a CRITICAL
log line when the lock has been held past ~11.1h - 3x the longest real
run) but deliberately never terminates anything automatically: killing a
DB connection on a heuristic can compound an incident, and this project
has a standing rule (2026-08-22 data-wipe incident) against unverified
automated DB operations. Clearing a stale holder is this script's job -
a deliberate, human-invoked action, never wired into the daily run.

Usage:
    PYTHONPATH=src python scripts/check_lock_staleness.py
        Inspect the current holder and print its PID, how long it's held
        the lock, its state and last query. Read-only. Exit 0 if no
        holder or holder looks healthy, 2 if the holder is stale.

    PYTHONPATH=src python scripts/check_lock_staleness.py --terminate
        Same inspection, then - only if a holder exists - prompt for
        confirmation (you must retype the holder's PID) before calling
        pg_terminate_backend on it. Anything other than the exact PID
        aborts without touching the database.
"""
import argparse
import os
import sys
import time

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, text

from huntloop.settings import DATABASE_URL
import backfill_skills_matching as b


def _is_stale(row) -> bool:
    return b._held_seconds(row) >= b.STALE_LOCK_THRESHOLD_SECONDS


def _describe(row) -> None:
    d = b._fmt_duration
    print(f"advisory lock key  : {b._LOCK_KEY}")
    print(f"holder PID         : {row.pid}")
    print(f"state              : {row.state}")
    print(f"held for           : {d(b._held_seconds(row))}  (state_change {row.state_change})")
    print(f"backend age        : {d(row.backend_age_seconds)}  (backend_start {row.backend_start})")
    print(f"query_start        : {row.query_start}")
    print(f"xact_start         : {row.xact_start}")
    print(f"wait_event_type    : {row.wait_event_type}")
    print(f"application_name   : {row.application_name!r}")
    print(f"client_addr        : {row.client_addr}")
    print(f"last query         : {row.last_query!r}")
    print(f"staleness threshold: {d(b.STALE_LOCK_THRESHOLD_SECONDS)} (3x the longest real ~3.7h backfill run)")
    print(f"VERDICT            : {'STALE - likely an orphaned/hung run' if _is_stale(row) else 'within threshold - looks like a healthy run'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--terminate", action="store_true",
        help="after inspection, prompt (retype the PID) to pg_terminate_backend the holder",
    )
    args = ap.parse_args()

    engine = create_engine(DATABASE_URL)
    try:
        row = b.lock_holder_info(engine)
        if row is None:
            print(f"advisory lock key {b._LOCK_KEY}: not held by any live backend. Nothing to do.")
            return 0

        _describe(row)
        stale = _is_stale(row)

        if not args.terminate:
            if stale:
                print("\nRe-run with --terminate to kill this backend after confirming.")
            return 2 if stale else 0

        print()
        if not stale:
            print("Holder is within the staleness threshold. Terminating a possibly-healthy "
                  "run is exactly what this script exists to avoid doing blindly.")
        answer = input(f"Type the holder PID ({row.pid}) to terminate it, or anything else to abort: ").strip()
        if answer != str(row.pid):
            print("Aborted - no PID match, database untouched.")
            return 1

        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            killed = conn.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": row.pid}).scalar()
        print(f"pg_terminate_backend({row.pid}) -> {killed}")
        # Postgres reaps the backend and drops its advisory locks
        # asynchronously - poll briefly before reporting.
        after = row
        for _ in range(10):
            time.sleep(0.5)
            after = b.lock_holder_info(engine)
            if after is None or after.pid != row.pid:
                break
        if after is None:
            print("lock now free.")
        elif after.pid != row.pid:
            print(f"lock now held by a different backend (pid {after.pid}) - a healthy run acquired it.")
        else:
            print(f"lock still shows pid {after.pid} after 5s - re-run this script to re-check.")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

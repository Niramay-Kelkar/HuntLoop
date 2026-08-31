"""
Concurrency guard for scripts/backfill_skills_matching.py (added
2026-08-30, see SESSIONS.md). The daily scheduled run and an ad hoc
manual run collided in production, double-processing ~700 rows and
burning real Groq/Gemini quota. A Postgres session-level advisory lock
now makes a second concurrent invocation exit cleanly instead of racing.

scripts/ isn't a package / isn't on the pytest pythonpath - same
sys.path shim as tests/test_detect_and_store_ats.py.
"""
import logging
import os
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import backfill_skills_matching as b  # noqa: E402


def test_second_holder_is_refused_then_lock_frees(test_database_url):
    e1 = create_engine(test_database_url)
    e2 = create_engine(test_database_url)
    try:
        with b._backfill_lock(e1) as got_first:
            assert got_first is True
            with b._backfill_lock(e2) as got_second:
                assert got_second is False, "second concurrent holder must be refused"
        # first holder released on context exit -> lock available again
        with b._backfill_lock(e2) as got_after:
            assert got_after is True
    finally:
        e1.dispose()
        e2.dispose()


def test_advisory_lock_is_actually_released(test_database_url):
    """Guard against the lock leaking (would wedge every future run)."""
    e = create_engine(test_database_url)
    try:
        with b._backfill_lock(e):
            pass
        with e.connect() as c:
            still_held = c.execute(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                    "AND ((classid::bigint << 32) | objid::bigint) = :k"
                ),
                {"k": b._LOCK_KEY},
            ).scalar()
        assert still_held == 0
    finally:
        e.dispose()


def test_main_exits_without_running_backfill_when_lock_held(test_database_url, monkeypatch):
    monkeypatch.setattr(b, "DATABASE_URL", test_database_url)
    calls = []
    monkeypatch.setattr(b, "_run_backfill", lambda **kw: calls.append(kw))

    holder = create_engine(test_database_url)
    try:
        with b._backfill_lock(holder) as got:
            assert got
            b.main(limit=7)
            assert calls == [], "main() must not run the backfill while the lock is held"
        # lock free now -> main() proceeds
        b.main(limit=7)
        assert calls == [{"limit": 7}]
    finally:
        holder.dispose()


# --- stale-lock detection (added 2026-08-31) --------------------------------


def test_lock_holder_info_finds_the_holder_and_is_none_when_free(test_database_url):
    e = create_engine(test_database_url)
    try:
        assert b.lock_holder_info(e) is None
        with b._backfill_lock(e) as got:
            assert got
            row = b.lock_holder_info(e)
            assert row is not None
            assert row.pid > 0
            assert row.held_seconds is not None and row.held_seconds >= 0
        assert b.lock_holder_info(e) is None
    finally:
        e.dispose()


def test_staleness_check_logs_critical_only_past_threshold(test_database_url, caplog):
    holder = create_engine(test_database_url)
    checker = create_engine(test_database_url)
    try:
        with b._backfill_lock(holder) as got:
            assert got

            # below threshold: no CRITICAL, no WARNING (silent - the caller
            # already logged the "already locked" WARNING itself)
            caplog.clear()
            with caplog.at_level(logging.WARNING, logger=b.logger.name):
                b.check_lock_holder_staleness(checker, threshold_seconds=10_000)
            assert caplog.records == []

            # past threshold: exactly one CRITICAL, carrying the real PID
            caplog.clear()
            with caplog.at_level(logging.CRITICAL, logger=b.logger.name):
                b.check_lock_holder_staleness(checker, threshold_seconds=0)
            crits = [r for r in caplog.records if r.levelno == logging.CRITICAL]
            assert len(crits) == 1
            assert "STALE ADVISORY LOCK ALERT" in crits[0].message
            holder_pid = b.lock_holder_info(checker).pid
            assert f"pid={holder_pid}" in crits[0].message
    finally:
        holder.dispose()
        checker.dispose()


def test_staleness_check_is_quiet_when_lock_is_free(test_database_url, caplog):
    e = create_engine(test_database_url)
    try:
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=b.logger.name):
            b.check_lock_holder_staleness(e, threshold_seconds=0)
        # no live holder -> one benign INFO at most, never WARNING/CRITICAL
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    finally:
        e.dispose()

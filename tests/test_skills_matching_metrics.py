"""
huntloop.skills_matching_metrics - the Prometheus metrics module for the
skills-matching backfill (scripts/backfill_skills_matching.py). Same
testing shape a future huntloop.metrics test would take: assert the
metric names/labels are what the dashboard JSON expects, and that a
failed push never raises (the same defensive contract huntloop.metrics's
push_run_metrics() already has).
"""
from prometheus_client import generate_latest

from huntloop import skills_matching_metrics as m


def teardown_function(_fn):
    """Each metric is module-level (shared across tests in this file) -
    reset the registry's collectors between tests so assertions don't see
    a previous test's increments."""
    for metric in (
        m.skills_matching_jobs_processed_total,
        m.skills_matching_jobs_succeeded_total,
        m.skills_matching_jobs_failed_total,
    ):
        metric.clear()
    m.skills_matching_backlog_remaining.set(0)


def test_registry_exposes_expected_metric_names():
    text = generate_latest(m.registry).decode()
    for name in (
        "huntloop_skills_matching_jobs_processed_total",
        "huntloop_skills_matching_jobs_succeeded_total",
        "huntloop_skills_matching_jobs_failed_total",
        "huntloop_skills_matching_backlog_remaining",
    ):
        assert name in text, f"{name} missing from the registry's exposition text"


def test_counters_are_labeled_by_provider():
    m.skills_matching_jobs_processed_total.labels(provider="groq_120b").inc(3)
    m.skills_matching_jobs_succeeded_total.labels(provider="groq_120b").inc(2)
    m.skills_matching_jobs_failed_total.labels(provider="groq_120b").inc(1)
    m.skills_matching_jobs_processed_total.labels(provider="gemini").inc(5)

    assert (
        m.skills_matching_jobs_processed_total.labels(provider="groq_120b")._value.get() == 3
    )
    assert (
        m.skills_matching_jobs_succeeded_total.labels(provider="groq_120b")._value.get() == 2
    )
    assert (
        m.skills_matching_jobs_failed_total.labels(provider="groq_120b")._value.get() == 1
    )
    assert (
        m.skills_matching_jobs_processed_total.labels(provider="gemini")._value.get() == 5
    )


def test_backlog_gauge_reflects_last_set_value():
    m.skills_matching_backlog_remaining.set(11057)
    assert m.skills_matching_backlog_remaining._value.get() == 11057
    m.skills_matching_backlog_remaining.set(84757)
    assert m.skills_matching_backlog_remaining._value.get() == 84757


def test_push_backfill_metrics_never_raises_when_pushgateway_unreachable(monkeypatch):
    # Same defensive contract as huntloop.metrics.push_run_metrics(): a
    # Pushgateway that's down or unreachable must degrade to a logged
    # warning, never an exception that could take down the backfill run.
    monkeypatch.setattr(m, "PUSHGATEWAY_URL", "localhost:1")  # nothing listens here
    m.push_backfill_metrics()  # must not raise

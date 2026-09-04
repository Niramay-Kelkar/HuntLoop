"""
Per-run Prometheus metrics for the skills-matching backfill
(scripts/backfill_skills_matching.py).

Same pattern as huntloop.metrics (the orchestrator/pipeline metrics) -
deliberately a SEPARATE module-level CollectorRegistry and a separate
Pushgateway job name ("huntloop_skills_matching_backfill", not
"huntloop_orchestrator"), pushed once as a batch at the end of a backfill
run. Keeping this a distinct registry/job means a backfill run's push can
never overwrite/collide with the orchestrator's own grouping key on the
Pushgateway, and this module never has to touch huntloop.metrics.

Metrics are observability, not a hard dependency - same defensive
principle as huntloop.metrics.push_run_metrics(): a Pushgateway-down
failure must never fail a real backfill run, so push_backfill_metrics()
catches everything and only ever logs a warning.
"""

import logging
import os

from prometheus_client import CollectorRegistry, Counter, Gauge, push_to_gateway

logger = logging.getLogger(__name__)

registry = CollectorRegistry()

skills_matching_jobs_processed_total = Counter(
    "huntloop_skills_matching_jobs_processed_total",
    "job_postings rows a skills-matching backfill run attempted to match, by provider",
    ["provider"],
    registry=registry,
)
skills_matching_jobs_succeeded_total = Counter(
    "huntloop_skills_matching_jobs_succeeded_total",
    "job_postings rows a skills-matching backfill run stored a result for, by provider",
    ["provider"],
    registry=registry,
)
skills_matching_jobs_failed_total = Counter(
    "huntloop_skills_matching_jobs_failed_total",
    "job_postings rows a skills-matching backfill run left NULL after a failed attempt, by provider",
    ["provider"],
    registry=registry,
)
skills_matching_backlog_remaining = Gauge(
    "huntloop_skills_matching_backlog_remaining",
    "Relevant job_postings rows still awaiting a skills-matching result "
    "(matched_skills IS NULL AND is_relevant IS TRUE), measured at the end of the run",
    registry=registry,
)

PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "localhost:9091")
PUSHGATEWAY_JOB_NAME = "huntloop_skills_matching_backfill"


def push_backfill_metrics():
    """Push this run's metrics to the Pushgateway as a single batch. Never
    raises - a failed push logs a warning and lets the caller
    (scripts/backfill_skills_matching.py) keep going, since the actual
    matching/DB-write work having already succeeded or failed is real
    work and must not be undone by an observability side-effect failing."""
    try:
        push_to_gateway(PUSHGATEWAY_URL, job=PUSHGATEWAY_JOB_NAME, registry=registry)
        logger.info(f"Pushed skills-matching backfill metrics to Pushgateway at {PUSHGATEWAY_URL}")
    except Exception as e:
        logger.warning(
            f"Failed to push skills-matching backfill metrics to Pushgateway at "
            f"{PUSHGATEWAY_URL} (backfill results above are unaffected): {e}"
        )

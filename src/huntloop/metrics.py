"""
Per-run Prometheus metrics for the orchestrator (main.py) and the pipeline
it drives (pipelines.py).

A single run's counters live on a module-level CollectorRegistry (not
prometheus_client's global default registry) and get pushed to the
Pushgateway exactly once, as a batch, at the end of run_multi_ats_scrape()
- see push_run_metrics(). Nothing pushes per-item; the Pushgateway isn't
scraped by Prometheus until after the whole run has finished anyway (see
observability/prometheus/prometheus.yml).

Metrics are observability, not a hard dependency - the same defensive
principle used elsewhere in this project (detect_ats()'s Playwright
fallback, the LCA ingestion's wage-unit handling): push_run_metrics()
catches everything and only ever logs a warning. A Pushgateway that's
down must never fail an actual scrape run.
"""

import logging
import os

from prometheus_client import CollectorRegistry, Counter, Gauge, push_to_gateway

logger = logging.getLogger(__name__)

registry = CollectorRegistry()

jobs_scraped_total = Counter(
    "huntloop_jobs_scraped_total",
    "Job postings scraped (yielded by a spider) in an orchestrator run",
    ["company", "source"],
    registry=registry,
)
jobs_inserted_total = Counter(
    "huntloop_jobs_inserted_total",
    "Job postings newly inserted into the database in an orchestrator run",
    ["company", "source"],
    registry=registry,
)
jobs_skipped_duplicate_total = Counter(
    "huntloop_jobs_skipped_duplicate_total",
    "Job postings skipped because they were already in the database (reposts/duplicates)",
    ["company", "source"],
    registry=registry,
)
scrape_errors_total = Counter(
    "huntloop_scrape_errors_total",
    "Errors encountered while scraping or storing job postings",
    ["company", "source"],
    registry=registry,
)
run_duration_seconds = Gauge(
    "huntloop_run_duration_seconds",
    "Wall-clock duration of the most recent orchestrator run",
    registry=registry,
)

PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "localhost:9091")
PUSHGATEWAY_JOB_NAME = "huntloop_orchestrator"


def push_run_metrics():
    """Push this run's metrics to the Pushgateway as a single batch. Never
    raises - a failed push logs a warning and lets the caller (main.py)
    keep going, since a scrape/insert run having already succeeded or
    failed is real work and must not be undone by an observability
    side-effect failing."""
    try:
        push_to_gateway(PUSHGATEWAY_URL, job=PUSHGATEWAY_JOB_NAME, registry=registry)
        logger.info(f"Pushed run metrics to Pushgateway at {PUSHGATEWAY_URL}")
    except Exception as e:
        logger.warning(
            f"Failed to push run metrics to Pushgateway at {PUSHGATEWAY_URL} "
            f"(scrape/DB-insert results above are unaffected): {e}"
        )

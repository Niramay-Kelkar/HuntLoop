"""
Prometheus metrics for the feedback-capture pipeline
(huntloop.api.routers.feedback).

Same pattern as huntloop.metrics / huntloop.skills_matching_metrics - its
own module-level CollectorRegistry (not the global default) and its own
Pushgateway job name, so a push here can never collide with the
orchestrator's or the skills-matching backfill's grouping key.

Unlike those two, this one increments and pushes from inside a live API
request (POST /feedback), not a batch script - so push_feedback_metric()
is called once per submission. It is still observability, not a hard
dependency: a Pushgateway-down failure must never fail a user's
submission, so this only ever logs a warning on push failure, same as
every other push_*_metrics() in this project.
"""

import logging
import os

from prometheus_client import CollectorRegistry, Counter, push_to_gateway

logger = logging.getLogger(__name__)

registry = CollectorRegistry()

feedback_submitted_total = Counter(
    "huntloop_feedback_submitted_total",
    "Feedback reports received via POST /feedback, by category",
    ["category"],
    registry=registry,
)

PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "localhost:9091")
PUSHGATEWAY_JOB_NAME = "huntloop_feedback"


def record_feedback_submission(category: str) -> None:
    """Increment the counter and push immediately. Never raises - see
    this module's docstring."""
    feedback_submitted_total.labels(category=category).inc()
    try:
        push_to_gateway(PUSHGATEWAY_URL, job=PUSHGATEWAY_JOB_NAME, registry=registry)
    except Exception as e:
        logger.warning(
            f"Failed to push feedback metrics to Pushgateway at {PUSHGATEWAY_URL} "
            f"(the feedback submission itself is unaffected): {e}"
        )

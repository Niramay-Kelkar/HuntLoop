"""
Prometheus metrics for the Modal resume-processing integration
(huntloop.modal_resume_processing) - Phase C of the Modal integration
(see CLAUDE.md's Modal integration write-up). Same pattern as
huntloop.feedback_metrics: its own module-level CollectorRegistry (not
the global default) and its own Pushgateway job name, so a push here can
never collide with any other job's grouping key.

Like feedback_metrics (and unlike the batch-script metrics modules,
huntloop.metrics / huntloop.skills_matching_metrics), this increments and
pushes from inside live API requests (POST /resumes/upload, PATCH
/resumes/{id}/activate) - so record_modal_invocation()/
record_modal_budget() are called per-request, not once per batch run.
Still observability, not a hard dependency: a Pushgateway-down failure
must never break a resume upload/activation, so this only ever logs a
warning on push failure, same as every other push_*_metrics() in this
project.
"""

import logging
import os

from prometheus_client import CollectorRegistry, Counter, Gauge, push_to_gateway

logger = logging.getLogger(__name__)

registry = CollectorRegistry()

modal_invocations_total = Counter(
    "huntloop_modal_resume_invocations_total",
    "Resume-processing calls routed through huntloop.modal_resume_processing, "
    "by function and outcome (success / fallback_not_configured / "
    "fallback_budget_exhausted / fallback_lookup_failed / "
    "fallback_invocation_error)",
    ["function", "outcome"],
    registry=registry,
)

modal_budget_used = Gauge(
    "huntloop_modal_invocation_budget_used",
    "Modal invocations used so far this calendar month, per the modal_usage ledger",
    registry=registry,
)

modal_budget_cap = Gauge(
    "huntloop_modal_invocation_budget_cap",
    "Modal monthly invocation budget cap currently in effect "
    "(MODAL_MONTHLY_INVOCATION_BUDGET, or the default)",
    registry=registry,
)

PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "localhost:9091")
PUSHGATEWAY_JOB_NAME = "huntloop_modal_resume"


def _push() -> None:
    try:
        push_to_gateway(PUSHGATEWAY_URL, job=PUSHGATEWAY_JOB_NAME, registry=registry)
    except Exception as e:
        logger.warning(
            f"Failed to push Modal resume-processing metrics to Pushgateway at "
            f"{PUSHGATEWAY_URL} (the resume operation itself is unaffected): {e}"
        )


def record_modal_invocation(function: str, outcome: str) -> None:
    """Increment the per-function/outcome counter and push immediately.
    Never raises - see module docstring."""
    modal_invocations_total.labels(function=function, outcome=outcome).inc()
    _push()


def record_modal_budget(used: int, cap: int) -> None:
    """Set the two budget gauges to the ledger's current state and push
    immediately. Never raises - see module docstring."""
    modal_budget_used.set(used)
    modal_budget_cap.set(cap)
    _push()

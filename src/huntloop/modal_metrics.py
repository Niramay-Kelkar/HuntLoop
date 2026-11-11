"""
Prometheus metrics for this project's Modal integration - originally
just resume processing (huntloop.modal_resume_processing, Phase C of the
Modal integration), and now the Resume Deep-Dive
(huntloop.api.routers.deep_dive) as well, recorded through the SAME
counters with function="deep_dive_remote" rather than a second module
or registry (it is the same ledger, the same Pushgateway job, and the
same question - "what did we spend on Modal and what happened?"). See
modal_invocations_total's own help text for the extra outcome values
the spawn-and-poll Deep-Dive flow adds. Phase C of the Modal integration
(see CLAUDE.md's Modal integration write-up). Same pattern as
huntloop.feedback_metrics: its own module-level CollectorRegistry (not
the global default) and its own Pushgateway job name, so a push here can
never collide with any other job's grouping key.

Like feedback_metrics (and unlike the batch-script metrics modules,
huntloop.metrics / huntloop.skills_matching_metrics), this increments and
pushes from inside live API requests (POST /resumes/upload, PATCH
/resumes/{id}/activate, POST /jobs/{id}/deep-dive, GET
/deep-dives/{id}) - so record_modal_invocation()/record_modal_budget()
are called per-request, not once per batch run. Still observability,
not a hard dependency: a Pushgateway-down failure must never break a
resume upload/activation or a Deep-Dive, so this only ever logs a
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
    "Modal calls made by this app, by function and outcome. "
    "function is the remote function name: process_resume_remote / "
    "embed_text_remote (huntloop.modal_resume_processing) or "
    "deep_dive_remote (huntloop.api.routers.deep_dive). "
    "outcome for the two synchronous resume functions is success / "
    "fallback_not_configured / fallback_budget_exhausted / "
    "fallback_lookup_failed / fallback_invocation_error. "
    "deep_dive_remote is spawn-and-poll rather than a blocking call, so it "
    "uses three extra outcomes of its own instead: `spawned` when the call "
    "is dispatched, then `success` or `failed` when a later poll reconciles "
    "it - meaning one Deep-Dive run normally records TWO increments "
    "(spawned, then success/failed), and a `spawned` count higher than "
    "success+failed is the signal for runs nobody ever polled to "
    "completion. It reuses the same fallback_* outcome names when a "
    "dispatch is refused outright, even though nothing falls back for that "
    "feature (there is no local path - see huntloop.modal_deep_dive), so "
    "that one panel can show every reason a Modal call didn't happen.",
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

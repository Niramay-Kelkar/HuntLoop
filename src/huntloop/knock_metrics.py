"""
Prometheus metrics for the Knock notification-orchestration layer
(huntloop.knock_client) - sits in front of the existing Resend
email-alert infrastructure (see CLAUDE.md/SESSIONS.md). Same pattern as
huntloop.resend_metrics / huntloop.modal_metrics / huntloop.feedback_metrics:
its own module-level CollectorRegistry (not the global default) and its
own Pushgateway job name, so a push here can never collide with any
other job's grouping key - in particular, this job name must differ
from Resend's own ("huntloop_resend") since both modules can be pushing
around the same time once Knock is wired in front of the same two call
sites Resend's own metrics already observe.

Called from wherever a trigger is actually attempted - huntloop.feedback_alerts
and huntloop.budget_alerts, once rewired to call
huntloop.knock_client.trigger_workflow() instead of
huntloop.resend_client.send_email(). Still observability, not a hard
dependency: a Pushgateway-down failure must never affect whether a
notification actually triggers, so this only ever logs a warning on
push failure, same as every other push_*_metrics() in this project.
"""

import logging
import os

from prometheus_client import CollectorRegistry, Counter, Gauge, push_to_gateway

logger = logging.getLogger(__name__)

registry = CollectorRegistry()

knock_triggers_total = Counter(
    "huntloop_knock_triggers_total",
    "Knock workflow triggers sent (or attempted) via huntloop.knock_client, by outcome "
    "(success / not_configured / daily_budget_exhausted / monthly_budget_exhausted / trigger_error)",
    ["outcome"],
    registry=registry,
)

knock_daily_budget_used = Gauge(
    "huntloop_knock_daily_budget_used",
    "Workflow triggers sent today (UTC) so far, per the knock_usage ledger",
    registry=registry,
)

knock_daily_budget_cap = Gauge(
    "huntloop_knock_daily_budget_cap",
    "Knock daily trigger budget cap currently in effect (KNOCK_DAILY_TRIGGER_BUDGET, or the default)",
    registry=registry,
)

knock_monthly_budget_used = Gauge(
    "huntloop_knock_monthly_budget_used",
    "Workflow triggers sent so far this calendar month, per the knock_usage ledger",
    registry=registry,
)

knock_monthly_budget_cap = Gauge(
    "huntloop_knock_monthly_budget_cap",
    "Knock monthly trigger budget cap currently in effect (KNOCK_MONTHLY_TRIGGER_BUDGET, or the default)",
    registry=registry,
)

PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "localhost:9091")
PUSHGATEWAY_JOB_NAME = "huntloop_knock"


def _push() -> None:
    try:
        push_to_gateway(PUSHGATEWAY_URL, job=PUSHGATEWAY_JOB_NAME, registry=registry)
    except Exception as e:
        logger.warning(
            f"Failed to push Knock metrics to Pushgateway at {PUSHGATEWAY_URL} "
            f"(the trigger attempt itself is unaffected): {e}"
        )


def record_knock_trigger(outcome: str) -> None:
    """Increment the per-outcome trigger counter and push immediately.
    Never raises - see module docstring."""
    knock_triggers_total.labels(outcome=outcome).inc()
    _push()


def record_knock_budget(daily_used: int, daily_cap: int, monthly_used: int, monthly_cap: int) -> None:
    """Set all four budget gauges to the ledger's current state and push
    immediately. Never raises - see module docstring."""
    knock_daily_budget_used.set(daily_used)
    knock_daily_budget_cap.set(daily_cap)
    knock_monthly_budget_used.set(monthly_used)
    knock_monthly_budget_cap.set(monthly_cap)
    _push()

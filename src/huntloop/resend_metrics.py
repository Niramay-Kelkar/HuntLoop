"""
Prometheus metrics for the Resend email-alert infrastructure
(huntloop.resend_client) - Phase A of the Resend integration (see
CLAUDE.md/SESSIONS.md). Same pattern as huntloop.modal_metrics /
huntloop.feedback_metrics: its own module-level CollectorRegistry (not
the global default) and its own Pushgateway job name, so a push here can
never collide with any other job's grouping key.

Like feedback_metrics/modal_metrics (and unlike the batch-script metrics
modules, huntloop.metrics / huntloop.skills_matching_metrics), this is
meant to be called from wherever a send is actually attempted - a live
API request or a cron script, whichever ends up calling
huntloop.resend_client.send_email() once that's wired up in a later
phase. Nothing calls this module yet (see huntloop.resend_client's
docstring - Phase A is infrastructure only). Still observability, not a
hard dependency: a Pushgateway-down failure must never affect whether an
email actually sends, so this only ever logs a warning on push failure,
same as every other push_*_metrics() in this project.
"""

import logging
import os

from prometheus_client import CollectorRegistry, Counter, Gauge, push_to_gateway

logger = logging.getLogger(__name__)

registry = CollectorRegistry()

resend_sends_total = Counter(
    "huntloop_resend_sends_total",
    "Emails sent (or attempted) via huntloop.resend_client, by outcome "
    "(success / fallback_not_configured / fallback_daily_budget_exhausted / "
    "fallback_monthly_budget_exhausted / fallback_send_error)",
    ["outcome"],
    registry=registry,
)

resend_daily_budget_used = Gauge(
    "huntloop_resend_daily_budget_used",
    "Emails sent today (UTC) so far, per the resend_usage ledger",
    registry=registry,
)

resend_daily_budget_cap = Gauge(
    "huntloop_resend_daily_budget_cap",
    "Resend daily send budget cap currently in effect (RESEND_DAILY_EMAIL_BUDGET, or the default)",
    registry=registry,
)

resend_monthly_budget_used = Gauge(
    "huntloop_resend_monthly_budget_used",
    "Emails sent so far this calendar month, per the resend_usage ledger",
    registry=registry,
)

resend_monthly_budget_cap = Gauge(
    "huntloop_resend_monthly_budget_cap",
    "Resend monthly send budget cap currently in effect (RESEND_MONTHLY_EMAIL_BUDGET, or the default)",
    registry=registry,
)

PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "localhost:9091")
PUSHGATEWAY_JOB_NAME = "huntloop_resend"


def _push() -> None:
    try:
        push_to_gateway(PUSHGATEWAY_URL, job=PUSHGATEWAY_JOB_NAME, registry=registry)
    except Exception as e:
        logger.warning(
            f"Failed to push Resend metrics to Pushgateway at {PUSHGATEWAY_URL} "
            f"(the send attempt itself is unaffected): {e}"
        )


def record_resend_send(outcome: str) -> None:
    """Increment the per-outcome send counter and push immediately.
    Never raises - see module docstring."""
    resend_sends_total.labels(outcome=outcome).inc()
    _push()


def record_resend_budget(daily_used: int, daily_cap: int, monthly_used: int, monthly_cap: int) -> None:
    """Set all four budget gauges to the ledger's current state and push
    immediately. Never raises - see module docstring."""
    resend_daily_budget_used.set(daily_used)
    resend_daily_budget_cap.set(daily_cap)
    resend_monthly_budget_used.set(monthly_used)
    resend_monthly_budget_cap.set(monthly_cap)
    _push()

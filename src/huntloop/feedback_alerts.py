"""
New-feedback email alert - Phase B of the Resend integration (see
CLAUDE.md/SESSIONS.md), built on top of Phase A's
huntloop.resend_client/huntloop.resend_metrics.

Public surface:

    send_new_feedback_alert(feedback_id, category, raw_text) -> None

Called from huntloop.api.routers.feedback.submit_feedback() via
FastAPI's BackgroundTasks, AFTER POST /feedback's row is already
committed and its 202 response has been sent - so a Resend outage or a
budget-cap refusal can never fail the actual submission, mirroring the
same "persist first, best-effort notify after" shape
scripts/triage_feedback.py already uses for LLM summarization (persist
immediately in the request, do the non-essential follow-up work
out-of-band). Unlike the triage script (a standalone process on its own
schedule), this runs as a FastAPI background task within the same
process, right after the response - no separate schedule needed, since
the task explicitly says "no batching, no new scheduled job, immediate
sends."

Needs its OWN database session: huntloop.api.dependencies.get_db()'s
session is closed as soon as the route handler returns (before a
BackgroundTasks callback runs, which FastAPI executes strictly after the
response has been sent) - so this function opens a fresh session via
that same module's SessionLocal rather than being handed the request's
own (already-closed) one. Only plain values (feedback_id/category/
raw_text), never the ORM row itself, are passed in from the router for
the same reason - a detached SQLAlchemy instance bound to a closed
session is not safe to touch here.

Never raises - any ResendUnavailable (not configured, today's/this
month's Resend budget exhausted, a real send error) is logged and
swallowed, never retried. This endpoint has no request to fail at this
point anyway (the response is already gone), but the contract is kept
explicit and absolute: a failure here must never surface anywhere the
submitter or a caller could see it.
"""
import logging
import os

from huntloop.api.dependencies import SessionLocal
from huntloop.resend_client import ResendUnavailable, send_email

logger = logging.getLogger(__name__)

RAW_TEXT_PREVIEW_CHARS = 280

APP_BASE_URL_ENV = "APP_BASE_URL"
DEFAULT_APP_BASE_URL = "http://localhost:3000"

ALERT_TO_ADDRESS_ENV = "RESEND_ALERT_TO_ADDRESS"
DEFAULT_ALERT_TO_ADDRESS = "niramayrkelkar@gmail.com"


def _alert_to_address() -> str:
    return os.getenv(ALERT_TO_ADDRESS_ENV, DEFAULT_ALERT_TO_ADDRESS)


def _app_base_url() -> str:
    return os.getenv(APP_BASE_URL_ENV, DEFAULT_APP_BASE_URL).rstrip("/")


def _truncate(text: str, limit: int = RAW_TEXT_PREVIEW_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def send_new_feedback_alert(feedback_id: int, category: str, raw_text: str) -> None:
    """Sends one email reporting a new feedback submission - the
    category, a truncated preview of its text, and a link to
    /admin/feedback. Intended to be queued via
    BackgroundTasks.add_task() from POST /feedback, never called
    directly from inside the request/response cycle itself. Never
    raises."""
    db = SessionLocal()
    try:
        preview = _escape_html(_truncate(raw_text))
        admin_url = f"{_app_base_url()}/admin/feedback"
        subject = f"HuntLoop: new {category} feedback"
        html = (
            f"<p>New <strong>{category}</strong> feedback was just submitted "
            f"(id={feedback_id}).</p>"
            f"<blockquote>{preview}</blockquote>"
            f"<p><a href=\"{admin_url}\">Review it in the admin dashboard</a></p>"
        )
        try:
            send_email(db, to=_alert_to_address(), subject=subject, html=html)
            logger.info(f"Sent new-feedback alert email for feedback id={feedback_id}")
        except ResendUnavailable as e:
            logger.warning(
                f"New-feedback alert email not sent for feedback id={feedback_id} "
                f"(reason={e.reason}): {e}"
            )
    except Exception:
        logger.exception(f"New-feedback alert failed unexpectedly for feedback id={feedback_id}")
    finally:
        db.close()

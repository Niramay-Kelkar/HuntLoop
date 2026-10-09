"""
Feedback capture + public status endpoints.

POST /feedback (public, no auth) persists a report immediately and
returns 202 - it never calls an LLM inline. That happens out-of-band in
scripts/triage_feedback.py, which is the whole point: a traffic spike
against this endpoint costs database rows, not LLM quota, and a Groq/
Gemini outage can never fail a user's submission (same "the real
work must not depend on an observability/best-effort side-effect
succeeding" principle used throughout this project - see e.g.
huntloop.metrics.push_run_metrics).

GET /feedback/public is the ONLY way this feedback ever becomes visible
to another visitor, and it returns a narrow, explicit field list
(category, llm_summary, status, created_at) - never raw_text. This is
deliberate: a public page rendering arbitrary unauthenticated
user-submitted text would be an open publishing surface, so the only
thing ever shown publicly is an LLM-generated summary written from this
project's own fixed prompt (huntloop.feedback_triage), not the
submitter's own words, and only for rows a human has explicitly marked
is_public=true via scripts/review_feedback.py (there is no admin web UI
yet - see CLAUDE.md).

Rate limiting (5/hour, 20/day per IP): counts existing `feedback` rows
with a matching ip_hash within the window, via a plain SQL query against
this same table - no new infrastructure (no Redis, no in-memory
counter), since this is a low-traffic page and the table itself is
sufficient. `ip_hash` stores a SHA-256 hash of the submitter's IP, never
the raw address, and is used for nothing but this rate limit.

New-feedback email alert (Phase B of the Resend integration - see
CLAUDE.md/SESSIONS.md): submit_feedback() queues
huntloop.feedback_alerts.send_new_feedback_alert() via FastAPI's
BackgroundTasks, which FastAPI runs only after the 202 response has
already been sent - so a Resend outage, a not-configured key, or a spent
send budget can never fail or slow down the actual submission. See that
module's own docstring for why it needs its own DB session rather than
reusing this request's.
"""
import hashlib
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from huntloop.api.dependencies import get_db
from huntloop.api.schemas.feedback import (
    ALLOWED_CATEGORIES,
    MAX_DESCRIPTION_LENGTH,
    FeedbackSubmitRequest,
    FeedbackSubmitResponse,
    PublicFeedbackItem,
)
from huntloop.db_models import Feedback, FeedbackCategory, FeedbackStatus, FeedbackTriageStatus
from huntloop.feedback_alerts import send_new_feedback_alert
from huntloop.feedback_metrics import record_feedback_submission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])

RATE_LIMIT_PER_HOUR = 5
RATE_LIMIT_PER_DAY = 20


def _hash_ip(ip: str) -> str:
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()


def _check_rate_limit(db: Session, ip_hash: str) -> None:
    now = datetime.now(timezone.utc)
    hour_count = db.execute(
        select(func.count())
        .select_from(Feedback)
        .where(Feedback.ip_hash == ip_hash, Feedback.created_at >= now - timedelta(hours=1))
    ).scalar_one()
    if hour_count >= RATE_LIMIT_PER_HOUR:
        raise HTTPException(
            429, f"Rate limit exceeded ({RATE_LIMIT_PER_HOUR} submissions per hour). Try again later."
        )

    day_count = db.execute(
        select(func.count())
        .select_from(Feedback)
        .where(Feedback.ip_hash == ip_hash, Feedback.created_at >= now - timedelta(days=1))
    ).scalar_one()
    if day_count >= RATE_LIMIT_PER_DAY:
        raise HTTPException(
            429, f"Rate limit exceeded ({RATE_LIMIT_PER_DAY} submissions per day). Try again later."
        )


def _validate_submission(body: FeedbackSubmitRequest) -> None:
    if body.category not in ALLOWED_CATEGORIES:
        raise HTTPException(
            400, f"category must be one of {sorted(ALLOWED_CATEGORIES)}, got {body.category!r}."
        )
    description = body.description.strip()
    if not description:
        raise HTTPException(400, "description must not be empty.")
    if len(body.description) > MAX_DESCRIPTION_LENGTH:
        raise HTTPException(
            400, f"description must be at most {MAX_DESCRIPTION_LENGTH} characters."
        )


@router.post("", status_code=202, response_model=FeedbackSubmitResponse)
def submit_feedback(
    body: FeedbackSubmitRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> FeedbackSubmitResponse:
    _validate_submission(body)

    client_ip = request.client.host if request.client else "unknown"
    ip_hash = _hash_ip(client_ip)
    _check_rate_limit(db, ip_hash)

    context: dict = {}
    if body.page_path is not None:
        context["page_path"] = body.page_path
    if body.filters is not None:
        context["filters"] = body.filters
    if body.recent_errors is not None:
        context["recent_errors"] = body.recent_errors

    row = Feedback(
        category=FeedbackCategory(body.category),
        raw_text=body.description,
        context=context or None,
        triage_status=FeedbackTriageStatus.PENDING,
        status=FeedbackStatus.OPEN,
        is_public=False,
        ip_hash=ip_hash,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    record_feedback_submission(body.category)
    background_tasks.add_task(send_new_feedback_alert, row.id, body.category, row.raw_text)

    return FeedbackSubmitResponse(
        id=row.id,
        triage_status=row.triage_status.value,
        status=row.status.value,
        is_public=row.is_public,
    )


@router.get("/public", response_model=list[PublicFeedbackItem])
def list_public_feedback(db: Session = Depends(get_db)) -> list[PublicFeedbackItem]:
    rows = (
        db.query(Feedback)
        .filter(Feedback.is_public.is_(True))
        .order_by(Feedback.created_at.desc())
        .all()
    )
    return [PublicFeedbackItem.model_validate(row) for row in rows]

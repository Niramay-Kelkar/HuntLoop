"""
Admin-only feedback review API - GET /admin/feedback (every row,
including raw_text) and PATCH /admin/feedback/{id} (update
status/is_public). Replaces the terminal-only
scripts/review_feedback.py workflow with a real web UI
(frontend/src/app/admin/feedback/page.tsx).

Every route here is gated by huntloop.api.admin_auth.require_admin -
see that module's docstring for the two-layer (valid Clerk session +
email allowlist) check. Deliberately a separate router/module from
huntloop.api.routers.feedback: that router's two routes (POST
/feedback, GET /feedback/public) stay completely public/unauthenticated
and untouched by this file - this one exposes raw_text and every row
regardless of is_public, so it must never be reachable without a
verified, allow-listed Clerk session.
"""
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from huntloop.api.admin_auth import AdminUser, require_admin
from huntloop.api.dependencies import get_db
from huntloop.db_models import Feedback, FeedbackStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/feedback", tags=["admin-feedback"])


class AdminFeedbackItem(BaseModel):
    """One full feedback row, raw_text included - never exposed outside
    this admin-only router (see huntloop.api.routers.feedback's
    PublicFeedbackItem for the narrow public-facing counterpart)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    category: str
    raw_text: str
    context: dict[str, Any] | None
    llm_summary: str | None
    triage_status: str
    status: str
    is_public: bool
    created_at: datetime
    updated_at: datetime


class AdminFeedbackUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = None
    is_public: bool | None = None


@router.get("", response_model=list[AdminFeedbackItem])
def list_all_feedback(
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
) -> list[AdminFeedbackItem]:
    rows = db.query(Feedback).order_by(Feedback.created_at.desc()).all()
    logger.info("Admin %s listed %d feedback rows", admin.email, len(rows))
    return [AdminFeedbackItem.model_validate(row) for row in rows]


@router.patch("/{feedback_id}", response_model=AdminFeedbackItem)
def update_feedback(
    feedback_id: int,
    body: AdminFeedbackUpdateRequest,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(require_admin),
) -> AdminFeedbackItem:
    if body.status is None and body.is_public is None:
        raise HTTPException(400, "Nothing to update - provide status and/or is_public.")

    row = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if row is None:
        raise HTTPException(404, f"No feedback row with id={feedback_id}.")

    if body.status is not None:
        try:
            row.status = FeedbackStatus(body.status)
        except ValueError:
            raise HTTPException(
                400,
                f"status must be one of {[s.value for s in FeedbackStatus]}, got {body.status!r}.",
            )
    if body.is_public is not None:
        row.is_public = body.is_public

    db.commit()
    db.refresh(row)
    logger.info("Admin %s updated feedback id=%s", admin.email, feedback_id)
    return AdminFeedbackItem.model_validate(row)

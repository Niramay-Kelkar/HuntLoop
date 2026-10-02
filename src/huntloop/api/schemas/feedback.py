"""
Pydantic request/response schemas for the feedback capture/triage
endpoints (huntloop.api.routers.feedback).
"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# The fixed category set, enforced manually in the router (see
# huntloop.api.routers.feedback._validate_submission) rather than via a
# Pydantic Literal - a Literal mismatch would 422 before the route body
# runs, but the task calls for a clear 400 here, same precedent as
# huntloop.api.routers.drafting._require_api_key.
ALLOWED_CATEGORIES = {"bug", "feature", "question", "other"}

MAX_DESCRIPTION_LENGTH = 2000


class FeedbackSubmitRequest(BaseModel):
    """Body of POST /feedback. `context` is NOT accepted from the client
    as a single opaque blob - the router assembles the stored `context`
    column server-side from the optional fields below (current page
    path, active filter/query state, recent client-side errors) plus
    the category/description, so what's persisted is always a known
    shape rather than arbitrary client-asserted JSON."""

    model_config = ConfigDict(extra="forbid")

    category: str
    description: str
    # Optional context the frontend may attach - current page path, the
    # active filter/query state (an arbitrary small JSON-able object),
    # and up to a handful of recent client-side error strings. All
    # optional; the router merges whatever is given into the stored
    # `context` column.
    page_path: str | None = Field(default=None, max_length=500)
    filters: dict[str, Any] | None = None
    recent_errors: list[str] | None = Field(default=None, max_length=10)


class FeedbackSubmitResponse(BaseModel):
    id: int
    triage_status: str
    status: str
    is_public: bool


class PublicFeedbackItem(BaseModel):
    """One row of GET /feedback/public. Deliberately does NOT include
    raw_text, user_id, ip_hash, or context - see that endpoint's
    docstring for why this is a narrow, explicit allow-list rather than
    a generic model dump."""

    model_config = ConfigDict(from_attributes=True)

    category: str
    llm_summary: str | None
    status: str
    created_at: datetime

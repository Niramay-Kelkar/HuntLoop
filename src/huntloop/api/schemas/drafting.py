"""
Pydantic request/response schemas for POST /jobs/{job_id}/draft-answer
(huntloop.api.routers.drafting).
"""
from typing import Literal

from pydantic import BaseModel, Field


class DraftAnswerRequest(BaseModel):
    """`api_key` is deliberately Optional[str] (default None), not a
    required field - a required field would make FastAPI/Pydantic reject
    a missing key with its own generic 422 before the route ever runs.
    The route needs to return a specific, honest 400 for "no key given"
    (see huntloop.api.routers.drafting), so validation of *presence* is
    done there, not here."""

    prompt: str = Field(min_length=1, description="The user's free-text drafting request, e.g. \"draft a why this company answer\".")
    provider: Literal["groq", "gemini"]
    api_key: str | None = Field(
        default=None,
        description="The user's own third-party provider API key. Never persisted or logged server-side.",
    )


class DraftAnswerResponse(BaseModel):
    answer: str
    provider: str

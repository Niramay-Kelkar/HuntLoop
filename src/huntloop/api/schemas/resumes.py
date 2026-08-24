"""
Pydantic response schema for the /resumes endpoints
(huntloop.api.routers.resumes).
"""
from datetime import datetime

from pydantic import BaseModel, Field


class ResumeVersionSummary(BaseModel):
    """Returned by GET /resumes (one per row), POST /resumes/upload, and
    PATCH /resumes/{id}/activate. Deliberately carries only a short
    text_preview, not the full extracted_text - GET /resumes is a list
    endpoint, and the full resume text isn't needed to see version
    history or which one is active. A detail endpoint returning the full
    text is future work if it's ever needed, not built here."""

    id: int
    version_number: int
    uploaded_at: datetime
    is_active: bool
    text_preview: str = Field(description="First ~200 characters of the cleaned extracted resume text.")

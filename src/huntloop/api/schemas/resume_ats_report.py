"""
Pydantic response schema for GET /resumes/active/ats-report
(huntloop.api.routers.resumes).
"""
from datetime import datetime

from pydantic import BaseModel


class AtsReportResponse(BaseModel):
    """One ATS-compatibility report for the currently active resume
    version - see huntloop.resume_ats_report for how score/feedback are
    generated, and huntloop.db_models.ResumeAtsReport for the cached
    row this is read from (computed once per resume_version_id, served
    from cache on every later request)."""

    resume_version_id: int
    score: int
    keyword_feedback: list[str]
    wording_feedback: list[str]
    formatting_feedback: list[str]
    created_at: datetime

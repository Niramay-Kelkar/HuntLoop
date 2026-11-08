"""
Pydantic response schemas for the Resume Deep-Dive endpoints
(huntloop.api.routers.deep_dive).

ONE response model is used for both POST /jobs/{id}/deep-dive and GET
/deep-dives/{id}, at every status - deliberately, so the frontend can
render from one shape regardless of which call it just made and whether
the run is pending, running, done, or failed. A 202 from a fresh
dispatch and a 200 for a finished run differ only in their status code
and in which optional fields are populated.

`result` is typed as a loose dict rather than a nested model. That is
not laziness: it is LLM-produced content that
huntloop.deep_dive.validate_deep_dive_result() has already normalized
and capped, it carries its own `version` field so the shape can change
over time, and rows written by an older version must keep deserializing
after a prompt change. The frontend reads every field defensively for
the same reason.
"""
from datetime import datetime

from pydantic import BaseModel


class DeepDiveResponse(BaseModel):
    id: int
    job_posting_id: int
    resume_version_id: int
    # One of huntloop.db_models.DeepDiveStatus's values: pending /
    # running / done / failed. A plain string rather than the enum so a
    # future status value can't break deserialization of an older row.
    status: str
    # Populated only when status == "done". The validated/capped dict
    # from huntloop.deep_dive, including its own "version" key.
    result: dict | None = None
    # Short, sanitized - never raw provider output. Populated only when
    # status == "failed".
    error: str | None = None
    dispatched_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

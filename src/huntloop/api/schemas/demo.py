"""
Pydantic response schema for GET /demo-info
(huntloop.api.routers.demo_info) - only mounted when DEMO_MODE is on.
"""
from datetime import date

from pydantic import BaseModel


class DemoInfoResponse(BaseModel):
    """GET /demo-info's response - tells the frontend banner what
    snapshot date the demo data is from, and gives it a short message
    to show next to it."""

    snapshot_date: date | None
    message: str

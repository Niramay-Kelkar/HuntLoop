"""
GET /health - basic liveness check.

Deliberately does not check the database or any other dependency -
skeleton-only step (see huntloop.api.main). A real readiness check
(e.g. confirming the DB is reachable via huntloop.api.dependencies.get_db)
is future work, not built here.
"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    return {"status": "ok"}

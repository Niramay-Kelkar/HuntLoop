"""
Shared FastAPI dependencies for huntloop.api routers.

Currently just the DB session dependency - part of this step's "set up
the structure for what's coming" scope (routers/, schemas/,
dependencies.py), ready for future routers to use. No router uses it
yet - the one existing endpoint (GET /health) is deliberately
DB-independent, see huntloop.api.routers.health.
"""
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from huntloop.settings import DATABASE_URL

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

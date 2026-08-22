"""
One-off script: compute and store all-MiniLM-L6-v2 embeddings for the
active resume version and every job_postings row missing one.

Not part of the app's ongoing pipeline - run manually. Requires a
working torch/sentence-transformers install, which this project's local
macOS dev venv does not have (see huntloop.embeddings' docstring and
SESSIONS.md, 2026-08-22 - PyPI has no compatible torch wheel for this
machine's Python 3.13 on Intel macOS). Run it inside the app Docker
image instead (Linux, real torch wheels exist there), pointed at the
real local Postgres via Docker's host gateway:

    docker compose build app   # picks up sentence-transformers/torch
    docker compose run --rm \\
      -e DATABASE_URL="postgresql+psycopg2://<user>:<password>@host.docker.internal:5432/<db>" \\
      app python scripts/backfill_embeddings.py

job_postings is processed in batches (BATCH_SIZE), committing after each
batch - safe to interrupt and re-run, since only rows with
embedding IS NULL are selected each time (same pattern as
scripts/backfill_employer_name_normalized.py). The active resume
version's embedding is recomputed every run (cheap - one row).
"""
import logging
import os
import sys
import time

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import JobPosting, ResumeVersion
from huntloop.embeddings import embed_texts
from huntloop.settings import DATABASE_URL

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


def backfill_resume_embedding(session) -> None:
    active = session.query(ResumeVersion).filter_by(is_active=True).first()
    if active is None:
        logger.warning("No active resume_versions row - nothing to embed.")
        return
    active.embedding = embed_texts([active.extracted_text])[0]
    session.commit()
    logger.info(f"Stored embedding for active resume version {active.version_number}")


def backfill_job_embeddings(session) -> int:
    total_done = 0
    while True:
        rows = (
            session.query(JobPosting)
            .filter(JobPosting.embedding.is_(None))
            .limit(BATCH_SIZE)
            .all()
        )
        if not rows:
            break

        texts = [row.job_description or "" for row in rows]
        embeddings = embed_texts(texts)
        for row, embedding in zip(rows, embeddings):
            row.embedding = embedding
        session.commit()

        total_done += len(rows)
        logger.info(f"Embedded {len(rows)} job_postings rows (running total: {total_done})")

    return total_done


def main():
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    start = time.perf_counter()
    try:
        backfill_resume_embedding(session)
        total = backfill_job_embeddings(session)
    finally:
        session.close()

    elapsed = time.perf_counter() - start
    logger.info(f"Backfill complete - {total} job_postings rows embedded this run in {elapsed:.1f}s")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

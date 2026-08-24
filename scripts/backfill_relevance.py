"""
One-off script: classify every job_postings row with is_relevant IS NULL
using huntloop.relevance_filter's hybrid keyword + embedding-similarity
logic, and store the result. Flags only - never deletes or filters rows
out of job_postings.

Requires a working torch/sentence-transformers install for the
embedding-similarity half of the check, same constraint as
scripts/backfill_embeddings.py - run inside the app Docker image:

    docker compose build app
    docker compose run --rm \\
      -e DATABASE_URL="postgresql+psycopg2://<user>:<password>@host.docker.internal:5432/<db>" \\
      app python scripts/backfill_relevance.py

Processed in batches of BATCH_SIZE, committing after each - safe to
interrupt and re-run, since only rows with is_relevant IS NULL are
selected each time (same pattern as scripts/backfill_embeddings.py and
scripts/backfill_skills_matching.py). Not yet wired into the daily
orchestrator - that wiring, and running this against future newly-
scraped postings, is a deliberately separate next step (see
SESSIONS.md/CLAUDE.md for this task's stated scope).
"""
import logging
import os
import sys
import time

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import JobPosting
from huntloop.embeddings import embed_texts
from huntloop.relevance_filter import REFERENCE_TEXT, classify_relevance, cosine_similarity
from huntloop.settings import DATABASE_URL

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


def main():
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    start = time.perf_counter()
    total_done = 0
    relevant_count = 0

    try:
        reference_embedding = embed_texts([REFERENCE_TEXT])[0]

        while True:
            rows = (
                session.query(JobPosting)
                .filter(JobPosting.is_relevant.is_(None))
                .limit(BATCH_SIZE)
                .all()
            )
            if not rows:
                break

            texts = [f"{row.job_title}\n{row.job_description or ''}" for row in rows]
            embeddings = embed_texts(texts)

            for row, embedding in zip(rows, embeddings):
                similarity = cosine_similarity(reference_embedding, embedding)
                row.is_relevant = classify_relevance(row.job_title, similarity)
                if row.is_relevant:
                    relevant_count += 1
            session.commit()

            total_done += len(rows)
            logger.info(f"Classified {len(rows)} job_postings rows (running total: {total_done})")
    finally:
        session.close()

    elapsed = time.perf_counter() - start
    logger.info(
        f"Backfill complete - {total_done} job_postings rows classified this run "
        f"({relevant_count} relevant, {total_done - relevant_count} not relevant) in {elapsed:.1f}s"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

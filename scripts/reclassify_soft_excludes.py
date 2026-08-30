"""
One-off: re-run huntloop.relevance_filter.classify_relevance for every
job_postings row whose title matches a SOFT_EXCLUDE_KEYWORD
("customer success" / "solutions consultant"), and store the result.

Needed after the 2026-08-30 soft-exclude change (see SESSIONS.md): those
two keywords went from an absolute exclude to an embedding-gated one, so
the stored is_relevant for every such row must be recomputed. No other
title's classification is affected by that change, so only these rows
are touched - not the whole table.

Idempotent and safe to re-run (it just recomputes). Batched /
commit-per-batch, same pattern as scripts/backfill_relevance.py.

Requires torch/sentence-transformers - run in the app Docker image:

    docker compose run --rm --build \\
      -e DATABASE_URL="postgresql+psycopg2://<user>:<pw>@host.docker.internal:5432/<db>" \\
      app python scripts/reclassify_soft_excludes.py
"""
import logging
import os
import sys
import time

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import JobPosting
from huntloop.embeddings import embed_texts
from huntloop.relevance_filter import (
    REFERENCE_TEXT, SOFT_EXCLUDE_KEYWORDS, classify_relevance, cosine_similarity,
)
from huntloop.settings import DATABASE_URL

logger = logging.getLogger(__name__)
BATCH_SIZE = 100


def main():
    engine = create_engine(DATABASE_URL, echo=False)
    session = sessionmaker(bind=engine)()

    like_clause = " OR ".join(f"lower(job_title) LIKE :k{i}" for i in range(len(SOFT_EXCLUDE_KEYWORDS)))
    params = {f"k{i}": f"%{kw}%" for i, kw in enumerate(SOFT_EXCLUDE_KEYWORDS)}

    start = time.perf_counter()
    ref_emb = embed_texts([REFERENCE_TEXT])[0]

    ids = [r[0] for r in session.execute(
        text(f"SELECT id FROM job_postings WHERE {like_clause} ORDER BY id"), params
    ).fetchall()]
    logger.info("%d soft-exclude-titled rows to reclassify", len(ids))

    flips_to_true = 0
    flips_to_false = 0
    unchanged = 0
    try:
        for start_i in range(0, len(ids), BATCH_SIZE):
            batch_ids = ids[start_i:start_i + BATCH_SIZE]
            rows = session.query(JobPosting).filter(JobPosting.id.in_(batch_ids)).all()
            texts = [f"{r.job_title}\n{r.job_description or ''}" for r in rows]
            embs = embed_texts(texts)
            for row, emb in zip(rows, embs):
                sim = cosine_similarity(ref_emb, emb)
                new = classify_relevance(row.job_title, sim)
                old = row.is_relevant
                if old is new:
                    unchanged += 1
                elif new:
                    flips_to_true += 1
                    logger.info("RESCUED id=%s sim=%.4f  %r", row.id, sim, row.job_title)
                else:
                    flips_to_false += 1
                    logger.info("NOW-EXCLUDED id=%s sim=%.4f  %r", row.id, sim, row.job_title)
                row.is_relevant = new
            session.commit()
            logger.info("reclassified %d/%d", min(start_i + BATCH_SIZE, len(ids)), len(ids))
    finally:
        session.close()

    logger.info(
        "Done in %.1fs: %d unchanged, %d flipped to relevant, %d flipped to not-relevant",
        time.perf_counter() - start, unchanged, flips_to_true, flips_to_false,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

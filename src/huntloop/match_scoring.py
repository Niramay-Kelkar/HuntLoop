"""
Shared definition of a job posting's resume match score.

Both the query-time score in the /jobs API (huntloop.api.routers.jobs)
and the job-selection order in scripts/backfill_skills_matching.py need
the same thing: "how well does this posting match the currently active
resume", computed as cosine similarity (1 - pgvector cosine distance)
between the posting's stored embedding and the active resume's embedding.

This module is the single source of truth for that expression and for
how to sort by it, so the two callers can't drift apart on what
match_score means or how NULL scores are ordered.
"""
from sqlalchemy import Float, literal

from huntloop.db_models import JobPosting

MATCH_SCORE_LABEL = "match_score"


def match_score_expr(resume_embedding):
    """SQLAlchemy column expression for a posting's match score against
    ``resume_embedding`` (cosine similarity in [0, 1], higher is a better
    match), labelled ``match_score``.

    When there is no embedding to score against - no active resume, or it
    hasn't been embedded yet (see scripts/backfill_embeddings.py) - this
    is a literal NULL column, so callers degrade to an absent score
    rather than erroring.
    """
    if resume_embedding is not None:
        return (1 - JobPosting.embedding.cosine_distance(resume_embedding)).label(MATCH_SCORE_LABEL)
    return literal(None, type_=Float).label(MATCH_SCORE_LABEL)


def match_score_order_by(resume_embedding, *, descending=True):
    """ORDER BY clause(s) for sorting postings by match score.

    With a resume embedding: best matches first (or worst first when
    ``descending=False``), NULLS LAST in both directions so
    not-yet-embedded postings never sort to the top (Postgres defaults to
    NULLS FIRST for DESC).

    With no resume embedding there is nothing real to sort by, so fall
    back to a stable, deterministic order (``JobPosting.id`` ascending) -
    the same rows in the same order on every run.
    """
    if resume_embedding is None:
        return [JobPosting.id.asc()]
    ordered = match_score_expr(resume_embedding)
    ordered = ordered.desc() if descending else ordered.asc()
    return [ordered.nulls_last()]

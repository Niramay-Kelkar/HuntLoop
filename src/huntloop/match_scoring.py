"""
Shared definition of a job posting's resume match score.

Both the query-time score in the /jobs API (huntloop.api.routers.jobs)
and the job-selection order in scripts/backfill_skills_matching.py need
the same thing: "how well does this posting match the currently active
resume". As of the composite-match-score-v1 work (2026-09-08, see
SESSIONS.md + the reviewed "Composite Match Score" proposal) that is no
longer raw embedding cosine similarity - it is a **composite** of two
signals, computed entirely at query time (no stored column, no
backfill, so no new invalidation problem):

  * embedding term - cosine similarity (1 - pgvector cosine distance)
    between the posting's stored embedding and the active resume's
    embedding, calibrated against SCORE_CEILING the same way the
    frontend gauge always has been (real similarities cluster ~0.03-0.68;
    a raw 0-1 scale would render almost every job in one tier).

  * skills term - matched / (matched + missing) from the stored
    matched_skills / missing_skills lists (see huntloop.skills_matching),
    calibrated against SKILLS_CEILING (~p95 of the real ratio
    distribution).

  composite = EMBEDDING_WEIGHT * embedding_cal + SKILLS_WEIGHT * skills_cal

Only postings that actually carry a usable skills signal get the full
composite. A posting is on the "full" basis when matched_skills and
missing_skills are both real JSON arrays and together hold at least
MIN_SKILLS_DENOM entries. Everything else - not yet analysed
(matched_skills IS NULL, ~86% of postings and shrinking only slowly),
or analysed but with too few total skills to trust the ratio - falls
back to the calibrated embedding term alone ("partial" basis). A
partial-basis score is a real best estimate on the same 0-1 scale, not
a penalty: it sorts and filters interleaved with full-basis scores, and
the API flags it (score_basis) so the frontend can show a neutral
"score provisional - skills analysis pending" marker.

Note on matched_skills = []: an empty matched_skills list *alongside a
populated missing_skills list* is a genuine "this resume matches nothing
here" signal, not missing data - it yields a skills ratio of 0.0 and
stays on the full basis (the low score is correct). Only when the two
lists together are shorter than MIN_SKILLS_DENOM (e.g. both empty) is
the signal treated as too thin and the posting drops to the fallback.

This module is the single source of truth for that expression and for
how to sort by it, so the API and the backfill can't drift apart on
what match_score means or how absent scores are ordered.

Explicitly out of scope for v1 (see the proposal + SESSIONS.md):
years-of-experience matching (deferred to v1.1, pending measurement of
v1) and education matching (dropped - job-side coverage is low and the
signal barely discriminates; display-only if ever surfaced).
"""
from sqlalchemy import Float, String, and_, case, cast, func, literal, null, select

from huntloop.db_models import JobPosting, ResumeSkillMatch

MATCH_SCORE_LABEL = "match_score"
SCORE_BASIS_LABEL = "score_basis"

# Embedding-similarity ceiling: a job at or above this reads as a full
# gauge. Matches the frontend's historical SCORE_CEILING (lib/theme.ts) -
# calibration used to live only in the frontend; the composite has to
# calibrate the embedding term before blending, so it moves here.
SCORE_CEILING = 0.6

# Skills-ratio ceiling. ~p95 of the real matched/(matched+missing)
# distribution over postings with a usable signal (measured 2026-09-08:
# p50 0.33, p90 0.63, p95 0.70). 0.66 per the reviewed proposal - a
# ratio at/above it contributes the full skills weight.
SKILLS_CEILING = 0.66

# Blend weights (proposal, already reviewed). Embedding is the only
# universal signal and the more stable one, so it anchors the score;
# 0.35 is enough for a strong/empty skills match to move the ranking
# without the noisier LLM-derived signal dominating.
EMBEDDING_WEIGHT = 0.65
SKILLS_WEIGHT = 0.35

# matched_skills + missing_skills must total at least this many entries
# for the skills ratio to be trusted; below it the posting falls back to
# the embedding-only score.
MIN_SKILLS_DENOM = 3


def _calibrated_embedding_expr(resume_embedding):
    """clamp((1 - cosine_distance) / SCORE_CEILING, 0, 1) as a SQL float,
    or NULL when the posting has no embedding.

    The explicit NULL guard matters: Postgres ``least``/``greatest``
    ignore NULL arguments, so ``greatest(NULL, 0.0)`` is ``0.0`` - a
    not-yet-embedded posting would score 0 instead of "no score" without
    this."""
    raw = 1 - JobPosting.embedding.cosine_distance(resume_embedding)
    clamped = func.least(func.greatest(raw, 0.0) / SCORE_CEILING, 1.0)
    return case((JobPosting.embedding.is_(None), literal(None)), else_=clamped)


def _skill_match_column(resume_version_id, column):
    """A correlated scalar subquery pulling one column (matched_skills or
    missing_skills) off resume_skill_matches for *this* JobPosting row and
    the given resume_version_id - the per-version replacement for reading
    JobPosting.matched_skills/missing_skills directly (see
    huntloop.db_models.ResumeSkillMatch). NULL (no row) when this job
    hasn't been scored against this resume version yet, same meaning as
    the old column being NULL."""
    if resume_version_id is None:
        return cast(null(), JobPosting.matched_skills.type)
    return (
        select(column)
        .where(
            ResumeSkillMatch.job_posting_id == JobPosting.id,
            ResumeSkillMatch.resume_version_id == resume_version_id,
        )
        .correlate(JobPosting)
        .scalar_subquery()
    )


def _skills_lengths(resume_version_id):
    """(matched_len, missing_len) only where the resume_skill_matches
    column really holds a JSON array - json_typeof guards against SQL
    NULL, a JSON `null` scalar, or any non-array value (json_array_length
    errors on those)."""
    matched_col = _skill_match_column(resume_version_id, ResumeSkillMatch.matched_skills)
    missing_col = _skill_match_column(resume_version_id, ResumeSkillMatch.missing_skills)
    matched_len = case(
        (func.json_typeof(matched_col) == "array", func.json_array_length(matched_col)),
        else_=None,
    )
    missing_len = case(
        (func.json_typeof(missing_col) == "array", func.json_array_length(missing_col)),
        else_=None,
    )
    return matched_len, missing_len


def _has_full_skills_signal(resume_version_id):
    """Boolean SQL clause: this posting carries a usable skills signal
    (both lists are real arrays and together hold >= MIN_SKILLS_DENOM
    entries)."""
    matched_len, missing_len = _skills_lengths(resume_version_id)
    return and_(
        matched_len.isnot(None),
        missing_len.isnot(None),
        (matched_len + missing_len) >= MIN_SKILLS_DENOM,
    )


def match_score_expr(resume_embedding, resume_version_id=None):
    """SQLAlchemy column expression for a posting's composite match score
    against ``resume_embedding`` (in [0, 1], higher is a better match),
    labelled ``match_score``. ``resume_version_id`` identifies which
    resume_skill_matches rows count as this resume's skills signal (see
    huntloop.db_models.ResumeSkillMatch) - pass the same resume's id
    whose embedding is passed as ``resume_embedding``.

    Full basis (usable skills signal): EMBEDDING_WEIGHT * calibrated
    embedding + SKILLS_WEIGHT * calibrated skills ratio. Partial basis
    (no usable skills signal): the calibrated embedding term alone.

    When there is no embedding to score against - no active resume, or
    it hasn't been embedded yet - this is a literal NULL column, so
    callers degrade to an absent score rather than erroring.
    """
    if resume_embedding is None:
        # cast(NULL AS ...) rather than a bare untyped NULL: psycopg reports an
        # untyped NULL column as OID 25 (text) and SQLAlchemy's Float result
        # processor then rejects it - GET /jobs with no active resume would 500.
        return cast(null(), Float).label(MATCH_SCORE_LABEL)

    embedding_cal = _calibrated_embedding_expr(resume_embedding)
    matched_len, missing_len = _skills_lengths(resume_version_id)
    denom = func.nullif(matched_len + missing_len, 0)
    # cast to float first - matched_len / denom would otherwise be integer
    # division in Postgres (every ratio < 1 collapsing to 0).
    skills_ratio = cast(matched_len, Float) / denom
    skills_cal = func.least(skills_ratio / SKILLS_CEILING, 1.0)
    composite = EMBEDDING_WEIGHT * embedding_cal + SKILLS_WEIGHT * skills_cal

    return case(
        (_has_full_skills_signal(resume_version_id), composite),
        else_=embedding_cal,
    ).label(MATCH_SCORE_LABEL)


def score_basis_expr(resume_embedding, resume_version_id=None):
    """SQLAlchemy column expression, labelled ``score_basis``: ``'full'``
    when match_score is the full composite, ``'partial'`` when it's the
    embedding-only fallback, ``NULL`` when there's no score at all (no
    active resume). Lets the API tell the frontend which postings should
    show the "score provisional" marker."""
    if resume_embedding is None:
        return cast(null(), String).label(SCORE_BASIS_LABEL)
    return case(
        (JobPosting.embedding.is_(None), literal(None, type_=String)),
        (_has_full_skills_signal(resume_version_id), literal("full")),
        else_=literal("partial"),
    ).label(SCORE_BASIS_LABEL)


def match_score_order_by(resume_embedding, resume_version_id=None, *, descending=True):
    """ORDER BY clause(s) for sorting postings by match score.

    With a resume embedding: best matches first (or worst first when
    ``descending=False``), NULLS LAST in both directions so
    not-yet-scored postings never sort to the top. Partial-basis
    (embedding-only) scores are real values on the same 0-1 scale and
    sort interleaved with full-composite scores - they are never
    bottom-sorted just for lacking skills data.

    With no resume embedding there is nothing real to sort by, so fall
    back to a stable, deterministic order (``JobPosting.id`` ascending).
    """
    if resume_embedding is None:
        return [JobPosting.id.asc()]
    ordered = match_score_expr(resume_embedding, resume_version_id)
    ordered = ordered.desc() if descending else ordered.asc()
    return [ordered.nulls_last()]


def resume_skill_columns(resume_version_id):
    """(matched_skills, missing_skills) columns, labelled the same as the
    old JobPosting columns, sourced from resume_skill_matches for the
    given resume_version_id instead. NULL/NULL when resume_version_id is
    None (no active resume) or this job hasn't been scored against it
    yet - same meaning the old columns' NULL always had."""
    matched_col = _skill_match_column(resume_version_id, ResumeSkillMatch.matched_skills)
    missing_col = _skill_match_column(resume_version_id, ResumeSkillMatch.missing_skills)
    return (
        matched_col.label("matched_skills"),
        missing_col.label("missing_skills"),
    )

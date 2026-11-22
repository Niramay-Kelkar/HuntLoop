"""
Single-pass scoring columns for GET /jobs.

Why this module exists, in one sentence: ``huntloop.match_scoring``
builds every skills-derived piece of the composite score out of
*correlated scalar subqueries* against ``resume_skill_matches``, and an
unfiltered job list renders fourteen of them in the score expression
alone (plus eight more in ``score_basis`` and two for the skill lists,
and another fourteen when the same score expression is re-rendered in
ORDER BY) - roughly 2.9M per-row lookups across ~219k postings, all of
them before the page ``LIMIT`` can apply. That is the whole reason an
unfiltered ``GET /jobs`` took ~30s: ``COUNT(*)`` is ~211ms and the
pgvector ordering ~398ms, so neither is the cost.

This module reads ``resume_skill_matches`` **once**, as a LEFT JOIN on
``(job_posting_id, resume_version_id)``, and rebuilds the *same*
arithmetic on top of the joined columns. It is deliberately a separate,
list-endpoint-only code path:

  * ``huntloop.match_scoring`` stays the shared definition, untouched,
    so ``scripts/backfill_skills_matching.py`` (the only other caller,
    which uses it purely for ``ORDER BY`` when choosing which postings
    to spend LLM quota on) cannot be affected by anything here.
  * every constant and the embedding term itself are *imported* from
    that module rather than copied, so the two cannot drift on what a
    score means. Only the plumbing - join vs. correlated subquery -
    differs.

The LEFT JOIN cannot change row multiplicity: ``resume_skill_matches``
carries a unique constraint on ``(job_posting_id, resume_version_id)``
(see ``huntloop.db_models.ResumeSkillMatch``), so for one fixed
``resume_version_id`` there is at most one match row per posting. A
posting with no row yields NULL columns, which is exactly what the old
correlated subquery returned for the same case - hence the same
"partial basis, embedding-only score" fallback.

This module is read-only. It issues no INSERT/UPDATE/DELETE/DDL and
touches no schema, index or migration.
"""
from sqlalchemy import Float, String, and_, case, cast, func, literal
from sqlalchemy.orm import aliased

from huntloop.db_models import JobPosting, ResumeSkillMatch
from huntloop.match_scoring import (
    EMBEDDING_WEIGHT,
    MATCH_SCORE_LABEL,
    MIN_SKILLS_DENOM,
    SCORE_BASIS_LABEL,
    SKILLS_CEILING,
    SKILLS_WEIGHT,
    _calibrated_embedding_expr,
    match_score_expr,
    resume_skill_columns,
    score_basis_expr,
)


class JobsListScoring:
    """The scoring columns for one GET /jobs request, reading
    ``resume_skill_matches`` through a single LEFT JOIN.

    Build one per request with the active resume's embedding and id,
    call :meth:`apply_join` on the select, and use the column
    properties. Degenerate cases (no active resume, or an active resume
    with no embedding yet) delegate straight to
    ``huntloop.match_scoring``, which emits no subqueries at all in
    those cases - so the SQL there is unchanged from before.
    """

    def __init__(self, resume_embedding, resume_version_id=None):
        self._embedding = resume_embedding
        self._resume_version_id = resume_version_id
        # Aliased so the join condition can be referenced by the
        # expressions below without depending on how the caller
        # assembled the rest of the FROM clause.
        self._match = aliased(ResumeSkillMatch) if resume_version_id is not None else None

    # -- FROM clause ----------------------------------------------------

    def apply_join(self, query):
        """Add the one LEFT JOIN this scoring needs. A no-op when there
        is no active resume version (nothing to join against)."""
        if self._match is None:
            return query
        return query.outerjoin(
            self._match,
            and_(
                self._match.job_posting_id == JobPosting.id,
                self._match.resume_version_id == self._resume_version_id,
            ),
        )

    # -- skills term ----------------------------------------------------

    def _skills_lengths(self):
        """(matched_len, missing_len) off the joined row, guarded by
        json_typeof exactly as ``huntloop.match_scoring._skills_lengths``
        does - SQL NULL, a JSON ``null`` scalar and any non-array value
        all yield NULL rather than erroring in json_array_length."""
        matched = self._match.matched_skills
        missing = self._match.missing_skills
        matched_len = case(
            (func.json_typeof(matched) == "array", func.json_array_length(matched)),
            else_=None,
        )
        missing_len = case(
            (func.json_typeof(missing) == "array", func.json_array_length(missing)),
            else_=None,
        )
        return matched_len, missing_len

    def _has_full_skills_signal(self):
        matched_len, missing_len = self._skills_lengths()
        return and_(
            matched_len.isnot(None),
            missing_len.isnot(None),
            (matched_len + missing_len) >= MIN_SKILLS_DENOM,
        )

    # -- public columns -------------------------------------------------

    @property
    def match_score(self):
        """The composite match score, labelled ``match_score`` - the
        same value ``huntloop.match_scoring.match_score_expr`` produces,
        computed from the joined match row instead of subqueries."""
        if self._embedding is None:
            return match_score_expr(None)
        if self._match is None:
            # An embedding but no resume version: no skills signal can
            # exist, so this is the embedding-only term either way.
            return match_score_expr(self._embedding, None)

        embedding_cal = _calibrated_embedding_expr(self._embedding)
        matched_len, missing_len = self._skills_lengths()
        denom = func.nullif(matched_len + missing_len, 0)
        # cast first: integer division in Postgres would collapse every
        # ratio below 1 to 0 (same note as in match_scoring).
        skills_ratio = cast(matched_len, Float) / denom
        skills_cal = func.least(skills_ratio / SKILLS_CEILING, 1.0)
        composite = EMBEDDING_WEIGHT * embedding_cal + SKILLS_WEIGHT * skills_cal
        return case(
            (self._has_full_skills_signal(), composite),
            else_=embedding_cal,
        ).label(MATCH_SCORE_LABEL)

    @property
    def score_basis(self):
        """``'full'`` / ``'partial'`` / NULL, labelled ``score_basis`` -
        same rule as ``huntloop.match_scoring.score_basis_expr``."""
        if self._embedding is None or self._match is None:
            return score_basis_expr(self._embedding, None)
        return case(
            (JobPosting.embedding.is_(None), literal(None, type_=String)),
            (self._has_full_skills_signal(), literal("full")),
            else_=literal("partial"),
        ).label(SCORE_BASIS_LABEL)

    @property
    def skill_columns(self):
        """(matched_skills, missing_skills), labelled as the API
        response expects, read off the joined row."""
        if self._match is None:
            return resume_skill_columns(None)
        return (
            self._match.matched_skills.label("matched_skills"),
            self._match.missing_skills.label("missing_skills"),
        )

    # -- ordering -------------------------------------------------------

    def order_by(self, *, descending=True):
        """ORDER BY clause(s) for the match score - best first, NULLS
        LAST in both directions, falling back to a deterministic
        ``JobPosting.id`` order when there is no embedding to sort by.
        Same contract as ``huntloop.match_scoring.match_score_order_by``."""
        if self._embedding is None:
            return [JobPosting.id.asc()]
        ordered = self.match_score
        ordered = ordered.desc() if descending else ordered.asc()
        return [ordered.nulls_last()]

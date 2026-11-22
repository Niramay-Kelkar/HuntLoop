"""
huntloop.api.jobs_list_scoring - the single-pass (LEFT JOIN) scoring
columns GET /jobs uses instead of huntloop.match_scoring's correlated
scalar subqueries.

Two things are asserted here, against the isolated test schema (see
tests/conftest.py):

  1. Equivalence. For every fixture posting - full skills signal, thin
     signal, no match row at all, no embedding, ties, a non-active
     resume version, and different matches recorded per resume version -
     the new builder returns exactly the score, basis, skill lists and
     ordering the shared huntloop.match_scoring expressions return. The
     old expressions are the reference; nothing here re-derives the
     formula by hand (tests/test_match_scoring.py already pins the
     arithmetic itself).

  2. Read-only-ness. A real GET /jobs request through the API client has
     every SQL statement it issues captured, and none may be an INSERT,
     UPDATE, DELETE or DDL statement.
"""
import re

import pytest
from sqlalchemy import event, select

from huntloop.api.jobs_list_scoring import JobsListScoring
from huntloop.db_models import Company, JobPosting, ResumeSkillMatch, ResumeVersion
from huntloop.match_scoring import (
    match_score_expr,
    match_score_order_by,
    resume_skill_columns,
    score_basis_expr,
)

EMBEDDING_DIM = 384


def _vec(index, value, second=None):
    v = [0.0] * EMBEDDING_DIM
    if index is not None:
        v[index] = value
    if second is not None:
        v[second[0]] = second[1]
    return v


RESUME_EMBEDDING = _vec(0, 1.0)
HIGH = _vec(0, 1.0)                                  # cosine similarity 1.0
MID = _vec(0, 0.3, second=(1, 0.9539392014169456))   # ~0.3
LOW = _vec(1, 1.0)                                   # orthogonal -> 0.0


@pytest.fixture
def seeded(db_session):
    """Two resume versions and a spread of postings:

      high      - full skills signal for the active resume
      mid       - skills recorded for the OTHER resume version only
      low       - a thin signal (1 entry total, below MIN_SKILLS_DENOM)
      tie_a/b   - identical embeddings to each other, no skills rows
      no_emb    - no embedding at all
      no_rows   - no resume_skill_matches row for any version
    """
    active = ResumeVersion(
        version_number=1, file_path="a.pdf", extracted_text="resume a",
        is_active=True, embedding=RESUME_EMBEDDING,
    )
    other = ResumeVersion(
        version_number=2, file_path="b.pdf", extracted_text="resume b",
        is_active=False, embedding=RESUME_EMBEDDING,
    )
    company = Company(name="acme")
    db_session.add_all([active, other, company])
    db_session.commit()

    jobs = {}
    for name, emb in (
        ("high", HIGH), ("mid", MID), ("low", LOW),
        ("tie_a", MID), ("tie_b", MID),
        ("no_emb", None), ("no_rows", HIGH),
    ):
        job = JobPosting(
            job_title=name, job_url=f"https://example.com/{name}",
            company_id=company.id, embedding=emb, is_relevant=True,
        )
        db_session.add(job)
        db_session.commit()
        jobs[name] = job

    db_session.add_all([
        # full signal for the active resume
        ResumeSkillMatch(job_posting_id=jobs["high"].id, resume_version_id=active.id,
                         matched_skills=["python", "sql"], missing_skills=["go"]),
        # the same posting scored differently against the other version
        ResumeSkillMatch(job_posting_id=jobs["high"].id, resume_version_id=other.id,
                         matched_skills=[], missing_skills=["rust", "k8s", "terraform"]),
        # only the other version has a row for mid
        ResumeSkillMatch(job_posting_id=jobs["mid"].id, resume_version_id=other.id,
                         matched_skills=["python", "sql", "go"], missing_skills=[]),
        # thin signal: 1 entry total, below MIN_SKILLS_DENOM
        ResumeSkillMatch(job_posting_id=jobs["low"].id, resume_version_id=active.id,
                         matched_skills=["python"], missing_skills=[]),
    ])
    db_session.commit()
    return {"active": active.id, "other": other.id, "jobs": jobs}


def _old_rows(db_session, embedding, resume_version_id):
    matched_col, missing_col = resume_skill_columns(resume_version_id)
    rows = db_session.execute(
        select(
            JobPosting.id,
            match_score_expr(embedding, resume_version_id),
            score_basis_expr(embedding, resume_version_id),
            matched_col,
            missing_col,
        ).order_by(JobPosting.id.asc())
    ).all()
    return {r[0]: tuple(r[1:]) for r in rows}


def _new_rows(db_session, embedding, resume_version_id):
    scoring = JobsListScoring(embedding, resume_version_id)
    matched_col, missing_col = scoring.skill_columns
    query = select(
        JobPosting.id, scoring.match_score, scoring.score_basis, matched_col, missing_col,
    )
    query = scoring.apply_join(query).order_by(JobPosting.id.asc())
    rows = db_session.execute(query).all()
    return {r[0]: tuple(r[1:]) for r in rows}


@pytest.mark.parametrize("version_key", ["active", "other"])
def test_scores_basis_and_skills_match_the_shared_expressions(db_session, seeded, version_key):
    """The new builder agrees with huntloop.match_scoring row for row,
    for both resume versions - including the postings whose skills rows
    belong to the *other* version (which must read as no signal)."""
    rvid = seeded[version_key]
    old = _old_rows(db_session, RESUME_EMBEDDING, rvid)
    new = _new_rows(db_session, RESUME_EMBEDDING, rvid)
    assert set(old) == set(new)
    assert old == new


def test_different_matches_per_resume_version_are_read_independently(db_session, seeded):
    """The same posting carries a different matched/missing split per
    resume version; each version's query must see only its own."""
    high = seeded["jobs"]["high"].id
    active = _new_rows(db_session, RESUME_EMBEDDING, seeded["active"])[high]
    other = _new_rows(db_session, RESUME_EMBEDDING, seeded["other"])[high]
    assert active[2] == ["python", "sql"] and active[1] == "full"
    assert other[2] == [] and other[1] == "full"
    assert active[0] != other[0]


def test_postings_with_no_match_row_fall_back_exactly_as_before(db_session, seeded):
    """A LEFT JOIN that finds nothing must give the same partial-basis,
    embedding-only score the correlated subquery's NULL gave."""
    rvid = seeded["active"]
    old = _old_rows(db_session, RESUME_EMBEDDING, rvid)
    new = _new_rows(db_session, RESUME_EMBEDDING, rvid)
    for key in ("no_rows", "tie_a", "tie_b", "mid"):
        job_id = seeded["jobs"][key].id
        assert new[job_id] == old[job_id]
        assert new[job_id][1] == "partial"
        assert new[job_id][2] is None and new[job_id][3] is None


def test_thin_skills_signal_stays_on_the_partial_basis(db_session, seeded):
    rvid = seeded["active"]
    job_id = seeded["jobs"]["low"].id
    assert _new_rows(db_session, RESUME_EMBEDDING, rvid)[job_id] == \
        _old_rows(db_session, RESUME_EMBEDDING, rvid)[job_id]
    assert _new_rows(db_session, RESUME_EMBEDDING, rvid)[job_id][1] == "partial"


def test_posting_with_no_embedding_has_no_score_or_basis(db_session, seeded):
    rvid = seeded["active"]
    job_id = seeded["jobs"]["no_emb"].id
    new = _new_rows(db_session, RESUME_EMBEDDING, rvid)[job_id]
    assert new == _old_rows(db_session, RESUME_EMBEDDING, rvid)[job_id]
    assert new[0] is None and new[1] is None


def test_no_active_resume_degrades_the_same_way(db_session, seeded):
    """No embedding (and no version): null score, null basis, null skill
    lists, deterministic id ordering - identical to match_scoring."""
    assert _new_rows(db_session, None, None) == _old_rows(db_session, None, None)
    scoring = JobsListScoring(None, None)
    assert [str(c) for c in scoring.order_by()] == [str(c) for c in match_score_order_by(None)]


def test_resume_version_without_an_embedding_still_reads_its_skill_rows(db_session, seeded):
    """An active resume whose embedding hasn't been computed yet: no
    score, but the skill lists still come from its own match rows."""
    rvid = seeded["active"]
    assert _new_rows(db_session, None, rvid) == _old_rows(db_session, None, rvid)
    high = seeded["jobs"]["high"].id
    assert _new_rows(db_session, None, rvid)[high][2] == ["python", "sql"]


def _ordered_ids(db_session, embedding, resume_version_id, use_new):
    if use_new:
        scoring = JobsListScoring(embedding, resume_version_id)
        query = scoring.apply_join(select(JobPosting.id)).order_by(
            *scoring.order_by(descending=True), JobPosting.id.asc()
        )
    else:
        query = select(JobPosting.id).order_by(
            *match_score_order_by(embedding, resume_version_id, descending=True),
            JobPosting.id.asc(),
        )
    return [r[0] for r in db_session.execute(query)]


def test_ordering_including_ties_is_identical(db_session, seeded):
    """tie_a and tie_b share an embedding and have no skills rows, so
    their scores are exactly equal. Both paths break that tie on
    JobPosting.id ascending, so the order is deterministic and the same."""
    rvid = seeded["active"]
    old = _ordered_ids(db_session, RESUME_EMBEDDING, rvid, use_new=False)
    new = _ordered_ids(db_session, RESUME_EMBEDDING, rvid, use_new=True)
    assert new == old
    tie_a, tie_b = seeded["jobs"]["tie_a"].id, seeded["jobs"]["tie_b"].id
    assert new.index(tie_a) < new.index(tie_b)
    # and the un-scored posting is last in both
    assert new[-1] == seeded["jobs"]["no_emb"].id


def test_the_join_never_duplicates_a_posting(db_session, seeded):
    """resume_skill_matches is unique on (job_posting_id,
    resume_version_id), so the LEFT JOIN cannot fan out - 'high' has a
    row for both versions and must still appear exactly once."""
    scoring = JobsListScoring(RESUME_EMBEDDING, seeded["active"])
    ids = [r[0] for r in db_session.execute(scoring.apply_join(select(JobPosting.id)))]
    assert len(ids) == len(set(ids)) == len(seeded["jobs"])


def test_the_new_path_emits_no_correlated_subquery(db_session, seeded):
    """The whole point: one join, zero per-row lookups."""
    from sqlalchemy.dialects import postgresql

    scoring = JobsListScoring(RESUME_EMBEDDING, seeded["active"])
    matched_col, missing_col = scoring.skill_columns
    query = scoring.apply_join(
        select(JobPosting.id, scoring.match_score, scoring.score_basis, matched_col, missing_col)
    ).order_by(*scoring.order_by(), JobPosting.id.asc())
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert sql.count("FROM resume_skill_matches") == 0
    assert sql.count("LEFT OUTER JOIN resume_skill_matches") == 1


_WRITE_STATEMENT = re.compile(
    r"^\s*(insert|update|delete|create|alter|drop|truncate|grant|revoke|comment)\b",
    re.IGNORECASE,
)


def test_get_jobs_issues_no_write_statement(api_client, db_session, seeded):
    """Every SQL statement a real GET /jobs request issues is captured;
    none may write or change schema."""
    statements = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        for params in (
            {},
            {"sort": "-date"},
            {"sort": "-salary"},
            {"company": "acme"},
            {"department": "__unspecified__"},
            {"employment_type": "__unspecified__"},
            {"min_score": 0.1},
            {"location": "__unspecified__"},
            {"salary_unspecified": "true"},
            {"tracked": "true"},
        ):
            response = api_client.get("/jobs", params=params)
            assert response.status_code == 200, (params, response.text)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert statements, "no SQL was captured - the listener did not fire"
    offenders = [s for s in statements if _WRITE_STATEMENT.match(s)]
    assert offenders == [], f"GET /jobs issued write statements: {offenders[:3]}"
    assert any("resume_skill_matches" in s for s in statements)


def test_get_jobs_response_is_unchanged_by_the_new_builder(api_client, db_session, seeded):
    """End-to-end: the endpoint's scores/bases/skill lists still equal
    what the shared match_scoring expressions produce for the active
    resume."""
    expected = _old_rows(db_session, RESUME_EMBEDDING, seeded["active"])
    payload = api_client.get("/jobs", params={"limit": 100}).json()
    assert payload["total"] == len(seeded["jobs"])
    for item in payload["items"]:
        score, basis, matched, missing = expected[item["id"]]
        assert item["match_score"] == pytest.approx(score) if score is not None else item["match_score"] is None
        assert item["score_basis"] == basis
        assert item["matched_skills"] == matched
        assert item["missing_skills"] == missing

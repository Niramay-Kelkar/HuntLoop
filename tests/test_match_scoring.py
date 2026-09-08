"""
huntloop.match_scoring - the shared composite match_score expression /
ORDER BY used by both GET /jobs?sort=-score (huntloop.api.routers.jobs)
and scripts/backfill_skills_matching.py's job-selection query.

Runs real queries against the isolated test schema (see tests/conftest.py)
- synthetic 384-dim vectors with known cosine similarity, same approach
as tests/test_api_jobs.py.

Composite formula (composite-match-score-v1, 2026-09-08):
  full basis  = 0.65 * clamp(sim / 0.60, 0, 1) + 0.35 * min(ratio / 0.66, 1)
  partial     = clamp(sim / 0.60, 0, 1)
where ratio = matched / (matched + missing) and a posting is "full" only
when both skills lists are real arrays totalling >= 3 entries.
"""
import pytest
from sqlalchemy import select

from huntloop.db_models import Company, JobPosting
from huntloop.match_scoring import (
    EMBEDDING_WEIGHT,
    SCORE_CEILING,
    SKILLS_CEILING,
    SKILLS_WEIGHT,
    match_score_expr,
    match_score_order_by,
    score_basis_expr,
)

EMBEDDING_DIM = 384


def _vec(index, value):
    v = [0.0] * EMBEDDING_DIM
    if index is not None:
        v[index] = value
    return v


RESUME_EMBEDDING = _vec(0, 1.0)
HIGH = _vec(0, 1.0)                       # cosine similarity 1.0
# cosine similarity 0.3 with the resume - deliberately < SCORE_CEILING so
# the calibrated embedding term (0.5) does NOT clamp, keeping the fixture
# a real mid-point instead of pinning to 1.0.
MID = _vec(0, 0.3)
MID[1] = 0.9539392014169456
LOW = _vec(1, 1.0)                        # orthogonal -> 0.0


def _seed(db_session):
    company = Company(name="acme")
    db_session.add(company)
    db_session.flush()
    jobs = [
        JobPosting(job_title="High", job_url="http://x/high", company_id=company.id, embedding=HIGH),
        JobPosting(job_title="Mid", job_url="http://x/mid", company_id=company.id, embedding=MID),
        JobPosting(job_title="Low", job_url="http://x/low", company_id=company.id, embedding=LOW),
        JobPosting(job_title="NoEmb", job_url="http://x/noemb", company_id=company.id, embedding=None),
    ]
    db_session.add_all(jobs)
    db_session.commit()
    return jobs


def _score_and_basis(db_session, job_id):
    row = db_session.execute(
        select(
            match_score_expr(RESUME_EMBEDDING),
            score_basis_expr(RESUME_EMBEDDING),
        ).where(JobPosting.id == job_id)
    ).one()
    return row[0], row[1]


def _calibrated_embedding(sim):
    return min(max(sim, 0.0) / SCORE_CEILING, 1.0)


# ---------------------------------------------------------------------------
# ORDER BY (unchanged behaviour: partial-basis scores are monotonic in the
# embedding term, so the pure-embedding fixtures rank exactly as before)
# ---------------------------------------------------------------------------
def test_order_by_ranks_descending_by_match_score_nulls_last(db_session):
    _seed(db_session)
    ordered = (
        db_session.query(JobPosting)
        .order_by(*match_score_order_by(RESUME_EMBEDDING))
        .all()
    )
    assert [j.job_title for j in ordered] == ["High", "Mid", "Low", "NoEmb"]


def test_order_by_ascending(db_session):
    _seed(db_session)
    ordered = (
        db_session.query(JobPosting)
        .order_by(*match_score_order_by(RESUME_EMBEDDING, descending=False))
        .all()
    )
    assert [j.job_title for j in ordered] == ["Low", "Mid", "High", "NoEmb"]


def test_no_resume_embedding_falls_back_to_stable_id_order(db_session):
    jobs = _seed(db_session)
    ordered = (
        db_session.query(JobPosting)
        .order_by(*match_score_order_by(None))
        .all()
    )
    assert [j.id for j in ordered] == sorted(j.id for j in jobs)
    again = db_session.query(JobPosting).order_by(*match_score_order_by(None)).all()
    assert [j.id for j in again] == [j.id for j in ordered]


def test_no_resume_embedding_gives_null_score_and_basis(db_session):
    jobs = _seed(db_session)
    row = db_session.execute(
        select(JobPosting.id, match_score_expr(None), score_basis_expr(None)).where(
            JobPosting.id == jobs[0].id
        )
    ).one()
    assert row[1] is None
    assert row[2] is None


# ---------------------------------------------------------------------------
# Partial basis: no usable skills data -> calibrated embedding term alone
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "embedding, sim",
    [(HIGH, 1.0), (MID, 0.3), (LOW, 0.0)],
)
def test_partial_basis_is_calibrated_embedding_only(db_session, embedding, sim):
    company = Company(name="acme")
    db_session.add(company)
    db_session.flush()
    job = JobPosting(
        job_title="p", job_url=f"http://x/p{sim}", company_id=company.id, embedding=embedding
    )
    db_session.add(job)
    db_session.commit()

    score, basis = _score_and_basis(db_session, job.id)
    assert basis == "partial"
    assert score == pytest.approx(_calibrated_embedding(sim), abs=1e-6)


def test_both_empty_skills_lists_are_treated_as_no_data(db_session):
    """[] / [] -> denominator 0 -> too thin -> partial fallback, NOT a
    zero-ratio full score."""
    company = Company(name="acme")
    db_session.add(company)
    db_session.flush()
    job = JobPosting(
        job_title="e", job_url="http://x/e", company_id=company.id, embedding=MID,
        matched_skills=[], missing_skills=[],
    )
    db_session.add(job)
    db_session.commit()

    score, basis = _score_and_basis(db_session, job.id)
    assert basis == "partial"
    assert score == pytest.approx(_calibrated_embedding(0.3), abs=1e-6)


def test_denominator_below_minimum_falls_back_to_partial(db_session):
    """matched + missing < 3 total -> ratio not trusted -> partial."""
    company = Company(name="acme")
    db_session.add(company)
    db_session.flush()
    job = JobPosting(
        job_title="thin", job_url="http://x/thin", company_id=company.id, embedding=MID,
        matched_skills=["Python"], missing_skills=["Go"],
    )
    db_session.add(job)
    db_session.commit()

    score, basis = _score_and_basis(db_session, job.id)
    assert basis == "partial"
    assert score == pytest.approx(_calibrated_embedding(0.3), abs=1e-6)


# ---------------------------------------------------------------------------
# Full basis: usable skills signal -> blended composite
# ---------------------------------------------------------------------------
def test_full_basis_blends_embedding_and_skills(db_session):
    company = Company(name="acme")
    db_session.add(company)
    db_session.flush()
    # sim 0.3 -> emb_cal = 0.3/0.6 = 0.5 (does not clamp)
    # matched 3, missing 3 -> ratio 0.5 -> skills_cal = 0.5/0.66 = 0.7576
    job = JobPosting(
        job_title="f", job_url="http://x/f", company_id=company.id, embedding=MID,
        matched_skills=["a", "b", "c"], missing_skills=["d", "e", "f"],
    )
    db_session.add(job)
    db_session.commit()

    score, basis = _score_and_basis(db_session, job.id)
    assert basis == "full"
    emb_cal = _calibrated_embedding(0.3)
    skills_cal = min(0.5 / SKILLS_CEILING, 1.0)
    expected = EMBEDDING_WEIGHT * emb_cal + SKILLS_WEIGHT * skills_cal
    assert score == pytest.approx(expected, abs=1e-6)


def test_full_basis_skills_ratio_is_capped_at_the_ceiling(db_session):
    company = Company(name="acme")
    db_session.add(company)
    db_session.flush()
    # LOW embedding (sim 0.0 -> emb_cal 0.0); matched 9 / missing 1 ->
    # ratio 0.9 -> skills_cal capped at 1.0 -> composite = 0.35
    job = JobPosting(
        job_title="cap", job_url="http://x/cap", company_id=company.id, embedding=LOW,
        matched_skills=[f"m{i}" for i in range(9)], missing_skills=["x"],
    )
    db_session.add(job)
    db_session.commit()

    score, basis = _score_and_basis(db_session, job.id)
    assert basis == "full"
    assert score == pytest.approx(SKILLS_WEIGHT * 1.0, abs=1e-6)


def test_empty_matched_with_populated_missing_is_a_real_zero(db_session):
    """matched=[] alongside a populated missing list is a genuine
    'matches nothing here' signal - full basis, skills term 0, so the
    score is 0.65 * emb_cal (a real penalty), not the fallback."""
    company = Company(name="acme")
    db_session.add(company)
    db_session.flush()
    job = JobPosting(
        job_title="z", job_url="http://x/z", company_id=company.id, embedding=HIGH,
        matched_skills=[], missing_skills=["d", "e", "f", "g"],
    )
    db_session.add(job)
    db_session.commit()

    score, basis = _score_and_basis(db_session, job.id)
    assert basis == "full"
    assert score == pytest.approx(EMBEDDING_WEIGHT * _calibrated_embedding(1.0), abs=1e-6)


def test_partial_high_embedding_outranks_full_low_composite(db_session):
    """A partial-basis posting is never bottom-sorted for lacking skills
    data - it interleaves by its real fallback value."""
    company = Company(name="acme")
    db_session.add(company)
    db_session.flush()
    partial = JobPosting(
        job_title="partial-strong", job_url="http://x/ps", company_id=company.id, embedding=HIGH,
    )
    full_weak = JobPosting(
        job_title="full-weak", job_url="http://x/fw", company_id=company.id, embedding=LOW,
        matched_skills=[], missing_skills=["d", "e", "f"],
    )
    db_session.add_all([partial, full_weak])
    db_session.commit()

    ordered = (
        db_session.query(JobPosting)
        .order_by(*match_score_order_by(RESUME_EMBEDDING))
        .all()
    )
    assert [j.job_title for j in ordered] == ["partial-strong", "full-weak"]

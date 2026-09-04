"""
huntloop.match_scoring - the shared match_score expression / ORDER BY
used by both GET /jobs?sort=-score (huntloop.api.routers.jobs) and
scripts/backfill_skills_matching.py's job-selection query.

Runs real queries against the isolated test schema (see tests/conftest.py)
- synthetic 384-dim vectors with known cosine similarity, same approach
as tests/test_api_jobs.py.
"""
from huntloop.db_models import Company, JobPosting
from huntloop.match_scoring import match_score_order_by

EMBEDDING_DIM = 384


def _vec(index, value):
    v = [0.0] * EMBEDDING_DIM
    if index is not None:
        v[index] = value
    return v


RESUME_EMBEDDING = _vec(0, 1.0)
HIGH = _vec(0, 1.0)                       # cosine similarity 1.0
MID = [0.70710678 if i in (0, 1) else 0.0 for i in range(EMBEDDING_DIM)]  # ~0.7071
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
    # deterministic: same order on a repeat call
    again = db_session.query(JobPosting).order_by(*match_score_order_by(None)).all()
    assert [j.id for j in again] == [j.id for j in ordered]

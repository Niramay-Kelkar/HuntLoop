"""
Metrics wiring inside scripts/backfill_skills_matching.py (see
huntloop.skills_matching_metrics for the metrics themselves). Verifies
that a real _run_backfill() pass increments the processed/succeeded/
failed counters with the provider that actually handled each batch, sets
the backlog gauge to the real post-run backlog count, and pushes exactly
once at the end - without hitting Groq/Gemini/Mistral or a real
Pushgateway.

scripts/ isn't a package / isn't on the pytest pythonpath - same
sys.path shim tests/test_backfill_lock.py already uses.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import backfill_skills_matching as b  # noqa: E402

from huntloop.db_models import JobPosting, ResumeVersion
from huntloop import skills_matching_metrics as sm_metrics


def _fixed_batch_limits(_state):
    # Large enough that both seeded jobs land in a single batch, so the
    # test only needs to model one router.match_skills_batch call.
    return (5, 1_000_000, 1_000_000, 1_000)


def _seed(db_session, *, job_count=2):
    resume = ResumeVersion(
        version_number=1,
        file_path="/tmp/resume.pdf",
        extracted_text="Python, SQL, distributed systems.",
        is_active=True,
        embedding=None,
    )
    db_session.add(resume)
    # Deliberately don't pass matched_skills=None here: as documented in
    # CLAUDE.md (the resume-activation reset bug), binding plain Python
    # None to this JSON column stores the literal JSON scalar `null`, not
    # a real SQL NULL - `matched_skills IS NULL` would then be false and
    # _run_backfill's own `matched_skills IS NULL` filter would find
    # nothing. Leaving the column untouched lets it default to a real
    # SQL NULL instead.
    jobs = [
        JobPosting(
            job_title=f"Software Engineer {i}",
            job_url=f"https://example.com/jobs/{i}",
            job_description="Backend role requiring Python and SQL.",
            is_relevant=True,
        )
        for i in range(job_count)
    ]
    db_session.add_all(jobs)
    db_session.commit()
    return resume, jobs


def test_run_attributes_processed_succeeded_failed_to_the_handling_provider(
    db_session, monkeypatch,
):
    _resume, jobs = _seed(db_session)

    # _run_backfill opens its own engine internally via
    # create_engine(DATABASE_URL) - redirect it to the isolated test
    # schema's own engine instead (same seam tests/test_backfill_lock.py
    # uses for DATABASE_URL/_run_backfill).
    monkeypatch.setattr(b, "create_engine", lambda *a, **kw: db_session.get_bind())

    def fake_match_skills_batch(resume_text, descriptions, state):
        assert len(descriptions) == 2
        state["last_provider"] = "groq_120b"
        state["jobs_by"]["groq_120b"] += 1
        state["batches_by"]["groq_120b"] += 1
        return [{"matched_skills": ["Python"], "missing_skills": ["Go"]}, None]

    monkeypatch.setattr(b.router, "batch_limits", _fixed_batch_limits)
    monkeypatch.setattr(b.router, "match_skills_batch", fake_match_skills_batch)

    pushed = []
    monkeypatch.setattr(b, "push_backfill_metrics", lambda: pushed.append(True))

    for metric in (
        sm_metrics.skills_matching_jobs_processed_total,
        sm_metrics.skills_matching_jobs_succeeded_total,
        sm_metrics.skills_matching_jobs_failed_total,
    ):
        metric.clear()

    b._run_backfill()

    assert (
        sm_metrics.skills_matching_jobs_processed_total.labels(provider="groq_120b")._value.get()
        == 2
    )
    assert (
        sm_metrics.skills_matching_jobs_succeeded_total.labels(provider="groq_120b")._value.get()
        == 1
    )
    assert (
        sm_metrics.skills_matching_jobs_failed_total.labels(provider="groq_120b")._value.get()
        == 1
    )
    # One of the two seeded jobs was left NULL (the fake provider
    # returned None for it) - that's the real post-run backlog.
    assert sm_metrics.skills_matching_backlog_remaining._value.get() == 1
    assert pushed == [True]

    db_session.refresh(jobs[0])
    db_session.refresh(jobs[1])
    stored = [j for j in jobs if j.matched_skills is not None]
    still_null = [j for j in jobs if j.matched_skills is None]
    assert len(stored) == 1
    assert len(still_null) == 1


def test_a_giveup_batch_is_labeled_none_not_the_prior_batchs_provider(
    db_session, monkeypatch,
):
    """If every available provider structural-fails a batch, router.
    match_skills_batch returns [None]*n WITHOUT updating state["last_provider"]
    (see skills_matching_router.match_skills_batch's fall-through branch) -
    the wiring must not misattribute that failure to whichever provider
    handled a previous, unrelated batch."""
    _resume, jobs = _seed(db_session, job_count=1)

    monkeypatch.setattr(b, "create_engine", lambda *a, **kw: db_session.get_bind())

    def fake_match_skills_batch(resume_text, descriptions, state):
        state["batch_giveups"] += 1
        return [None] * len(descriptions)

    monkeypatch.setattr(b.router, "batch_limits", _fixed_batch_limits)
    monkeypatch.setattr(b.router, "match_skills_batch", fake_match_skills_batch)
    monkeypatch.setattr(b, "push_backfill_metrics", lambda: None)

    for metric in (
        sm_metrics.skills_matching_jobs_processed_total,
        sm_metrics.skills_matching_jobs_failed_total,
    ):
        metric.clear()

    b._run_backfill()

    assert sm_metrics.skills_matching_jobs_processed_total.labels(provider="none")._value.get() == 1
    assert sm_metrics.skills_matching_jobs_failed_total.labels(provider="none")._value.get() == 1
    db_session.refresh(jobs[0])
    assert jobs[0].matched_skills is None

"""
Integration tests for GET /resumes/active/ats-report
(huntloop.api.routers.resumes), against the isolated test schema (see
tests/conftest.py's api_client fixture) - never touches real production
data, and never calls a real LLM provider (huntloop.resume_ats_report.
generate_ats_report is monkeypatched in every test below).

This endpoint lives on the SAFE `router` (not unsafe_router) and never
touches resume_versions.is_active, matched_skills, or missing_skills -
there is nothing here for the skill-gap backfill or resumes.activate()
to be disturbed by.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import huntloop.resume_ats_report as resume_ats_report
from huntloop.db_models import ResumeAtsReport, ResumeVersion


def _make_active_resume(db_session, text="A resume with some content.") -> ResumeVersion:
    resume = ResumeVersion(
        version_number=1,
        file_path="/tmp/does-not-matter.pdf",
        extracted_text=text,
        is_active=True,
    )
    db_session.add(resume)
    db_session.commit()
    return resume


_FAKE_REPORT = {
    "score": 72,
    "keyword_feedback": ["Mentions Python and SQL but not Docker."],
    "wording_feedback": ["Several bullets start with 'Responsible for' - use stronger verbs."],
    "formatting_feedback": ["No tables or columns detected - should parse cleanly."],
}


def test_404_when_no_active_resume(api_client, db_session):
    response = api_client.get("/resumes/active/ats-report")
    assert response.status_code == 404


def test_first_call_computes_and_caches_report(api_client, db_session, monkeypatch):
    resume = _make_active_resume(db_session)

    calls = []

    def fake_generate(resume_text):
        calls.append(resume_text)
        return dict(_FAKE_REPORT)

    monkeypatch.setattr(resume_ats_report, "generate_ats_report", fake_generate)

    response = api_client.get("/resumes/active/ats-report")
    assert response.status_code == 200
    body = response.json()
    assert body["resume_version_id"] == resume.id
    assert body["score"] == 72
    assert body["keyword_feedback"] == _FAKE_REPORT["keyword_feedback"]
    assert body["wording_feedback"] == _FAKE_REPORT["wording_feedback"]
    assert body["formatting_feedback"] == _FAKE_REPORT["formatting_feedback"]
    assert "created_at" in body

    # Exactly one real "LLM" call was made, with the active resume's
    # actual extracted text.
    assert calls == [resume.extracted_text]

    # And a real cache row now exists.
    cached = db_session.query(ResumeAtsReport).filter_by(resume_version_id=resume.id).first()
    assert cached is not None
    assert cached.score == 72


def test_second_call_serves_from_cache_without_calling_the_provider_again(api_client, db_session, monkeypatch):
    resume = _make_active_resume(db_session)

    call_count = {"n": 0}

    def fake_generate(resume_text):
        call_count["n"] += 1
        return dict(_FAKE_REPORT)

    monkeypatch.setattr(resume_ats_report, "generate_ats_report", fake_generate)

    first = api_client.get("/resumes/active/ats-report")
    second = api_client.get("/resumes/active/ats-report")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    # The real evidence this is a cache hit, not a second computation:
    # the "provider" was only ever invoked once across both requests.
    assert call_count["n"] == 1


def test_returns_502_when_every_provider_fails_and_nothing_is_cached(api_client, db_session, monkeypatch):
    _make_active_resume(db_session)
    monkeypatch.setattr(resume_ats_report, "generate_ats_report", lambda resume_text: None)

    response = api_client.get("/resumes/active/ats-report")
    assert response.status_code == 502
    assert db_session.query(ResumeAtsReport).count() == 0


def test_losing_a_cache_write_race_serves_the_winners_row(api_client, db_session, monkeypatch, test_database_url):
    """Simulates two concurrent first-requests (e.g. two worker
    processes, which the in-process lock alone can't cover): while this
    request's own "LLM" call is in flight, a second, fully independent
    DB session/connection commits a report for the same resume_version_id
    first. This request should then hit the unique-constraint
    IntegrityError on its own commit and return the winner's already-
    committed row rather than erroring or double-inserting."""
    resume = _make_active_resume(db_session)

    def fake_generate_with_concurrent_writer(resume_text):
        other_engine = create_engine(test_database_url)
        OtherSession = sessionmaker(bind=other_engine)
        with OtherSession() as other_session:
            other_session.add(
                ResumeAtsReport(
                    resume_version_id=resume.id,
                    score=55,
                    keyword_feedback=["winner"],
                    wording_feedback=["winner"],
                    formatting_feedback=["winner"],
                )
            )
            other_session.commit()
        other_engine.dispose()
        return dict(_FAKE_REPORT)

    monkeypatch.setattr(resume_ats_report, "generate_ats_report", fake_generate_with_concurrent_writer)

    response = api_client.get("/resumes/active/ats-report")
    assert response.status_code == 200
    assert response.json()["score"] == 55
    assert db_session.query(ResumeAtsReport).count() == 1

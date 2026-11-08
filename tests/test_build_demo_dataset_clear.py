"""
Tests for scripts/build_demo_dataset.py's _force_clear_target() table
ORDER, specifically that resume_deep_dives is cleared before the two
tables it has NOT NULL foreign keys to (resume_versions and
job_postings).

Runs against the isolated test schema (tests/conftest.py), never a real
or live database - and in particular never TARGET_DATABASE_URL. Nothing
here invokes the dataset builder's main(); only the one clear function
is called, with a session this suite already owns.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

from huntloop.db_models import (
    Company,
    DeepDiveStatus,
    JobPosting,
    JobSource,
    ResumeDeepDive,
    ResumeVersion,
)


def _load_builder():
    """scripts/ isn't an importable package, so the module is loaded
    from its path - same approach as the other script-level tests here."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_demo_dataset.py"
    spec = importlib.util.spec_from_file_location("build_demo_dataset_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def builder():
    return _load_builder()


def test_force_clear_lists_deep_dives_before_its_parent_tables(builder):
    """A static check on the declared order, so this stays correct even
    if ON DELETE CASCADE would have masked a wrong order at runtime."""
    import inspect

    source = inspect.getsource(builder._force_clear_target)
    order = [
        name
        for name in ("resume_deep_dives", "job_postings", "resume_versions")
        if f'"{name}"' in source
    ]
    assert "resume_deep_dives" in order, "_force_clear_target must clear resume_deep_dives"
    assert order.index("resume_deep_dives") < order.index("job_postings")
    assert order.index("resume_deep_dives") < order.index("resume_versions")


def test_force_clear_removes_deep_dive_rows_without_a_foreign_key_error(builder, db_session):
    """The real thing: seed a Deep-Dive whose FKs point at a resume
    version and a job posting that are about to be deleted too, then run
    the clear and confirm it completes and leaves all three tables
    empty."""
    company = Company(name="acme")
    source = JobSource(name="greenhouse_api")
    db_session.add_all([company, source])
    db_session.commit()

    job = JobPosting(
        job_title="Backend Engineer",
        job_url="https://example.com/jobs/backend",
        company_id=company.id,
        source_id=source.id,
    )
    resume = ResumeVersion(
        version_number=1,
        file_path="data/resumes/fake_v1.pdf",
        extracted_text="resume text",
        is_active=True,
    )
    db_session.add_all([job, resume])
    db_session.commit()

    db_session.add(
        ResumeDeepDive(
            job_posting_id=job.id,
            resume_version_id=resume.id,
            status=DeepDiveStatus.DONE,
            result={"version": "deep-dive-v1", "summary": "s", "fit_rating": "strong"},
        )
    )
    db_session.commit()
    assert db_session.query(ResumeDeepDive).count() == 1

    builder._force_clear_target(db_session)

    assert db_session.query(ResumeDeepDive).count() == 0
    assert db_session.query(JobPosting).count() == 0
    assert db_session.query(ResumeVersion).count() == 0


def test_builder_never_generates_deep_dives(builder):
    """The demo dataset deliberately ships ZERO Deep-Dives - they're
    created by visitors pressing the button, against the demo's own
    Modal budget. A future change that starts seeding them should have
    to delete this test deliberately."""
    import inspect

    source = inspect.getsource(builder)
    assert "ResumeDeepDive(" not in source

"""
Integration tests for the /resumes API endpoints
(huntloop.api.routers.resumes), against the isolated test schema (see
tests/conftest.py's api_client fixture) - never touches real production
data.

huntloop.embeddings.embed_text requires a working torch install, which
this project's local dev venv can't run (see CLAUDE.md/SESSIONS.md -
confirmed by actually trying: ModuleNotFoundError on this machine).
huntloop.embeddings itself fails to import here (it imports
sentence_transformers at module level), so `monkeypatch.setattr
("huntloop.embeddings.embed_text", ...)` doesn't work either - that
still has to import the real module first. Every test that exercises an
embedding-computing code path instead pre-seeds `sys.modules
["huntloop.embeddings"]` with a fake module (see stub_embed_text below)
before the resumes router's lazy `from huntloop.embeddings import
embed_text` runs - Python's import system finds it already in
sys.modules and never touches the real file, so the real
sentence-transformers import is never attempted. This only swaps out the
model call itself; the resumes router's own logic (version bookkeeping,
is_active swaps, skills reset, dynamic active-resume resolution) is
exercised for real, unchanged.

save_uploaded_pdf() (huntloop.resume_ingestion) writes to the real
data/resumes/ directory regardless of which DB schema a test points at -
it's a filesystem path, not schema-scoped. Every test that uploads a
real file tracks and deletes what it wrote via the _uploaded_paths
fixture below, so this suite never leaves stray files behind.
"""
import io
import os
import sys
import types

import pytest

from huntloop.api.routers.resumes import MAX_UPLOAD_BYTES, _activate_rate_limiter, _upload_rate_limiter
from huntloop.db_models import Company, JobPosting, JobSource, ResumeVersion
from huntloop.resume_ingestion import RESUMES_DIR


@pytest.fixture(autouse=True)
def _reset_resume_rate_limiters():
    """Same reasoning as test_api_drafting.py's _reset_rate_limiter - the
    FastAPI `app` (and therefore these limiters, module-level singletons)
    is shared across the whole test session."""
    _upload_rate_limiter.reset()
    _activate_rate_limiter.reset()
    yield
    _upload_rate_limiter.reset()
    _activate_rate_limiter.reset()


@pytest.fixture(autouse=True)
def _force_modal_not_configured(monkeypatch):
    """This suite must stay hermetic regardless of what's in a real
    developer's .env - huntloop.modal_resume_processing.load_dotenv()
    picks up MODAL_TOKEN_ID/MODAL_TOKEN_SECRET from the real .env the
    moment it's present on the machine running the tests, which lets
    POST /resumes/upload and PATCH /resumes/{id}/activate genuinely reach
    the real deployed Modal app over the network instead of exercising
    the local stub_embed_text path these tests are built around -
    confirmed live: test_activate_computes_missing_embedding_before_activating
    failed for exactly this reason once a real Modal deployment existed
    (see CLAUDE.md). Clearing both env vars for every test in this module
    forces huntloop.modal_resume_processing._modal_configured() to False,
    so every test deterministically exercises the local fallback it was
    written against - same guarantee tests/test_modal_resume_processing.py
    gives its own tests, applied here too."""
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    monkeypatch.delenv("MODAL_TOKEN_SECRET", raising=False)

EMBEDDING_DIM = 384


def _vec(*active_indices_and_values):
    v = [0.0] * EMBEDDING_DIM
    for index, value in active_indices_and_values:
        v[index] = value
    return v


EMBEDDING_A = _vec((0, 1.0))  # resume version A's embedding
EMBEDDING_B = _vec((1, 1.0))  # resume version B's embedding - orthogonal to A


def _minimal_pdf_bytes(text: str | None) -> bytes:
    """A minimal, hand-built single-page PDF whose content stream draws
    `text` with the standard Helvetica font (or contains no text object
    at all, if text is None) - real, valid PDF bytes that pdfplumber
    genuinely parses, not a mock, without needing a PDF-authoring library
    as a test dependency. Verified against pdfplumber directly before
    relying on it here."""
    content = b"BT ET" if text is None else f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (b"<< /Length %d >>\nstream\n" % len(content)) + content + b"\nendstream",
    ]
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{i} 0 obj\n".encode())
        buf.write(obj)
        buf.write(b"\nendobj\n")
    xref_offset = buf.tell()
    n = len(objects) + 1
    buf.write(f"xref\n0 {n}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.write(f"{off:010d} 00000 n \n".encode())
    buf.write(b"trailer\n")
    buf.write(f"<< /Size {n} /Root 1 0 R >>\n".encode())
    buf.write(b"startxref\n")
    buf.write(f"{xref_offset}\n".encode())
    buf.write(b"%%EOF")
    return buf.getvalue()


@pytest.fixture()
def _uploaded_paths():
    """Deletes any real file a test uploads via POST /resumes/upload
    (save_uploaded_pdf writes to the real data/resumes/ dir - see this
    module's docstring) afterward. Diffs RESUMES_DIR's contents before/
    after rather than reading it off a resume_versions row, since a
    rejected upload (e.g. the 422 no-extractable-text case) writes the
    file to disk before validation fails and never gets a DB row at all
    - that file still needs cleaning up."""
    os.makedirs(RESUMES_DIR, exist_ok=True)
    before = set(os.listdir(RESUMES_DIR))
    yield
    after = set(os.listdir(RESUMES_DIR))
    for name in after - before:
        os.remove(os.path.join(RESUMES_DIR, name))


@pytest.fixture()
def stub_embed_text(monkeypatch):
    """Deterministic stand-in for huntloop.embeddings.embed_text - see
    module docstring for why this goes through sys.modules rather than
    monkeypatch.setattr. Content containing "AAA" embeds to EMBEDDING_A,
    "BBB" to EMBEDDING_B, anything else to EMBEDDING_A."""
    calls = []

    def _fake(text):
        calls.append(text)
        return EMBEDDING_B if "BBB" in text else EMBEDDING_A

    fake_module = types.ModuleType("huntloop.embeddings")
    fake_module.embed_text = _fake
    monkeypatch.setitem(sys.modules, "huntloop.embeddings", fake_module)
    return calls


def _seed_resume(db_session, version_number, is_active, embedding=None, text="resume text"):
    resume = ResumeVersion(
        version_number=version_number,
        file_path=f"data/resumes/fake_v{version_number}.pdf",
        extracted_text=text,
        is_active=is_active,
        embedding=embedding,
    )
    db_session.add(resume)
    db_session.commit()
    return resume


def _seed_job(db_session, company, source, title, embedding, matched=None, missing=None):
    job = JobPosting(
        job_title=title,
        job_url=f"https://example.com/jobs/{title}",
        job_description="a real job description",
        company_id=company.id,
        source_id=source.id,
        embedding=embedding,
        matched_skills=matched,
        missing_skills=missing,
    )
    db_session.add(job)
    db_session.commit()
    return job


# ---------------------------------------------------------------------
# GET /resumes
# ---------------------------------------------------------------------


def test_list_resumes_returns_metadata_and_truncated_preview_not_full_text(api_client, db_session):
    long_text = "Experienced engineer. " * 50  # well over the 200-char preview length
    _seed_resume(db_session, 1, is_active=False, text=long_text)
    _seed_resume(db_session, 2, is_active=True, text="short active resume text")

    response = api_client.get("/resumes")
    assert response.status_code == 200
    body = response.json()

    assert len(body) == 2
    # Newest version first.
    assert [r["version_number"] for r in body] == [2, 1]

    v1 = next(r for r in body if r["version_number"] == 1)
    assert v1["is_active"] is False
    assert "uploaded_at" in v1
    assert len(v1["text_preview"]) < len(long_text)
    assert v1["text_preview"] != long_text

    v2 = next(r for r in body if r["version_number"] == 2)
    assert v2["is_active"] is True


# ---------------------------------------------------------------------
# POST /resumes/upload
# ---------------------------------------------------------------------


def test_upload_creates_new_active_version_and_deactivates_old(api_client, db_session, stub_embed_text, _uploaded_paths):
    old = _seed_resume(db_session, 1, is_active=True, embedding=EMBEDDING_A, text="old resume")

    pdf_bytes = _minimal_pdf_bytes("New resume content AAA")
    response = api_client.post(
        "/resumes/upload",
        files={"file": ("new_resume.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["version_number"] == 2
    assert body["is_active"] is True
    assert "New resume content" in body["text_preview"]

    db_session.expire_all()
    old_reloaded = db_session.get(ResumeVersion, old.id)
    assert old_reloaded.is_active is False

    new_row = db_session.query(ResumeVersion).filter_by(version_number=2).one()
    assert new_row.is_active is True
    assert new_row.embedding is not None
    assert os.path.exists(new_row.file_path)
    assert new_row.file_path.startswith(RESUMES_DIR)


def test_upload_resets_matched_and_missing_skills_on_all_jobs(api_client, db_session, stub_embed_text, _uploaded_paths):
    _seed_resume(db_session, 1, is_active=True, embedding=EMBEDDING_A)
    company = Company(name="checkr")
    source = JobSource(name="greenhouse_api")
    db_session.add_all([company, source])
    db_session.commit()
    job = _seed_job(
        db_session, company, source, "Backend Engineer", EMBEDDING_A,
        matched=["Python", "AWS"], missing=["Go"],
    )

    pdf_bytes = _minimal_pdf_bytes("New resume AAA")
    response = api_client.post("/resumes/upload", files={"file": ("r.pdf", pdf_bytes, "application/pdf")})
    assert response.status_code == 201

    db_session.expire_all()
    reloaded = db_session.get(JobPosting, job.id)
    assert reloaded.matched_skills is None
    assert reloaded.missing_skills is None
    # A stronger check than the ORM-level assertion above: a JSON column
    # holding the *JSON* scalar null (as opposed to a real SQL NULL) also
    # deserializes to Python None via the ORM (json.loads('null') ==
    # None), so the assertions above alone would NOT catch a reset that
    # writes JSON 'null' instead of SQL NULL - a real bug found while
    # verifying this endpoint against production data (see
    # SESSIONS.md/the _reset_skills_matching docstring). This queries the
    # exact same way scripts/backfill_skills_matching.py's own
    # reprocessing query does (`.filter(JobPosting.matched_skills.is_(None))`)
    # - the thing that would have silently stopped picking up these rows.
    assert db_session.query(JobPosting).filter(JobPosting.matched_skills.is_(None)).count() == 1
    assert db_session.query(JobPosting).filter(JobPosting.missing_skills.is_(None)).count() == 1


def test_upload_rejects_non_pdf_file(api_client, db_session):
    response = api_client.post(
        "/resumes/upload",
        files={"file": ("resume.txt", b"not a pdf", "text/plain")},
    )
    assert response.status_code == 400


def test_upload_rejects_empty_file(api_client, db_session):
    response = api_client.post(
        "/resumes/upload",
        files={"file": ("resume.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 400


def test_upload_422_when_no_text_extracted(api_client, db_session, _uploaded_paths):
    pdf_bytes = _minimal_pdf_bytes(None)  # a real, valid PDF with no text object at all
    response = api_client.post(
        "/resumes/upload",
        files={"file": ("blank.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 422
    # No resume_versions row should have been created for a rejected upload.
    assert db_session.query(ResumeVersion).count() == 0


# ---------------------------------------------------------------------
# PATCH /resumes/{id}/activate
# ---------------------------------------------------------------------


def test_activate_switches_active_version_and_resets_skills(api_client, db_session):
    # Distinct text (not just distinct embeddings) - the new content-hash
    # no-op check (see huntloop.api.routers.resumes) compares extracted
    # text, so two versions sharing the same seeded default text would
    # incorrectly look like a no-op re-upload and this test's whole
    # premise (activating a genuinely different resume DOES reset
    # matched/missing_skills) wouldn't be exercised.
    version_a = _seed_resume(db_session, 1, is_active=True, embedding=EMBEDDING_A, text="resume text AAA")
    version_b = _seed_resume(db_session, 2, is_active=False, embedding=EMBEDDING_B, text="resume text BBB")
    company = Company(name="checkr")
    source = JobSource(name="greenhouse_api")
    db_session.add_all([company, source])
    db_session.commit()
    job = _seed_job(
        db_session, company, source, "Backend Engineer", EMBEDDING_A,
        matched=["Python"], missing=["Go"],
    )

    response = api_client.patch(f"/resumes/{version_b.id}/activate")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == version_b.id
    assert body["is_active"] is True

    db_session.expire_all()
    assert db_session.get(ResumeVersion, version_a.id).is_active is False
    assert db_session.get(ResumeVersion, version_b.id).is_active is True

    reloaded_job = db_session.get(JobPosting, job.id)
    assert reloaded_job.matched_skills is None
    assert reloaded_job.missing_skills is None
    # See the equivalent assertion in test_upload_resets_matched_and_
    # missing_skills_on_all_jobs for why this SQL-level IS NULL check is
    # necessary and not redundant with the two assertions above.
    assert db_session.query(JobPosting).filter(JobPosting.matched_skills.is_(None)).count() == 1
    assert db_session.query(JobPosting).filter(JobPosting.missing_skills.is_(None)).count() == 1


def test_activate_computes_missing_embedding_before_activating(api_client, db_session, stub_embed_text):
    _seed_resume(db_session, 1, is_active=True, embedding=EMBEDDING_A)
    never_embedded = _seed_resume(db_session, 2, is_active=False, embedding=None, text="BBB never embedded yet")

    response = api_client.patch(f"/resumes/{never_embedded.id}/activate")
    assert response.status_code == 200
    assert "BBB" in stub_embed_text[0]

    db_session.expire_all()
    reloaded = db_session.get(ResumeVersion, never_embedded.id)
    assert reloaded.is_active is True
    assert reloaded.embedding is not None


def test_activate_already_active_is_noop_and_preserves_skills(api_client, db_session):
    active = _seed_resume(db_session, 1, is_active=True, embedding=EMBEDDING_A)
    company = Company(name="checkr")
    source = JobSource(name="greenhouse_api")
    db_session.add_all([company, source])
    db_session.commit()
    job = _seed_job(
        db_session, company, source, "Backend Engineer", EMBEDDING_A,
        matched=["Python"], missing=["Go"],
    )

    response = api_client.patch(f"/resumes/{active.id}/activate")
    assert response.status_code == 200
    assert response.json()["is_active"] is True

    db_session.expire_all()
    # Already active - reactivating it is a no-op, so matched/missing_skills
    # must NOT have been wiped.
    reloaded_job = db_session.get(JobPosting, job.id)
    assert reloaded_job.matched_skills == ["Python"]
    assert reloaded_job.missing_skills == ["Go"]


def test_activate_404_for_missing_resume(api_client, db_session):
    response = api_client.patch("/resumes/999999/activate")
    assert response.status_code == 404


# ---------------------------------------------------------------------
# Point 4: the live match-score query must resolve "the active resume"
# dynamically at query time (is_active=true), never a cached/hardcoded
# reference. This is the test that would have caught a hardcoded-resume
# bug: it swaps which version is active *mid-test*, on the same running
# api_client/app instance (no restart), and asserts GET /jobs' scores
# actually change on the very next request.
# ---------------------------------------------------------------------


def test_activating_a_different_resume_changes_live_match_scores(api_client, db_session):
    version_a = _seed_resume(db_session, 1, is_active=True, embedding=EMBEDDING_A)
    version_b = _seed_resume(db_session, 2, is_active=False, embedding=EMBEDDING_B)
    company = Company(name="checkr")
    source = JobSource(name="greenhouse_api")
    db_session.add_all([company, source])
    db_session.commit()
    # job_matching_a's embedding is identical to version_a (score ~1.0
    # against A, ~0.0 against B) and orthogonal to version_b - the
    # inverse of job_matching_b.
    job_matching_a = _seed_job(db_session, company, source, "Matches A", EMBEDDING_A)
    job_matching_b = _seed_job(db_session, company, source, "Matches B", EMBEDDING_B)

    before = api_client.get("/jobs").json()
    scores_before = {item["job_title"]: item["match_score"] for item in before["items"]}
    assert scores_before["Matches A"] == pytest.approx(1.0, abs=1e-6)
    assert scores_before["Matches B"] == pytest.approx(0.0, abs=1e-6)

    activate_response = api_client.patch(f"/resumes/{version_b.id}/activate")
    assert activate_response.status_code == 200

    after = api_client.get("/jobs").json()
    scores_after = {item["job_title"]: item["match_score"] for item in after["items"]}
    # If GET /jobs had cached or hardcoded which resume is "active" (at
    # import time, in a module global, etc.) these scores would be
    # unchanged from `before` despite the activation swap above having
    # genuinely happened in the DB - this assertion is what would fail.
    assert scores_after["Matches A"] == pytest.approx(0.0, abs=1e-6)
    assert scores_after["Matches B"] == pytest.approx(1.0, abs=1e-6)

    # And reactivating the original version reverts scores back - proof
    # the resolution is fully dynamic in both directions, not a one-way
    # invalidation of some cache.
    api_client.patch(f"/resumes/{version_a.id}/activate")
    reverted = api_client.get("/jobs").json()
    scores_reverted = {item["job_title"]: item["match_score"] for item in reverted["items"]}
    assert scores_reverted["Matches A"] == pytest.approx(1.0, abs=1e-6)
    assert scores_reverted["Matches B"] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------
# Content-hash no-op check (huntloop.api.routers.resumes) - repeated
# uploads of byte-identical content must not repeatedly wipe
# matched_skills/missing_skills across the whole job_postings table.
# ---------------------------------------------------------------------


def test_reuploading_identical_resume_content_skips_the_skills_reset(
    api_client, db_session, stub_embed_text, _uploaded_paths
):
    _seed_resume(db_session, 1, is_active=True, embedding=EMBEDDING_A, text="AAA original")
    company = Company(name="checkr")
    source = JobSource(name="greenhouse_api")
    db_session.add_all([company, source])
    db_session.commit()
    job = _seed_job(
        db_session, company, source, "Backend Engineer", EMBEDDING_A,
        matched=["Python", "AWS"], missing=["Go"],
    )

    # Same extracted-text content as the currently-active version 1
    # ("AAA original") - a real duplicate upload, e.g. the user
    # re-submitting the same file.
    pdf_bytes = _minimal_pdf_bytes("AAA original")
    response = api_client.post("/resumes/upload", files={"file": ("resume.pdf", pdf_bytes, "application/pdf")})
    assert response.status_code == 201
    assert response.json()["version_number"] == 2

    db_session.expire_all()
    # The reset must NOT have run - this job's real, previously-computed
    # skills-match data should be completely untouched.
    reloaded_job = db_session.get(JobPosting, job.id)
    assert reloaded_job.matched_skills == ["Python", "AWS"]
    assert reloaded_job.missing_skills == ["Go"]

    # A genuinely new, different resume version should still have been
    # created and marked active - only the reset is skipped, nothing else.
    new_active = db_session.query(ResumeVersion).filter_by(version_number=2).one()
    assert new_active.is_active is True
    old = db_session.query(ResumeVersion).filter_by(version_number=1).one()
    assert old.is_active is False


def test_reuploading_genuinely_different_resume_content_still_resets_skills(
    api_client, db_session, stub_embed_text, _uploaded_paths
):
    _seed_resume(db_session, 1, is_active=True, embedding=EMBEDDING_A, text="AAA original")
    company = Company(name="checkr")
    source = JobSource(name="greenhouse_api")
    db_session.add_all([company, source])
    db_session.commit()
    job = _seed_job(
        db_session, company, source, "Backend Engineer", EMBEDDING_A,
        matched=["Python", "AWS"], missing=["Go"],
    )

    pdf_bytes = _minimal_pdf_bytes("BBB completely different content")
    response = api_client.post("/resumes/upload", files={"file": ("resume2.pdf", pdf_bytes, "application/pdf")})
    assert response.status_code == 201

    db_session.expire_all()
    reloaded_job = db_session.get(JobPosting, job.id)
    assert reloaded_job.matched_skills is None
    assert reloaded_job.missing_skills is None


def test_reactivating_a_version_with_identical_content_skips_the_skills_reset(api_client, db_session):
    # Two DB rows, deliberately given the SAME extracted text (e.g. the
    # same file uploaded twice at different times, each creating its own
    # resume_versions row) - reactivating the older one should be
    # recognized as a content no-op even though it's a different row id
    # than the one currently active.
    _seed_resume(db_session, 1, is_active=False, embedding=EMBEDDING_A, text="identical content")
    active = _seed_resume(db_session, 2, is_active=True, embedding=EMBEDDING_A, text="identical content")
    older = db_session.query(ResumeVersion).filter_by(version_number=1).one()

    company = Company(name="checkr")
    source = JobSource(name="greenhouse_api")
    db_session.add_all([company, source])
    db_session.commit()
    job = _seed_job(
        db_session, company, source, "Backend Engineer", EMBEDDING_A,
        matched=["Python"], missing=["Go"],
    )

    response = api_client.patch(f"/resumes/{older.id}/activate")
    assert response.status_code == 200

    db_session.expire_all()
    reloaded_job = db_session.get(JobPosting, job.id)
    assert reloaded_job.matched_skills == ["Python"]
    assert reloaded_job.missing_skills == ["Go"]
    assert db_session.get(ResumeVersion, older.id).is_active is True
    assert db_session.get(ResumeVersion, active.id).is_active is False


# ---------------------------------------------------------------------
# Per-IP rate limiting (reuses huntloop.api.routers.drafting._RateLimiter)
# ---------------------------------------------------------------------


def test_upload_rate_limit_triggers_after_threshold(api_client, db_session, stub_embed_text, _uploaded_paths):
    statuses = []
    for i in range(_upload_rate_limiter.max_requests):
        pdf_bytes = _minimal_pdf_bytes(f"AAA content {i}")
        resp = api_client.post("/resumes/upload", files={"file": (f"r{i}.pdf", pdf_bytes, "application/pdf")})
        statuses.append(resp.status_code)
    assert all(s == 201 for s in statuses), statuses

    pdf_bytes = _minimal_pdf_bytes("AAA one too many")
    over_limit = api_client.post("/resumes/upload", files={"file": ("over.pdf", pdf_bytes, "application/pdf")})
    assert over_limit.status_code == 429
    assert "rate limit exceeded" in over_limit.json()["detail"].lower()


def test_activate_rate_limit_triggers_after_threshold(api_client, db_session):
    # Seed one extra resume version per request beyond the limit, each
    # already active so activate_resume's early "already active" no-op
    # branch short-circuits BEFORE the rate limit would matter for
    # correctness of the underlying operation - but _enforce_rate_limit
    # runs first regardless of that branch, which is exactly what this
    # test is checking.
    resume = _seed_resume(db_session, 1, is_active=True, embedding=EMBEDDING_A)

    statuses = []
    for _ in range(_activate_rate_limiter.max_requests):
        resp = api_client.patch(f"/resumes/{resume.id}/activate")
        statuses.append(resp.status_code)
    assert all(s == 200 for s in statuses), statuses

    over_limit = api_client.patch(f"/resumes/{resume.id}/activate")
    assert over_limit.status_code == 429
    assert "rate limit exceeded" in over_limit.json()["detail"].lower()


# ---------------------------------------------------------------------
# Hard upload-size cap (MaxUploadSizeMiddleware) - see
# tests/test_max_upload_size_middleware.py for a lower-level unit test
# proving the middleware cuts off mid-stream rather than after fully
# buffering an oversized body. This test proves it's actually wired into
# the real app for the real /resumes/upload path end to end.
# ---------------------------------------------------------------------


def test_oversized_upload_is_rejected_with_413(api_client, db_session):
    oversized = b"x" * (MAX_UPLOAD_BYTES + 1024)
    response = api_client.post(
        "/resumes/upload",
        files={"file": ("huge.pdf", oversized, "application/pdf")},
    )
    assert response.status_code == 413

    # Nothing should have been written to resume_versions or to disk for
    # a request this large - it was rejected before upload_resume() (the
    # route handler) ever ran.
    assert db_session.query(ResumeVersion).count() == 0

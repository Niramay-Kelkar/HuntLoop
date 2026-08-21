"""
Regression tests for scripts/detect_and_store_ats.py's upsert_company_ats().

Reproduces the exact bug found while verifying that script (SESSIONS.md,
2026-08-21): a transient detect_ats() fetch error (e.g. a network
ReadTimeout) used to silently overwrite a company's existing,
previously-successful ats_platform/ats_token with unknown/NULL, even
though nothing about the company's real ATS had changed. Fixed by
treating detect_ats()'s `error` field as "couldn't check" and leaving a
prior successful value alone in that case - distinct from a genuine
"checked successfully, no pattern matched" result, which should still
overwrite/store as "unknown".

scripts/ isn't a package and isn't on the pytest pythonpath (only src/
is, per pytest.ini) - imported here by adding it to sys.path directly,
same as any other standalone script would be run.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import detect_and_store_ats  # noqa: E402

from huntloop.db_models import Company  # noqa: E402


def make_result(ats="unknown", identifier=None, error=None):
    """Shaped like a real detect_ats() return value."""
    return {
        "source_url": "https://example.com/careers",
        "ats": ats,
        "identifier": identifier,
        "matched_url": None,
        "http_status": None if error else 200,
        "render_attempted": False,
        "error": error,
    }


def test_fetch_error_does_not_overwrite_existing_successful_value(db_session, caplog):
    # Seed a previously-successful detection, same as a real prior run of
    # the script would have stored.
    db_session.add(Company(name="checkr", ats_platform="greenhouse", ats_token="checkr"))
    db_session.commit()

    # A transient fetch error, same shape as the real ReadTimeout observed
    # while verifying this script live.
    error_result = make_result(error="ReadTimeout: HTTPSConnectionPool(...): Read timed out. (read timeout=10)")

    with caplog.at_level("WARNING"):
        detect_and_store_ats.upsert_company_ats(
            db_session, "checkr", "https://job-boards.greenhouse.io/checkr", error_result
        )

    company = db_session.query(Company).filter_by(name="checkr").one()
    assert company.ats_platform == "greenhouse"
    assert company.ats_token == "checkr"
    assert "detection attempt failed" in caplog.text
    assert "checkr" in caplog.text


def test_genuine_no_match_result_still_overwrites_to_unknown(db_session):
    # A company previously (incorrectly, for this test's purposes) marked
    # as some platform - a real, error-free "checked, nothing matched"
    # result must still overwrite it, e.g. if the company migrated ATS
    # platforms or an earlier detection was wrong.
    db_session.add(Company(name="brex", ats_platform="greenhouse", ats_token="brex"))
    db_session.commit()

    clean_unknown_result = make_result(ats="unknown", identifier=None, error=None)

    detect_and_store_ats.upsert_company_ats(db_session, "brex", "https://brex.com/careers", clean_unknown_result)

    company = db_session.query(Company).filter_by(name="brex").one()
    assert company.ats_platform == "unknown"
    assert company.ats_token is None


def test_error_on_never_before_detected_company_stores_unknown(db_session):
    # No prior successful value exists to protect - storing unknown/NULL
    # is fine in this case.
    db_session.add(Company(name="new-co"))  # ats_platform/ats_token left NULL
    db_session.commit()

    error_result = make_result(error="ConnectionError: DNS resolution failed")

    detect_and_store_ats.upsert_company_ats(db_session, "new-co", "https://new-co.example/careers", error_result)

    company = db_session.query(Company).filter_by(name="new-co").one()
    assert company.ats_platform == "unknown"
    assert company.ats_token is None

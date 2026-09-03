"""
Unit tests for scripts/detect_ats_for_sponsors.py's slug_candidates() -
the pure, network-free part of the sponsor-ATS coverage script. The
probing itself (Greenhouse/Lever HTTP) is not exercised here; it's a live
external call verified by actually running the script (see SESSIONS.md
2026-08-29).

scripts/ isn't on the pytest pythonpath (only src/ is, per pytest.ini) -
imported here by adding it to sys.path directly.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import detect_ats_for_sponsors as d  # noqa: E402


def test_single_word_name_is_emitted_as_is():
    # normalize_employer_name strips INC/LLC upstream, so a one-word name
    # like "STRIPE" is the real input shape.
    cands = d.slug_candidates("STRIPE")
    assert cands == ["stripe"]


def test_trailing_noise_words_are_progressively_stripped():
    cands = d.slug_candidates("COGNIZANT TECHNOLOGY SOLUTIONS US")
    assert "cognizanttechnologysolutionsus" in cands
    assert "cognizanttechnologysolutions" in cands
    assert "cognizanttechnology" in cands
    # first-two-words fallback (the fully-trimmed list is still > 2 words)
    assert "cognizanttechnology" in cands


def test_no_bare_first_word_slug_for_multiword_names():
    # The first probe run's biggest false-positive source: "GENERAL
    # MOTORS" -> "general" hitting an unrelated board. Must not happen.
    for name, bad in [
        ("GENERAL MOTORS", "general"),
        ("US BANK NATIONAL", "us"),
        ("CHARLES SCHWAB & COMPANY", "charles"),
        ("CAPITAL ONE SERVICES", "capital"),
    ]:
        assert bad not in d.slug_candidates(name), name


def test_ampersand_is_dropped_not_slugified():
    cands = d.slug_candidates("JPMORGAN CHASE &")
    assert all("&" not in c for c in cands)
    assert "jpmorganchase" in cands
    assert "jpmorgan-chase" in cands


def test_hyphen_and_joined_variants_both_present():
    cands = d.slug_candidates("PALO ALTO NETWORKS")
    assert "paloaltonetworks" in cands
    assert "palo-alto-networks" in cands


def test_empty_and_ampersand_only_names_yield_no_candidates():
    assert d.slug_candidates("") == []
    assert d.slug_candidates("&") == []


def test_candidates_are_unique_and_ordered():
    cands = d.slug_candidates("PALO ALTO NETWORKS GROUP")
    assert cands == list(dict.fromkeys(cands))
    assert cands[0] == "paloaltonetworksgroup"


def test_known_noncanonical_slugs_are_rejected(monkeypatch):
    # linkedin / microsoftcorporation are real Greenhouse demo/test tenants
    # - probe must short-circuit them regardless of what the API returns.
    monkeypatch.setattr(d, "_get", lambda url: (_ for _ in ()).throw(AssertionError("should not be called")))
    assert d.probe_greenhouse("linkedin", "LINKEDIN CORPORATION") is False
    assert d.probe_lever("microsoftcorporation") is False


def test_short_or_common_reduced_slugs_are_gated():
    # Second probe run's residual false positives: a multi-word legal
    # name collapsing (via noise-stripping) to a short or generic single
    # word that collides with an unrelated real board.
    for name, bad in [
        ("FLEX CONSULTING GROUP", "flex"),
        ("YES TECHNOLOGIES", "yes"),
        ("OATH HOLDINGS", "oath"),
        ("NATIONAL CONSULTING GROUP", "national"),
        ("CAPITAL GROUP COMPANIES GLOBAL", "capital"),
        ("HS TECHNOLOGIES", "hs"),
    ]:
        assert bad not in d.slug_candidates(name), name
    # ...but a genuine single-word name and a long distinctive reduction
    # still go through.
    assert "okta" in d.slug_candidates("OKTA")
    assert "palantir" in d.slug_candidates("PALANTIR TECHNOLOGIES")
    assert "robinhood" in d.slug_candidates("ROBINHOOD MARKETS")
    # A short acronym whose other words are ALL noise is still tried
    # (real Greenhouse customers ASM/NICE/IMC) - the name-similarity gate
    # in probe_greenhouse is what filters the bad acronym collisions.
    assert "asm" in d.slug_candidates("ASM AMERICA")
    assert "nice" in d.slug_candidates("NICE SYSTEMS")
    # ...but not when a non-noise word sits in between.
    assert "rpa" in d.slug_candidates("RPA TECHNOLOGY")  # TECHNOLOGY is noise
    assert "tec" not in d.slug_candidates("TEC HOLDINGS CONSULTANTS")  # CONSULTANTS not noise


def test_upsert_hits_inserts_new_and_fills_null_or_unknown(db_session):
    from huntloop.db_models import Company
    db_session.add_all([
        Company(name="brex", ats_platform="unknown", ats_token=None),
        Company(name="palantir", ats_platform="lever", ats_token="palantir"),
        Company(name="figma"),  # ats_platform NULL
    ])
    db_session.commit()

    hits = [
        {"slug": "brex", "ats": "greenhouse", "employer": "BREX", "filings": 126},
        {"slug": "palantir", "ats": "greenhouse", "employer": "PALANTIR", "filings": 241},
        {"slug": "figma", "ats": "greenhouse", "employer": "FIGMA", "filings": 107},
        {"slug": "stripe", "ats": "greenhouse", "employer": "STRIPE", "filings": 783},
    ]
    inserted, updated = d.upsert_hits(db_session, hits)

    assert inserted == 1  # stripe
    assert updated == 2    # brex (unknown->greenhouse), figma (NULL->greenhouse)
    assert db_session.query(Company).filter_by(name="brex").one().ats_platform == "greenhouse"
    assert db_session.query(Company).filter_by(name="figma").one().ats_platform == "greenhouse"
    # an existing, different, successful platform is left alone
    assert db_session.query(Company).filter_by(name="palantir").one().ats_platform == "lever"
    assert db_session.query(Company).filter_by(name="stripe").one().ats_platform == "greenhouse"


def test_name_matches_gate():
    assert d._name_matches("Checkr", "CHECKR")
    assert d._name_matches("ASM", "ASM AMERICA")
    assert d._name_matches("ROBLOX CORPORATION", "ROBLOX CORPORATION")
    # clear mismatches from real slug collisions
    assert not d._name_matches("Headspace", "HS TECHNOLOGIES")
    assert not d._name_matches("Oath Animal Hospital", "OATH HOLDINGS")

"""
One-off / repeatable coverage script: take the real universe of distinct
sponsor employer names already in ``lca_disclosures`` (filtered to a
meaningful filing footprint) and detect which of them run their careers
board on Greenhouse or Lever, by probing those platforms' *public job-board
APIs directly* with candidate slugs derived from the employer name.

Why not ``huntloop.ats_detection.detect_ats()``?  That function needs a
real careers URL per company (a live HTTP GET + optional Playwright
render).  We have ~108k distinct normalized sponsor names and zero careers
URLs for them.  Neither Greenhouse nor Lever publishes any reverse
"list our customers" endpoint (confirmed against their own API docs,
2026-08-29 - see SESSIONS.md), so the only feasible approach at this scale
is: guess a slug from the employer name, hit the board API, and treat a
200 + non-empty board as a confirmed customer.

    GET https://boards-api.greenhouse.io/v1/boards/{slug}
    GET https://api.lever.co/v0/postings/{slug}?mode=json

Slug guessing is lossy - real board slugs rarely match DOL legal filing
names exactly (legal suffixes, "&", abbreviations, branding).  We try a
handful of variants per company and stop at the first hit, but the match
count this produces is a LOWER BOUND, not a definitive census.

Run:  python scripts/detect_ats_for_sponsors.py [--min-filings N] [--limit N] [--commit]

Without --commit it only writes scratch_sponsor_ats_detection.json (a
gitignored report) and prints the breakdown.  With --commit it also
upserts every Greenhouse/Lever hit into the ``companies`` table
(name = ats_token = discovered slug), so main.py's orchestrator picks
them up on the next scrape - pure coverage expansion, no new spider code.

RE-RUN CADENCE: re-run this after each new quarterly DOL LCA file is
ingested (scripts/ingest_lca_disclosures.py), not on a fixed calendar -
a new quarter adds new employers and bumps others past the filing
threshold.  It is safe to re-run: it only ever inserts new companies rows
or leaves existing ones untouched.
"""
import argparse
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv
from rapidfuzz.fuzz import token_set_ratio
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

load_dotenv()

from huntloop.db_models import Company
from huntloop.matching.normalize import normalize_employer_name
from huntloop.settings import DATABASE_URL

logger = logging.getLogger(__name__)

REPORT_PATH = "scratch_sponsor_ats_detection.json"
DEFAULT_MIN_FILINGS = 20

_GREENHOUSE_ROOT_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}"
_GREENHOUSE_JOBS_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json&limit=25"

# Require at least this many live postings before treating a board as a
# real, scrapeable customer board. >0 was not enough: probing turned up
# tiny squatter / sandbox / Greenhouse-demo boards on generic slugs
# ("us" -> 1 junk job, "general" -> 1, "linkedin" -> 53 obvious test
# rows like "123123" / "Bug Bash Job"). 3 is a low bar that still cuts
# the 1-2-job squatters; the demo-board case is handled by _looks_like_test().
_MIN_POSTINGS = 3

# Boards a probe confidently hits (real slug, non-trivial job count) that
# are demonstrably NOT the company's real hiring board - Greenhouse
# demo/test tenants registered under a well-known company name. Found by
# eyeballing the first probe run's hits (2026-08-29). Keep this short and
# evidence-based, not a guess-list.
_KNOWN_NONCANONICAL_SLUGS = {
    "linkedin",             # "LI Test Company" - 53 rows, all test data
    "microsoftcorporation",  # 2 sandbox rows ("Health Strategy...| TBD")
}
_USER_AGENT = "HuntLoop-ATS-coverage/1.0 (+sponsorship-matching research)"
_TIMEOUT = 15
_MAX_WORKERS = 24

# Generic trailing words that show up in DOL legal names but almost never
# in a real board slug. Stripped (from the end, iteratively) to form extra
# slug candidates - the un-stripped variants are still tried too.
_TRAILING_NOISE = {
    "US", "USA", "AMERICA", "AMERICAS", "NA", "GLOBAL", "GROUP", "HOLDINGS",
    "HOLDING", "TECHNOLOGIES", "TECHNOLOGY", "SOLUTIONS", "SERVICES",
    "SYSTEMS", "CONSULTING", "INTERNATIONAL", "INDUSTRIES", "ENTERPRISES",
    "PARTNERS", "LIMITED", "INTL", "COMPANY", "COMPANIES", "CORPORATION",
    "CORP", "INCORPORATED", "MARKETS", "ASSOCIATION", "ASSOCIATES",
}
_STRIP_TOKENS_RE = re.compile(r"[^a-z0-9]+")

# When trailing-noise stripping collapses a multi-word legal name down to
# a SINGLE word, that word is only used as a slug candidate if it's at
# least this long and not a common/generic word. Without this, names like
# "FLEX CONSULTING GROUP" -> "flex", "YES TECHNOLOGIES" -> "yes",
# "NATIONAL CONSULTING GROUP" -> "national", "OATH HOLDINGS" -> "oath"
# each collide with an unrelated real board (Flextronics, "Yes LLC",
# a board literally named "NATIONAL", "Oath Animal Hospital"). A genuine
# single-word company name (OKTA, ROKU, NURO) is unaffected - it never
# goes through the reduction path. (2026-08-29, second probe run.)
# A reduced single-word slug is allowed if it's >= 6 chars, OR it's a
# short (>= 3) acronym-style token where every OTHER word in the original
# name was pure noise ("ASM AMERICA" -> "asm", "NICE SYSTEMS" -> "nice",
# "IMC AMERICAS" -> "imc" are all real Greenhouse customers). In either
# case it must not be a plain dictionary / generic word - those collide
# with unrelated real boards that legitimately ARE named that word
# ("FLEX CONSULTING GROUP" vs Flextronics, "YES TECHNOLOGIES" vs "Yes
# LLC", "AURA GLOBAL SOLUTIONS" vs Aura). The Greenhouse name-similarity
# gate (_name_matches) then catches the acronym collisions where the
# board name doesn't match ("HS TECHNOLOGIES" -> board "Headspace").
_MIN_REDUCED_SLUG_LEN = 6
_MIN_ACRONYM_SLUG_LEN = 3
_COMMON_WORDS = {
    "national", "general", "global", "capital", "american", "standard",
    "premier", "allied", "united", "federal", "creative", "dynamic",
    "strategic", "advanced", "integrated", "unified", "superior",
    "central", "pacific", "atlantic", "modern", "digital", "quantum",
    "flex", "aura", "yes", "oath", "first", "prime", "next", "core",
    "edge", "peak", "apex", "base", "main", "nova", "spark", "summit",
    "bridge", "beacon", "true", "bold", "pure", "vertex", "nexus",
    "blockchain", "crypto", "cloud", "data", "tech", "systems", "labs",
}


def slug_candidates(employer_normalized: str) -> list[str]:
    """Derive an ordered, de-duplicated list of candidate board slugs from
    a normalized DOL employer name. Lossy by design - see module docstring.

    Deliberately does NOT emit a bare first-word slug for a multi-word
    name: the first probe run (2026-08-29) showed that produced many
    false positives on generic fragments ("GENERAL MOTORS" -> "general",
    "US BANK ..." -> "us", "CHARLES SCHWAB ..." -> "charles"), each
    hitting an unrelated small board. A single-word name is still emitted
    as-is; multi-word names only ever try the full joined/hyphenated
    forms (and their trailing-noise-trimmed prefixes)."""
    words = [w for w in re.split(r"[\s/]+", employer_normalized.upper()) if w and w != "&"]
    if not words:
        return []

    # Progressively strip generic trailing noise words to build word-lists.
    word_lists = [words]
    trimmed = list(words)
    while len(trimmed) > 1 and trimmed[-1] in _TRAILING_NOISE:
        trimmed = trimmed[:-1]
        word_lists.append(list(trimmed))

    candidates: list[str] = []
    other_words_all_noise = all(w in _TRAILING_NOISE for w in words[1:])

    def _add_wordlist(wl: list[str], is_reduction: bool) -> None:
        # A single-word slug that only exists because noise-stripping /
        # first-two-words truncation collapsed a multi-word name is the
        # main false-positive source - gate it by length + a common-word
        # stoplist. The unmodified full name (is_reduction=False) is
        # always allowed.
        if is_reduction and len(wl) == 1:
            w = wl[0].lower()
            min_len = _MIN_ACRONYM_SLUG_LEN if other_words_all_noise else _MIN_REDUCED_SLUG_LEN
            if len(w) < min_len or w in _COMMON_WORDS:
                return
        joined = _STRIP_TOKENS_RE.sub("", "".join(wl).lower())
        hyphen = _STRIP_TOKENS_RE.sub("-", " ".join(wl).lower()).strip("-")
        for c in (joined, hyphen):
            if c and c not in candidates:
                candidates.append(c)

    for i, wl in enumerate(word_lists):
        _add_wordlist(wl, is_reduction=(i > 0))

    # First TWO words - covers names where a meaningful (non-noise) third+
    # word isn't in the real slug (multi-entity "X Y Group Inc" names
    # often board under just "X Y" or "X").
    if len(word_lists[-1]) > 2:
        _add_wordlist(word_lists[-1][:2], is_reduction=True)

    return candidates


def _get(url: str) -> requests.Response | None:
    try:
        return requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT)
    except requests.RequestException:
        return None


def _looks_like_test(board_name: str) -> bool:
    n = board_name.lower()
    return "test" in n or "demo" in n or "sandbox" in n or "dummy" in n


# Minimum fuzzy similarity between the Greenhouse board's own display
# name and the DOL employer name before a probe hit is trusted. Catches
# slug collisions the length/stoplist gate misses: "hs" -> board named
# "Headspace" (vs "HS TECHNOLOGIES"), "xai" -> "SpaceXAI" (vs "XAI").
# Deliberately low (a real match like "ASM" vs "ASM AMERICA" or "Roblox"
# vs "ROBLOX CORPORATION" scores ~65-75) - this only rejects the clear
# name mismatches, it's not entity resolution.
_NAME_SIM_MIN = 55


def _name_matches(board_name: str, employer_normalized: str) -> bool:
    return token_set_ratio(normalize_employer_name(board_name), employer_normalized) >= _NAME_SIM_MIN


def probe_greenhouse(slug: str, employer_normalized: str) -> bool:
    if slug in _KNOWN_NONCANONICAL_SLUGS:
        return False
    root = _get(_GREENHOUSE_ROOT_URL.format(slug=slug))
    if root is None or root.status_code != 200:
        return False
    try:
        meta = root.json()
    except ValueError:
        return False
    if not isinstance(meta, dict) or not meta.get("name") or _looks_like_test(meta["name"]):
        return False
    if not _name_matches(meta["name"], employer_normalized):
        return False

    r = _get(_GREENHOUSE_JOBS_URL.format(slug=slug))
    if r is None or r.status_code != 200:
        return False
    try:
        data = r.json()
    except ValueError:
        return False
    return isinstance(data, dict) and isinstance(data.get("jobs"), list) and len(data["jobs"]) >= _MIN_POSTINGS


def probe_lever(slug: str) -> bool:
    if slug in _KNOWN_NONCANONICAL_SLUGS:
        return False
    r = _get(_LEVER_URL.format(slug=slug))
    if r is None or r.status_code != 200:
        return False
    try:
        data = r.json()
    except ValueError:
        return False
    # Lever returns a JSON list (possibly empty if 0 open roles). An empty
    # list from a 200 still means the site exists, but we can't distinguish
    # that from Lever's soft-404 behavior reliably, so require a few
    # postings - same bar as Greenhouse.
    return isinstance(data, list) and len(data) >= _MIN_POSTINGS


def detect_one(row: dict) -> dict:
    name = row["employer"]
    for slug in slug_candidates(name):
        if probe_greenhouse(slug, name):
            return {**row, "ats": "greenhouse", "slug": slug}
        if probe_lever(slug):
            return {**row, "ats": "lever", "slug": slug}
    return {**row, "ats": "neither", "slug": None}


def load_sponsors(session, min_filings: int, limit: int | None) -> list[dict]:
    q = text(
        "SELECT employer_name_normalized AS employer, count(*) AS filings "
        "FROM lca_disclosures GROUP BY 1 HAVING count(*) >= :m ORDER BY filings DESC"
    )
    rows = session.execute(q, {"m": min_filings}).fetchall()
    if limit:
        rows = rows[:limit]
    return [{"employer": r.employer, "filings": int(r.filings)} for r in rows]


def upsert_hits(session, hits: list[dict]) -> tuple[int, int]:
    """Insert a companies row for each hit slug not already present, and
    fill in ats_platform for existing rows that have none yet OR were
    previously left as 'unknown' (a real prior detection that failed to
    match anything - e.g. 'brex', which detect_and_store_ats.py stored as
    'unknown' before it moved to Greenhouse). Never overwrites an
    existing, different, already-successful platform. Returns
    (inserted, updated)."""
    inserted = updated = 0
    for h in hits:
        slug = h["slug"]
        existing = session.query(Company).filter_by(name=slug).first()
        if existing is None:
            session.add(Company(name=slug, ats_platform=h["ats"], ats_token=slug))
            inserted += 1
        elif existing.ats_platform in (None, "unknown"):
            existing.ats_platform = h["ats"]
            existing.ats_token = slug
            updated += 1
    session.commit()
    return inserted, updated


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-filings", type=int, default=DEFAULT_MIN_FILINGS)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    engine = create_engine(DATABASE_URL, echo=False)
    session = sessionmaker(bind=engine)()

    sponsors = load_sponsors(session, args.min_filings, args.limit)
    logger.info(
        "Probing %d distinct sponsor employers (>= %d filings) against Greenhouse + Lever",
        len(sponsors), args.min_filings,
    )

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        for i, res in enumerate(pool.map(detect_one, sponsors), 1):
            results.append(res)
            if i % 250 == 0:
                logger.info("  ... %d/%d probed", i, len(sponsors))

    gh = [r for r in results if r["ats"] == "greenhouse"]
    lv = [r for r in results if r["ats"] == "lever"]
    neither = [r for r in results if r["ats"] == "neither"]

    report = {
        "min_filings": args.min_filings,
        "total_probed": len(results),
        "greenhouse": len(gh),
        "lever": len(lv),
        "neither": len(neither),
        "hits": sorted(
            [{k: r[k] for k in ("employer", "filings", "ats", "slug")} for r in gh + lv],
            key=lambda r: -r["filings"],
        ),
    }
    with open(REPORT_PATH, "w") as fh:
        json.dump(report, fh, indent=2)

    print(f"\n=== ATS detection over {len(results)} distinct sponsors (>= {args.min_filings} filings) ===")
    print(f"Greenhouse : {len(gh)}")
    print(f"Lever      : {len(lv)}")
    print(f"Neither    : {len(neither)}  (future-spider candidates or genuinely not on GH/Lever)")
    print(f"Match rate : {(len(gh) + len(lv)) / len(results):.1%}  (LOWER BOUND - slug guessing is lossy)")
    print(f"Report     : {REPORT_PATH}")

    if args.commit:
        inserted, updated = upsert_hits(session, gh + lv)
        print(f"\nCommitted: {inserted} new companies rows inserted, {updated} existing rows "
              f"updated (NULL/'unknown' -> detected platform) for scraping coverage.")
    else:
        print("\n(dry run - re-run with --commit to add these to the companies table)")

    session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()

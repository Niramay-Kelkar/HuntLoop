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
                                                 [--confirmations FILE]

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

BARE-FIRST-WORD CANDIDATE (added 2026-09-02, see SESSIONS.md's "Add a
gated bare-first-word candidate for 2-word company names" entry): a real
follow-up investigation (SESSIONS.md's "Investigate white-labeled
Greenhouse/Lever on custom domains" entry) found that ``slug_candidates``
above deliberately never emits a bare first-word slug for a 2-word
employer name whose second word isn't recognized generic corporate noise
(e.g. "RIPPLE LABS" - "LABS" isn't in ``_TRAILING_NOISE``), and that this
specific, narrow gap has real (if modest) prevalence - a 44-employer
targeted sample found 2 confirmed genuine misses (Faire Wholesale ->
faire, Highnote Platform -> highnote) alongside 2 more hits that would
have been false positives without the existing stoplist/name-similarity
guards. ``bare_first_word_candidate()`` below adds exactly that one
narrow candidate, on top of (never instead of) ``slug_candidates()``'s
own unmodified candidates, and ONLY as a fallback tried after every
regular candidate has already missed. It goes through the exact same
live verification as every other candidate (a real API call, plus
``probe_greenhouse``'s existing board-name similarity check) - but,
because it's exactly the same collision-prone shape as the
``first-word-only``/``acronym`` candidate kinds this project already
treats as never-auto-store (see ``discover_ashby_job_board.py``'s
``_NON_AUTO_KINDS`` / ``discover_and_store_ashby.py``'s ``gate()``), a
hit via this candidate kind is NEVER auto-stored regardless of name
similarity - it is always held for explicit human confirmation via
``--confirmations`` (same file format as the Ashby/iCIMS/Gem onboarding
scripts: one slug per line, ``#`` comments ok). Every other candidate
kind's behavior (including whether *they* auto-store) is completely
unchanged by this addition.

REAL, CONFIRMED LIMITATION FOR LEVER (found while building this, not
assumed): unlike Greenhouse's board endpoint, which returns a
``{"name": ...}`` field used for ``_name_matches``'s board-name
cross-check, Lever's postings endpoint
(``api.lever.co/v0/postings/{slug}``) returns a bare JSON list of
postings with no company/org display-name field anywhere in the response
(checked live against real boards - wealthfront/palantir/kraken - full
key list has no name/org field, just per-posting ``hostedUrl``, which
only echoes the slug back). So ``probe_lever`` has never had a
name-similarity check for ANY candidate kind, not just this new one -
this addition doesn't weaken anything that existed, but it does mean a
Lever bare-first-word hit is verified by live-postings-count alone, with
no independent confirmation the board actually belongs to the queried
company. That's exactly why this candidate kind is always held rather
than ever auto-passing.
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

from huntloop.company_display_name import fetch_greenhouse_display_name, fetch_lever_display_name
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


# Minimum length for the bare-first-word candidate's first word. Looser
# than slug_candidates()'s own _MIN_REDUCED_SLUG_LEN (6) - that bar would
# block both confirmed real misses this candidate exists to catch
# ("FAIRE WHOLESALE" -> "faire" is 5 chars). Safe to be looser here
# specifically because, unlike a slug_candidates() reduction candidate,
# this candidate kind is NEVER auto-stored (see gate() below) - the real
# false-positive defense for this candidate is the mandatory human
# confirmation step, not the length bar. Still blocks the shortest,
# highest-collision-risk fragments ("US BANK" -> "us", "NA GROUP" -> "na").
_MIN_BARE_FIRST_WORD_LEN = 3


def bare_first_word_candidate(employer_normalized: str) -> str | None:
    """The one new, narrowly-scoped candidate this session adds (see the
    module docstring's "BARE-FIRST-WORD CANDIDATE" section) - NOT part of
    ``slug_candidates()`` above, which is left completely unmodified.

    Only fires for a name that is EXACTLY two words (after the same
    whitespace/``&``-splitting ``slug_candidates`` uses) whose second word
    is NOT recognized generic corporate noise - the precise pattern
    ``slug_candidates`` deliberately skips (its own docstring explains
    why: bare first-word guessing on a reduced/truncated name produced
    false positives like "GENERAL MOTORS" -> "general" in an earlier
    probe run). Still reuses ``_COMMON_WORDS`` (so "GENERAL MOTORS" ->
    "general" stays blocked here too), but uses a looser
    ``_MIN_BARE_FIRST_WORD_LEN`` length bar than ``slug_candidates()``'s
    own reduction candidates - see that constant's comment for why."""
    words = [w for w in re.split(r"[\s/]+", employer_normalized.upper()) if w and w != "&"]
    if len(words) != 2 or words[1] in _TRAILING_NOISE:
        return None
    w = words[0].lower()
    if len(w) < _MIN_BARE_FIRST_WORD_LEN or w in _COMMON_WORDS:
        return None
    return _STRIP_TOKENS_RE.sub("", w)


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


def _greenhouse_board_name(slug: str) -> str | None:
    """Reporting-only helper - re-fetches the board's own display name for
    a confirmed hit, so a held bare-first-word candidate's report row (and
    a --confirmations file written by a human) can show what the board is
    actually called, not just its slug. Never used to gate anything -
    probe_greenhouse already did the real name-similarity check."""
    root = _get(_GREENHOUSE_ROOT_URL.format(slug=slug))
    if root is None or root.status_code != 200:
        return None
    try:
        meta = root.json()
    except ValueError:
        return None
    return meta.get("name") if isinstance(meta, dict) else None


def detect_one(row: dict) -> dict:
    name = row["employer"]
    for slug in slug_candidates(name):
        if probe_greenhouse(slug, name):
            return {**row, "ats": "greenhouse", "slug": slug, "candidate_kind": "primary"}
        if probe_lever(slug):
            return {**row, "ats": "lever", "slug": slug, "candidate_kind": "primary"}

    # Fallback only - tried after every regular candidate has already
    # missed (see the module docstring's "BARE-FIRST-WORD CANDIDATE"
    # section). Never changes the outcome for a name that already
    # resolves via slug_candidates().
    bare = bare_first_word_candidate(name)
    if bare:
        if probe_greenhouse(bare, name):
            return {
                **row, "ats": "greenhouse", "slug": bare, "candidate_kind": "bare_first_word",
                "board_name": _greenhouse_board_name(bare),
            }
        if probe_lever(bare):
            return {**row, "ats": "lever", "slug": bare, "candidate_kind": "bare_first_word", "board_name": None}

    return {**row, "ats": "neither", "slug": None, "candidate_kind": None}


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
        # Real proper-name source per platform (see
        # huntloop.company_display_name) - fetched here so a newly
        # onboarded Greenhouse/Lever company gets display_name populated
        # at discovery time, not just via a later one-off backfill.
        if h["ats"] == "greenhouse":
            display_name = fetch_greenhouse_display_name(slug)
        elif h["ats"] == "lever":
            display_name = fetch_lever_display_name(slug)
        else:
            display_name = None
        existing = session.query(Company).filter_by(name=slug).first()
        if existing is None:
            session.add(Company(
                name=slug, ats_platform=h["ats"], ats_token=slug, display_name=display_name,
            ))
            inserted += 1
        elif existing.ats_platform in (None, "unknown"):
            existing.ats_platform = h["ats"]
            existing.ats_token = slug
            if display_name and not existing.display_name:
                existing.display_name = display_name
            updated += 1
    session.commit()
    return inserted, updated


def read_confirmations(path: str | None) -> set[str]:
    """One human-approved slug per line, '#' comments ok - same file
    shape as the Ashby/iCIMS/Gem onboarding scripts' --confirmations.
    Only ever consulted for candidate_kind == 'bare_first_word' hits
    (see gate() below); a slug here has no effect on any 'primary'-kind
    hit, which auto-stores exactly as it always has."""
    if not path:
        return set()
    approved: set[str] = set()
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            approved.add(line.split("\t")[0].strip().lower())
    return approved


def gate(hit: dict, confirmed_slugs: set[str]) -> str:
    """'auto' for every 'primary'-kind hit (unchanged from before this
    session - slug_candidates()'s own candidates were always auto-stored
    and stay that way). A 'bare_first_word' hit is 'auto' only if a human
    has explicitly approved its slug via --confirmations; otherwise
    'held' - mirrors the Ashby/SmartRecruiters project convention of
    never auto-storing a first-word-only-shaped match (see the module
    docstring)."""
    if hit["candidate_kind"] == "primary":
        return "auto"
    if hit["slug"] and hit["slug"].lower() in confirmed_slugs:
        return "auto"
    return "held"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-filings", type=int, default=DEFAULT_MIN_FILINGS)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument(
        "--confirmations",
        help="file of human-approved bare-first-word slugs to store despite the gate (never affects primary-kind hits)",
    )
    args = ap.parse_args()

    engine = create_engine(DATABASE_URL, echo=False)
    session = sessionmaker(bind=engine)()
    confirmed_slugs = read_confirmations(args.confirmations)

    sponsors = load_sponsors(session, args.min_filings, args.limit)
    logger.info(
        "Probing %d distinct sponsor employers (>= %d filings) against Greenhouse + Lever%s",
        len(sponsors), args.min_filings,
        f"; {len(confirmed_slugs)} human-approved bare-first-word slugs loaded" if confirmed_slugs else "",
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
    hits = gh + lv
    for h in hits:
        h["gate"] = gate(h, confirmed_slugs)

    primary = [r for r in hits if r["candidate_kind"] == "primary"]
    bare = [r for r in hits if r["candidate_kind"] == "bare_first_word"]
    bare_auto = [r for r in bare if r["gate"] == "auto"]
    bare_held = [r for r in bare if r["gate"] == "held"]
    to_store = [r for r in hits if r["gate"] == "auto"]

    report = {
        "min_filings": args.min_filings,
        "total_probed": len(results),
        "greenhouse": len(gh),
        "lever": len(lv),
        "neither": len(neither),
        "primary_candidate_hits": len(primary),
        "bare_first_word_hits": len(bare),
        "bare_first_word_auto": len(bare_auto),
        "bare_first_word_held": len(bare_held),
        "hits": sorted(
            [
                {k: r.get(k) for k in ("employer", "filings", "ats", "slug", "candidate_kind", "board_name", "gate")}
                for r in hits
            ],
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
    print(f"  of which via the primary candidates (unchanged)      : {len(primary)}")
    print(f"  of which via the new bare-first-word candidate       : {len(bare)}")
    print(f"    - auto-passed (human-confirmed via --confirmations): {len(bare_auto)}")
    print(f"    - held (needs a human glance)                      : {len(bare_held)}")
    if bare_held:
        for r in sorted(bare_held, key=lambda r: -r["filings"]):
            print(f"      HOLD: {r['employer']!r} -> slug={r['slug']!r} ats={r['ats']} "
                  f"board_name={r.get('board_name')!r} filings={r['filings']}")
    print(f"Report     : {REPORT_PATH}")

    if args.commit:
        inserted, updated = upsert_hits(session, to_store)
        print(f"\nCommitted: {inserted} new companies rows inserted, {updated} existing rows "
              f"updated (NULL/'unknown' -> detected platform) for scraping coverage. "
              f"({len(bare_held)} bare-first-word hits left held, not stored.)")
    else:
        print("\n(dry run - re-run with --commit to add these to the companies table)")

    session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()

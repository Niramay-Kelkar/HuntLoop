"""
Proof-of-mechanism (NOT the spider, NOT a batch onboarding runner): given
a company name, resolve its real, API-verified Gem job-board slug.

Same "prove it before building it" pattern as the SmartRecruiters /
Workday / Ashby / iCIMS discovery scripts.

**What renders a Gem board (confirmed live 2026-09-01 via browser network
inspection of jobs.gem.com/modular, /fetch, /felix):** a client-side
React SPA that calls ONE public GraphQL endpoint

    POST https://jobs.gem.com/api/public/graphql/batch      (JSON array body)

with the query `JobBoardList($boardId)` where **`boardId` IS the vanity
URL slug** (jobs.gem.com/{slug}). It returns:
  * oatsExternalJobPostings.jobPostings[]  - every posting in one call
    (NO pagination; verified fetch=70 / felix=114 exactly match the live
    rendered "Open positions (N)" count), each with id / extId / title /
    locations / department / employmentType but NO description
  * jobBoardExternal { id teamDisplayName pageTitle descriptionHtml }
    - the board's own org name, for the confidence cross-check
Per-job description needs a SECOND query, `ExternalJobPosting($boardId,
$extId)` -> `oatsExternalJobPosting { descriptionHtml firstPublishedTsSec
compensationHtml ... }` (also public, also unauthenticated).

Real response shapes:
  * unknown slug       -> HTTP 200, jobBoardExternal: null, jobPostings: []
  * real board, jobs   -> HTTP 200, jobBoardExternal non-null, jobPostings non-empty
  * real board, 0 jobs -> HTTP 200, jobBoardExternal non-null, jobPostings: []
So "resolved" = jobBoardExternal != null; "resolved + live" = also >= 1 job.

No robots.txt is published on jobs.gem.com (404). Honest identifying UA,
polite delays. This is a genuine public API (endpoint literally
`/api/public/graphql/`) - not HTML-scraping.

Run:
    PYTHONPATH=src python scripts/discover_gem_job_board.py "Modular"
    PYTHONPATH=src python scripts/discover_gem_job_board.py "The Boring Company" --slug the-boring-company
    PYTHONPATH=src python scripts/discover_gem_job_board.py --batch names.txt --json out.json

Discovery only - never touches the database.
"""
import argparse
import json
import os
import re
import sys
import time

import requests
from rapidfuzz.fuzz import token_set_ratio

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
try:
    from huntloop.matching.normalize import normalize_employer_name as _norm
except Exception:  # pragma: no cover
    def _norm(s: str) -> str:
        return re.sub(r"[^A-Z0-9 ]+", "", (s or "").upper()).strip()

_ENDPOINT = "https://jobs.gem.com/api/public/graphql/batch"
_UA = {"User-Agent": "HuntLoop/1.0 (sponsorship-aware job aggregator; sponsorship-matching research)"}
_TIMEOUT = 25

_LIST_QUERY = """
query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) {
    jobPostings { id extId title locations { name city isoCountry isRemote } job { department { name } locationType employmentType } }
  }
  jobBoardExternal(vanityUrlPath: $boardId) { id teamDisplayName pageTitle descriptionHtml }
}
""".strip()

_SUFFIX_NOISE = {
    "inc", "llc", "llp", "lp", "corp", "corporation", "co", "ltd", "limited",
    "plc", "pllc", "pc", "usa", "us", "na", "the", "gmbh", "ag", "bv",
    "technologies", "technology", "labs", "ai", "io", "hq",
}

# Common English words / far-too-generic single-word slugs — force the
# confidence down on a bare-first-word / acronym guess unless the board's
# own org name genuinely matches.
_GENERIC_SLUG = {
    "gem", "mission", "fetch", "inception", "nominal", "up", "arc", "flow",
    "pi", "data", "cloud", "app", "one", "now", "go", "get", "labs", "core",
    "base", "hub", "next", "flux", "atlas", "orbit", "prism", "vector",
}


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _slugify_hyphen(text: str) -> str:
    """Gem slugs are very frequently hyphenated: `the-boring-company`,
    `myriad-technology`, `black-ore`."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def candidate_slugs(name: str) -> list[tuple[str, str]]:
    """Ordered, de-duplicated (slug, kind), most likely first."""
    raw_words = [w for w in re.split(r"[\s/&,.\-]+", name.strip()) if w]
    meaningful = [w.lower() for w in raw_words if _slugify(w) and _slugify(w) not in _SUFFIX_NOISE]

    cands: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(c: str, kind: str) -> None:
        c = c.strip("-")
        if c and 2 <= len(c) <= 80 and c not in seen:
            seen.add(c)
            cands.append((c, kind))

    add(_slugify_hyphen(name), "hyphenated-full")           # the-boring-company
    add(_slugify_hyphen(" ".join(meaningful)), "hyphenated-core")
    add(_slugify(name), "full-slug")                        # theboringcompany
    add(_slugify("".join(meaningful)), "core-slug")         # boringcompany
    if len(meaningful) >= 2:
        add(_slugify(meaningful[0] + meaningful[1]), "core-slug")
        add(_slugify_hyphen(meaningful[0] + " " + meaningful[1]), "hyphenated-core")
    if meaningful:
        add(_slugify(meaningful[0]), "first-word-only")     # weak
    if len(meaningful) >= 2:
        add("".join(_slugify(w)[0] for w in meaningful if _slugify(w)), "acronym")  # weak
    return cands


def check_slug(slug: str, retries: int = 2) -> dict | None:
    """Live call. Returns a dict for any HTTP-200 response (real board or
    not); None only on transport failure / non-200."""
    payload = [{"operationName": "JobBoardList", "query": _LIST_QUERY, "variables": {"boardId": slug}}]
    for attempt in range(retries + 1):
        try:
            r = requests.post(_ENDPOINT, headers={**_UA, "Content-Type": "application/json"},
                              json=payload, timeout=_TIMEOUT)
        except requests.RequestException:
            time.sleep(1.5 + attempt)
            continue
        if r.status_code == 429:
            time.sleep(4 + attempt * 4)
            continue
        if r.status_code != 200:
            return None
        try:
            data = r.json()[0].get("data") or {}
        except (ValueError, IndexError, KeyError):
            return None
        board = data.get("jobBoardExternal")
        jobs = ((data.get("oatsExternalJobPostings") or {}).get("jobPostings")) or []
        org = None
        if board:
            org = (board.get("teamDisplayName")
                   or re.sub(r"\s+Careers\s*$", "", board.get("pageTitle") or "", flags=re.I).strip()
                   or None)
        return {
            "slug": slug,
            "is_board": board is not None,
            "board_id": (board or {}).get("id"),
            "org_name": org,
            "job_count": len(jobs),
            "sample_titles": [j.get("title", "").strip() for j in jobs[:5]],
        }
    return None


def _assess(name: str, hit: dict, kind: str) -> tuple[str, float]:
    if not hit["is_board"]:
        return "not-a-board (slug returned jobBoardExternal: null)", 0.0
    org = hit.get("org_name")
    sim = token_set_ratio(_norm(name), _norm(org)) if org else 0.0
    loose = kind in ("first-word-only", "acronym")
    generic = hit["slug"] in _GENERIC_SLUG or len(hit["slug"]) <= 3
    if hit["job_count"] == 0:
        return "found-unverifiable (board exists but lists 0 jobs)", round(sim, 1)
    if org is None:
        return "medium (board live but gave no org name to cross-check)", 0.0
    if sim < 55:
        return "low-suspect (board org name does not match the company)", round(sim, 1)
    if (loose or generic) and sim < 85:
        return "medium (generic/loose slug - confirm it's the right entity)", round(sim, 1)
    if sim >= 85:
        return "high", round(sim, 1)
    return "medium", round(sim, 1)


def _result(name, slug, kind, hit) -> dict:
    conf, sim = _assess(name, hit, kind)
    return {
        "name": name,
        "resolved_slug": slug if hit["is_board"] else None,
        "candidate_slug": slug,
        "is_board": hit["is_board"],
        "job_count": hit["job_count"],
        "org_name": hit.get("org_name"),
        "name_similarity": sim,
        "first_titles": hit.get("sample_titles", []),
        "winning_candidate_kind": kind,
        "method": "forced_slug" if kind == "forced" else "slug_guess",
        "confidence": conf,
    }


def _rank(r: dict) -> tuple:
    if r["is_board"] and r["job_count"] > 0:
        return (3, r["name_similarity"], r["job_count"])
    if r["is_board"]:
        return (2, r["name_similarity"], 0)
    return (1, 0, 0)


def discover(name: str, forced_slug: str | None = None) -> dict:
    tried: list[str] = []
    ordered = ([(_slugify_hyphen(forced_slug), "forced")] if forced_slug else []) + candidate_slugs(name)
    best: dict | None = None
    for slug, kind in ordered:
        if not slug or slug in tried:
            continue
        tried.append(slug)
        hit = check_slug(slug)
        time.sleep(0.6)
        if hit is None:
            continue
        cand = _result(name, slug, kind, hit)
        if best is None or _rank(cand) > _rank(best):
            best = cand
        if cand["is_board"] and cand["job_count"] > 0 and cand["name_similarity"] >= 85:
            break  # a live, name-matching board is a definitive answer
    if best is None:
        best = {"name": name, "resolved_slug": None, "candidate_slug": None, "is_board": False,
                "job_count": 0, "org_name": None, "name_similarity": 0.0, "first_titles": [],
                "winning_candidate_kind": None, "method": "unresolved", "confidence": "unresolved"}
    best["candidates_tried"] = tried
    return best


def _print(res: dict) -> None:
    if res["candidate_slug"]:
        print(f"  {res['name']!r} -> jobs.gem.com/{res['candidate_slug']}  ({res['method']})")
        print(f"     is_board={res['is_board']}  jobs={res['job_count']}  org={res['org_name']!r}  sim={res['name_similarity']}")
        print(f"     confidence: {res['confidence']}")
        if res["first_titles"]:
            print(f"     sample: {res['first_titles'][:3]}")
    else:
        print(f"  {res['name']!r} -> UNRESOLVED (tried {res['candidates_tried']})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?")
    ap.add_argument("--slug", dest="forced_slug", help="a jobs.gem.com/{slug} to verify first")
    ap.add_argument("--batch", help="file with one company name per line ('name\\tforced_slug' ok)")
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()

    jobs: list[tuple[str, str | None]] = []
    if args.batch:
        with open(args.batch) as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                nm, _, fid = line.partition("\t")
                jobs.append((nm.strip(), fid.strip() or None))
    elif args.name:
        jobs.append((args.name, args.forced_slug))
    else:
        ap.error("give a name or --batch")

    results = []
    for nm, fid in jobs:
        res = discover(nm, fid)
        results.append(res)
        _print(res)
        time.sleep(0.8)

    clean = [r for r in results if r["resolved_slug"] and r["job_count"] > 0 and r["name_similarity"] >= 55]
    empty = [r for r in results if r["is_board"] and r["job_count"] == 0]
    print(f"\n{len(clean)}/{len(results)} cleanly resolved (live board, name matches, >= 1 job).  "
          f"live-but-empty {len(empty)}, unresolved {len(results) - len([r for r in results if r['is_board']])}")
    for r in results:
        if r["is_board"] and r["confidence"] != "high":
            print(f"    check: {r['name']!r} -> {r['resolved_slug']!r} "
                  f"(org {r['org_name']!r}, {r['job_count']} jobs, {r['confidence']})")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"results": results}, fh, indent=2)
        print(f"report: {args.json_out}")


if __name__ == "__main__":
    sys.exit(main())

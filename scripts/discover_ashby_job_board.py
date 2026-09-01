"""
Proof-of-mechanism (NOT the spider, NOT a batch runner): given a company
name, try to resolve its real, API-verified Ashby jobBoardName.

Same "prove it before building it" pattern as the SmartRecruiters
companyId discovery and the Workday {tenant,dc,site} discovery. An Ashby
job board is reached at:

    GET https://api.ashbyhq.com/posting-api/job-board/{jobBoardName}

Real response shapes (confirmed live, 2026-09-01):
  * unknown jobBoardName            -> HTTP 404, plain body "Not Found"
  * real board, has listings        -> HTTP 200, {"jobs": [ {...}, ... ], "apiVersion": ...}
  * real board, nothing listed now  -> HTTP 200, {"jobs": [], "apiVersion": ...}

So a 200 alone is a weak signal (a real-but-empty board and, in
principle, a squatted/stale board both look the same); this script treats
"resolved" as a 200 with a NON-EMPTY jobs array, and separately reports
200-but-empty boards as "found, unverifiable".

The API response carries NO company-identifying field (no org name), only
the slug echoed inside each job's `jobUrl`. For the confidence signal we
therefore fetch the public board page https://jobs.ashbyhq.com/{slug} and
read its <title> / og:title ("<Org> Jobs"), then fuzzy-compare that org
name to the queried company name - the same spirit as the SmartRecruiters
board-name cross-check. A generic single-word slug that resolves to an
unrelated org is flagged low-confidence rather than trusted.

No pagination: the job board endpoint returns every listed job in one
response (verified against boards of 767 / 137 / 119 jobs - no cursor,
nextToken, offset or Link header anywhere).

Run:
    PYTHONPATH=src python scripts/discover_ashby_job_board.py "Linear"
    PYTHONPATH=src python scripts/discover_ashby_job_board.py "Anysphere" --slug cursor
    PYTHONPATH=src python scripts/discover_ashby_job_board.py --batch names.txt --json out.json

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
except Exception:  # normalize is optional here - fall back to a trivial one
    def _norm(s: str) -> str:  # type: ignore
        return re.sub(r"[^A-Z0-9 ]+", "", (s or "").upper()).strip()

_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
_BOARD_PAGE = "https://jobs.ashbyhq.com/{slug}"
_UA = {"User-Agent": "HuntLoop-ATS-research/1.0 (+sponsorship-matching)"}
_TIMEOUT = 20

# Legal-entity / geo suffix words worth stripping when building a slug -
# an Ashby jobBoardName is almost never the full legal name. Kept
# conservative for the same reason the SmartRecruiters list is: words
# like "labs"/"technologies"/"health" are frequently part of the real
# board slug. The un-stripped full slug is always emitted too.
_SUFFIX_NOISE = {
    "inc", "llc", "llp", "lp", "corp", "corporation", "co", "ltd", "limited",
    "plc", "pllc", "pc", "usa", "us", "na", "the", "gmbh", "ag", "bv",
}

# Slugs that are common English words / far too generic to trust on a
# bare-first-word or acronym guess without a strong name match. Not a
# hard block - just forces the confidence down unless the board page's
# org name genuinely matches.
_GENERIC_SLUG = {
    "up", "refer", "matrix", "plural", "agave", "inco", "bob", "eon",
    "linear", "notion", "ramp", "hex", "cursor", "arc", "flow", "pi",
    "labs", "data", "cloud", "app", "one", "now", "go", "get",
}


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _slugify_hyphen(text: str) -> str:
    """Like _slugify but keeps word boundaries as single hyphens - Ashby
    slugs are frequently hyphenated ("immunic-therapeutics")."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def candidate_slugs(name: str) -> list[tuple[str, str]]:
    """Ordered, de-duplicated list of (slug guess, kind) for `name`, most
    likely first. Each is verified live by the caller. Ashby slugs are
    lowercase (the board page redirects casing), so - unlike
    SmartRecruiters - we only emit lowercase forms."""
    raw_words = [w for w in re.split(r"[\s/&,.\-]+", name.strip()) if w]
    meaningful_raw = [w for w in raw_words if _slugify(w) and _slugify(w) not in _SUFFIX_NOISE]
    meaningful = [w.lower() for w in meaningful_raw]

    cands: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(c: str, kind: str) -> None:
        c = c.strip("-")
        if c and c not in seen:
            seen.add(c)
            cands.append((c, kind))

    nospace_all = _slugify(name)
    nospace_meaningful = _slugify("".join(meaningful))

    add(nospace_all, "full-slug")                       # "getyourguide"
    add(nospace_meaningful, "core-slug")                # drops Inc/Ltd/...
    add(_slugify_hyphen(name), "hyphenated")            # "immunic-therapeutics"
    add(_slugify_hyphen(" ".join(meaningful)), "hyphenated")
    if len(meaningful) >= 2:
        add(_slugify(meaningful[0] + meaningful[1]), "core-slug")
    if meaningful:
        add(_slugify(meaningful[0]), "first-word-only")            # weak, collision-prone
    if len(meaningful) >= 2:                                       # acronym, weak
        add("".join(_slugify(w)[0] for w in meaningful if _slugify(w)), "acronym")
    # suffix variants Ashby boards sometimes carry
    for base in (nospace_all, nospace_meaningful):
        for suf in ("hq", "careers", "jobs", "team", "inc", "global", "1", "2"):
            add(base + suf, "suffix-variant")
    return cands


def _board_org_name(slug: str) -> str | None:
    """Public board page -> the org's own display name ("<Org> Jobs")."""
    try:
        r = requests.get(_BOARD_PAGE.format(slug=slug), headers=_UA, timeout=_TIMEOUT)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    html = r.text
    m = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html, re.I)
    if not m:
        m = re.search(r"<title>([^<]+)</title>", html, re.I)
    if not m:
        return None
    org = m.group(1).strip()
    org = re.sub(r"\s+Jobs\s*$", "", org, flags=re.I).strip()
    return org or None


def check_slug(slug: str, retries: int = 2) -> dict | None:
    """Live call. Returns a dict for a real board (HTTP 200), including
    whether it currently lists any jobs; None for an unknown board (404)
    or a transport failure."""
    url = _API.format(slug=slug)
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=_UA, timeout=_TIMEOUT)
        except requests.RequestException:
            time.sleep(1.5)
            continue
        if r.status_code == 429:
            time.sleep(3 + attempt * 3)
            continue
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except ValueError:
            return None
        jobs = data.get("jobs") or []
        return {
            "slug": slug,
            "job_count": len(jobs),
            "sample_titles": [j.get("title", "").strip() for j in jobs[:5]],
            "sample_job_url": (jobs[0].get("jobUrl") if jobs else None),
            "has_description": bool(jobs and jobs[0].get("descriptionHtml")),
        }
    return None


def _assess(name: str, hit: dict, kind: str) -> dict:
    org = _board_org_name(hit["slug"])
    sim = token_set_ratio(_norm(name), _norm(org)) if org else 0.0
    loose = kind in ("first-word-only", "acronym")
    generic = hit["slug"] in _GENERIC_SLUG or len(hit["slug"]) <= 3
    empty = hit["job_count"] == 0

    if empty:
        conf = "found-unverifiable (board exists but lists 0 jobs)"
    elif org is None:
        conf = "medium (board live but its page gave no org name to cross-check)"
    elif sim < 55:
        conf = "low-suspect (board org name does not match the company)"
    elif (loose or generic) and sim < 85:
        conf = "medium (generic/loose slug - confirm it's the right entity)"
    elif sim >= 85:
        conf = "high"
    else:
        conf = "medium"
    return {
        "board_org_name": org,
        "name_similarity": round(sim, 1),
        "winning_candidate_kind": kind,
        "generic_slug": generic,
        "confidence": conf,
    }


def discover(name: str, forced_slug: str | None = None) -> dict:
    tried: list[str] = []
    ordered = ([(_slugify_hyphen(forced_slug), "forced")] if forced_slug else []) + candidate_slugs(name)
    for slug, kind in ordered:
        if not slug or slug in tried:
            continue
        tried.append(slug)
        hit = check_slug(slug)
        time.sleep(0.25)
        if hit is None:
            continue
        assessment = _assess(name, hit, kind)
        return {
            "name": name,
            "resolved_slug": slug,
            "job_count": hit["job_count"],
            "first_title": (hit["sample_titles"] or [None])[0],
            "sample_job_url": hit["sample_job_url"],
            "api_has_description": hit["has_description"],
            "method": "forced_slug" if kind == "forced" else "slug_guess",
            "candidates_tried": tried,
            **assessment,
        }
    return {"name": name, "resolved_slug": None, "job_count": 0, "first_title": None,
            "method": "unresolved", "candidates_tried": tried,
            "board_org_name": None, "name_similarity": 0.0, "confidence": "unresolved"}


def _print(res: dict) -> None:
    if res["resolved_slug"]:
        print(f"  {res['name']!r}")
        print(f"    -> jobBoardName={res['resolved_slug']!r}  jobs={res['job_count']}  "
              f"({res['method']})  confidence={res['confidence']}")
        print(f"    board org name: {res['board_org_name']!r}  "
              f"name_similarity={res['name_similarity']}  "
              f"winning_candidate={res.get('winning_candidate_kind')}")
        print(f"    first posting: {res['first_title']!r}")
        print(f"    sample jobUrl: {res['sample_job_url']}")
        print(f"    candidates tried: {res['candidates_tried']}")
    else:
        print(f"  {res['name']!r} -> UNRESOLVED  (tried: {res['candidates_tried']})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="company name to resolve")
    ap.add_argument("--slug", dest="forced_slug", help="a jobBoardName (e.g. from a web search) to verify first")
    ap.add_argument("--batch", help="file with one company name per line (optionally 'name\\tforced_slug')")
    ap.add_argument("--json", dest="json_out", help="write full results here")
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
        time.sleep(0.4)

    resolved = [r for r in results if r["resolved_slug"]]
    by_slug = [r for r in resolved if r["method"] == "slug_guess"]
    by_forced = [r for r in resolved if r["method"] == "forced_slug"]
    hi = [r for r in resolved if r["confidence"] == "high"]
    print(f"\n{len(resolved)}/{len(results)} resolved to a live Ashby board  "
          f"(slug-guess {len(by_slug)}, forced/web-search {len(by_forced)}, "
          f"unresolved {len(results) - len(resolved)})")
    print(f"  of those, {len(hi)} are high-confidence; "
          f"{len(resolved) - len(hi)} need a manual glance")
    for r in resolved:
        if r["confidence"] != "high":
            print(f"    check: {r['name']!r} -> {r['resolved_slug']!r} "
                  f"(org {r['board_org_name']!r}, {r['job_count']} jobs, {r['confidence']})")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"results": results}, fh, indent=2)
        print(f"report: {args.json_out}")


if __name__ == "__main__":
    sys.exit(main())

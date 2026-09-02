"""
Proof-of-mechanism (NOT the spider, NOT a batch onboarding runner): given
a company name, resolve its real, live iCIMS career-portal subdomain.

Same "prove it before trusting it" pattern as the Workday / SmartRecruiters
/ Ashby discovery scripts. An iCIMS tenant portal lives at:

    https://careers-{slug}.icims.com/jobs/search?ss=1&in_iframe=1   (server-rendered HTML)

Real response shapes (confirmed live 2026-09-01 - see SESSIONS.md
"iCIMS discovery pass"):
  * unknown subdomain            -> DNS/connection failure, or an iCIMS
                                    404 landing page
  * real portal, has openings    -> HTTP 200, page <title> "Job Listings
                                    at {Org}", one or more
                                    <a class="iCIMS_Anchor" ...> rows
  * real portal, 0 openings      -> HTTP 200, "Sorry, no jobs were found"
  * some tenants                  -> robots.txt "User-agent: * /
                                    Disallow: /" (must NOT be crawled)

"resolved" here = a live 200 portal that robots.txt PERMITS and that
currently lists >= 1 job. A robots-disallowed or 0-job portal is reported
separately (``robots_ok`` / ``job_count``) and never counts as a clean
resolution.

Confidence signal (like the Ashby / SmartRecruiters board-name
cross-check): the results page <title> carries the tenant's own display
name ("Job Listings at Herbalife"); we fuzzy-compare it to the queried
company name. A generic single-word slug resolving to an unrelated org is
flagged low-suspect rather than trusted.

Run:
    PYTHONPATH=src python scripts/discover_icims_job_board.py "Herbalife"
    PYTHONPATH=src python scripts/discover_icims_job_board.py "Insmed Incorporated" --slug insmed
    PYTHONPATH=src python scripts/discover_icims_job_board.py --batch names.txt --json out.json

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

from huntloop.icims_portal import (  # noqa: E402
    careers_base_url,
    listing_org_name,
    listing_has_no_results,
    parse_listing_anchors,
    robots_allows_listing,
)

try:
    from huntloop.matching.normalize import normalize_employer_name as _norm  # noqa: E402
except Exception:  # pragma: no cover
    def _norm(s: str) -> str:
        return re.sub(r"[^A-Z0-9 ]+", "", (s or "").upper()).strip()

_UA = {"User-Agent": "HuntLoop/1.0 (sponsorship-aware job aggregator; sponsorship-matching research)"}
_UA_STR = _UA["User-Agent"]
_TIMEOUT = 20

_SUFFIX_NOISE = {
    "inc", "llc", "llp", "lp", "corp", "corporation", "co", "ltd", "limited",
    "plc", "pllc", "pc", "usa", "us", "na", "the", "gmbh", "ag", "bv",
    "group", "holdings", "services", "solutions", "company", "companies",
    "incorporated", "international",
}

_GENERIC_SLUG = {
    "aurora", "horizon", "matrix", "apex", "summit", "pioneer", "vertex",
    "sierra", "atlas", "orion", "nova", "delta", "sterling", "premier",
}


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def candidate_slugs(name: str) -> list[tuple[str, str]]:
    """Ordered (slug, kind), most likely first. iCIMS careers subdomains
    are lowercase and almost always a compact form of the company name -
    ``careers-herbalife``, ``careers-persistentsystems``."""
    words = [w for w in re.split(r"[\s/&,.\-]+", name.strip()) if w]
    meaningful_raw = [w for w in words if _slugify(w) and _slugify(w) not in _SUFFIX_NOISE]
    meaningful = [w.lower() for w in meaningful_raw]

    cands: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(c: str, kind: str) -> None:
        c = c.strip("-")
        if c and 2 <= len(c) <= 63 and c not in seen:
            seen.add(c)
            cands.append((c, kind))

    add(_slugify("".join(meaningful)), "core-slug")        # "persistentsystems"
    add(_slugify(name), "full-slug")                        # includes Inc/Group/...
    if len(meaningful) >= 2:
        add(_slugify(meaningful[0] + meaningful[1]), "core-slug")
    if meaningful:
        add(_slugify(meaningful[0]), "first-word-only")     # weak, collision-prone
    if len(meaningful) >= 2:
        add("".join(_slugify(w)[0] for w in meaningful if _slugify(w)), "acronym")  # weak
    return cands


def _robots_ok(slug: str) -> bool | None:
    """True/False if robots.txt was fetched; None if it couldn't be
    reached (treated as 'subdomain probably absent')."""
    try:
        r = requests.get(f"{careers_base_url(slug)}/robots.txt", headers=_UA, timeout=_TIMEOUT)
    except requests.RequestException:
        return None
    if r.status_code >= 500:
        return None
    body = r.text if r.status_code == 200 else ""
    return robots_allows_listing(body, _UA_STR)


def check_slug(slug: str, retries: int = 1) -> dict | None:
    """Live check. Returns a dict for a reachable portal; None if the
    subdomain doesn't resolve / isn't an iCIMS portal at all."""
    robots_ok = _robots_ok(slug)
    if robots_ok is None:
        return None
    time.sleep(0.6)

    url = f"{careers_base_url(slug)}/jobs/search?ss=1&in_iframe=1"
    html = None
    status = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=_UA, timeout=_TIMEOUT, allow_redirects=True)
        except requests.RequestException:
            time.sleep(1.5)
            continue
        status = r.status_code
        html = r.text
        break
    if html is None:
        return None
    # An unknown careers-{slug} that still DNS-resolves (wildcard) serves
    # a tiny iCIMS 404 landing page - no portal markers.
    is_portal = ("iCIMS" in html) and ("/jobs/search" in html or "iCIMS_JobsTable" in html)
    if status != 200 or not is_portal:
        return {"slug": slug, "http_status": status, "robots_ok": robots_ok,
                "is_portal": False, "job_count": 0, "org_name": None, "sample_titles": []}

    anchors = parse_listing_anchors(html)
    return {
        "slug": slug,
        "http_status": status,
        "robots_ok": robots_ok,
        "is_portal": True,
        "no_results_banner": listing_has_no_results(html),
        "job_count": len(anchors),
        "org_name": listing_org_name(html),
        "sample_titles": [a["title_hint"] for a in anchors[:5] if a["title_hint"]],
    }


def _assess(name: str, hit: dict, kind: str) -> str:
    if not hit["robots_ok"]:
        return "blocked-by-robots (Disallow covers /jobs/search - do not crawl)"
    if not hit["is_portal"]:
        return "not-a-portal (subdomain resolves but no iCIMS board there)"
    org = hit.get("org_name")
    sim = token_set_ratio(_norm(name), _norm(org)) if org else 0.0
    generic = hit["slug"] in _GENERIC_SLUG or len(hit["slug"]) <= 3
    loose = kind in ("first-word-only", "acronym")
    if hit["job_count"] == 0:
        return "found-unverifiable (portal live + robots-permitted but 0 open jobs)"
    if org is None:
        return "medium (portal live but <title> gave no org name to cross-check)"
    if sim < 55:
        return "low-suspect (portal org name does not match the company)"
    if (loose or generic) and sim < 85:
        return "medium (generic/loose slug - confirm it's the right entity)"
    if sim >= 85:
        return "high"
    return "medium"


def _result(name, slug, kind, hit) -> dict:
    confidence = _assess(name, hit, kind)
    sim = 0.0
    if hit.get("org_name"):
        sim = round(token_set_ratio(_norm(name), _norm(hit["org_name"])), 1)
    return {
        "name": name,
        "resolved_slug": slug if hit["is_portal"] else None,
        "subdomain_slug": slug,
        "robots_ok": hit["robots_ok"],
        "is_portal": hit["is_portal"],
        "job_count": hit["job_count"],
        "org_name": hit.get("org_name"),
        "name_similarity": sim,
        "first_titles": hit.get("sample_titles", []),
        "winning_candidate_kind": kind,
        "method": "forced_slug" if kind == "forced" else "slug_guess",
        "confidence": confidence,
    }


# Rank so the best candidate across all guesses wins: a real portal with
# jobs > a real-but-empty portal > a robots-disallowed subdomain > a
# subdomain that isn't an iCIMS portal at all.
def _rank(r: dict) -> tuple:
    if r["is_portal"] and r["job_count"] > 0:
        return (4, r["job_count"])
    if r["is_portal"]:
        return (3, 0)
    if r["robots_ok"] is False:
        return (2, 0)
    return (1, 0)


def discover(name: str, forced_slug: str | None = None) -> dict:
    tried: list[str] = []
    ordered = ([(_slugify(forced_slug), "forced")] if forced_slug else []) + candidate_slugs(name)
    best: dict | None = None
    for slug, kind in ordered:
        if not slug or slug in tried:
            continue
        tried.append(slug)
        hit = check_slug(slug)
        time.sleep(0.5)
        if hit is None:
            continue
        cand = _result(name, slug, kind, hit)
        if best is None or _rank(cand) > _rank(best):
            best = cand
        if cand["is_portal"] and cand["job_count"] > 0:
            break  # a live board with openings is a definitive answer
    if best is None:
        best = {"name": name, "resolved_slug": None, "subdomain_slug": None, "robots_ok": None,
                "is_portal": False, "job_count": 0, "org_name": None, "name_similarity": 0.0,
                "first_titles": [], "winning_candidate_kind": None, "method": "unresolved",
                "confidence": "unresolved"}
    best["candidates_tried"] = tried
    return best


def _print(res: dict) -> None:
    if res["resolved_slug"] or res["subdomain_slug"]:
        print(f"  {res['name']!r} -> careers-{res['subdomain_slug']}.icims.com  ({res['method']})")
        print(f"     robots_ok={res['robots_ok']}  is_portal={res['is_portal']}  "
              f"jobs={res['job_count']}  org={res['org_name']!r}  sim={res['name_similarity']}")
        print(f"     confidence: {res['confidence']}")
        if res["first_titles"]:
            print(f"     sample: {res['first_titles'][:3]}")
    else:
        print(f"  {res['name']!r} -> UNRESOLVED (tried {res['candidates_tried']})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?")
    ap.add_argument("--slug", dest="forced_slug", help="a careers-{slug} to verify first")
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

    clean = [r for r in results if r["resolved_slug"] and r["robots_ok"] and r["job_count"] > 0]
    blocked = [r for r in results if r.get("robots_ok") is False]
    empty = [r for r in results if r["is_portal"] and r["job_count"] == 0 and r["robots_ok"]]
    print(f"\n{len(clean)}/{len(results)} cleanly resolved "
          f"(live portal, robots-permitted, >=1 job).  "
          f"robots-blocked {len(blocked)}, live-but-empty {len(empty)}, "
          f"unresolved {len(results) - len([r for r in results if r['is_portal']])}")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"results": results}, fh, indent=2)
        print(f"report: {args.json_out}")


if __name__ == "__main__":
    sys.exit(main())

"""
Proof-of-mechanism (NOT the spider, NOT a batch runner): given a company
name, try to resolve its real, API-verified SmartRecruiters companyId.

Why this needs its own discovery step (same "prove it before building it"
pattern as the Workday {tenant,dc,site} discovery): a SmartRecruiters
companyId is often close to the company name (lowercased, spaces removed)
but not reliably so - it can carry an appended digit ("Ubisoft" ->
"Ubisoft2"), be an abbreviation, or be a completely different internal
name. The public postings endpoint:

    GET https://api.smartrecruiters.com/v1/companies/{companyId}/postings

returns HTTP 200 with `totalFound: 0` for an unknown id (NOT a 404), so
the ONLY reliable "this id is real" signal is a live call returning
`totalFound > 0`. This script generates ordered name-derived candidates,
calls the endpoint for each, and returns the first candidate that comes
back with postings. A `--id` override lets you feed a candidate found by
a web search ("{name} site:smartrecruiters.com") through the exact same
live verification.

Run:
    PYTHONPATH=src python scripts/discover_smartrecruiters_id.py "Visa"
    PYTHONPATH=src python scripts/discover_smartrecruiters_id.py "Ubisoft" --id Ubisoft2
    PYTHONPATH=src python scripts/discover_smartrecruiters_id.py --batch names.txt --json out.json

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

_API = "https://api.smartrecruiters.com/v1/companies/{cid}/postings"
_UA = {"User-Agent": "HuntLoop-ATS-research/1.0 (+sponsorship-matching)"}
_TIMEOUT = 15

# Legal-entity / geo suffix words worth stripping when building a slug -
# a SmartRecruiters id is almost never the full legal name. Deliberately
# conservative: words like "solutions"/"consulting"/"technology" are NOT
# stripped because they're frequently part of the real SR board name
# ("Hitachi Solutions", "PA Consulting", "Jade Global") - over-stripping
# them was a real miss in an earlier pass of this script. We also always
# emit the un-stripped full slug as a candidate regardless.
_SUFFIX_NOISE = {
    "inc", "llc", "llp", "lp", "corp", "corporation", "co", "ltd", "limited",
    "plc", "pllc", "pc", "usa", "us", "na", "the",
}


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def candidate_ids(name: str) -> list[tuple[str, str]]:
    """Ordered, de-duplicated list of (companyId guess, kind) for `name`,
    most likely first. Each is verified live by the caller - order only
    affects which valid id is reported when several would work.

    SmartRecruiters companyIds ARE case-sensitive ("Visa", "BoschGroup",
    not "visa"/"boschgroup"), so every lexical guess is emitted in a few
    real-world casings: all-lower, Capitalised, and CamelCase-per-word."""
    raw_words = [w for w in re.split(r"[\s/&,]+", name.strip()) if w]
    meaningful_raw = [w for w in raw_words if _slugify(w) and _slugify(w) not in _SUFFIX_NOISE]
    meaningful = [w.lower() for w in meaningful_raw]

    cands: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(c: str, kind: str) -> None:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            cands.append((c, kind))

    def add_cased(base_lower: str, kind: str, camel: str | None = None) -> None:
        """Emit a lexical guess in the casings SmartRecruiters actually uses."""
        if not base_lower:
            return
        add(base_lower, kind)
        add(base_lower.capitalize(), kind)
        if camel and camel != base_lower.capitalize():
            add(camel, kind)

    nospace_all = _slugify(name)
    nospace_meaningful = _slugify("".join(meaningful))
    camel_meaningful = "".join(_slugify(w).capitalize() for w in meaningful) or None

    add_cased(nospace_all, "full-slug")                       # visa / Visa
    add_cased(nospace_meaningful, "core-slug", camel_meaningful)   # boschgroup / BoschGroup
    add("-".join(_slugify(w) for w in meaningful if _slugify(w)), "core-slug")
    add("-".join(_slugify(w).capitalize() for w in meaningful if _slugify(w)), "core-slug")
    if len(meaningful) >= 2:
        add_cased(_slugify(meaningful[0] + meaningful[1]), "core-slug")   # squareenix
    if meaningful:
        add_cased(_slugify(meaningful[0]), "first-word-only")            # weak, collision-prone
    if len(meaningful) >= 2:                                             # acronym, weak
        acr = "".join(_slugify(w)[0] for w in meaningful if _slugify(w))
        add(acr, "acronym")
        add(acr.upper(), "acronym")
    # disambiguating suffixes SmartRecruiters appends on id collision
    for base in (nospace_all, nospace_meaningful, camel_meaningful or ""):
        for suf in ("2", "1", "Inc", "US", "USA", "Careers", "Group", "Global"):
            add(base + suf, "suffix-variant")
    return cands


def check_id(company_id: str, retries: int = 2) -> dict | None:
    """Live call. Returns a dict (companyId, totalFound, the SR board's own
    display name, and a few sample posting titles/locations) if the id is
    real (totalFound > 0), None otherwise (unknown id, or non-200)."""
    url = _API.format(cid=company_id)
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=_UA, params={"limit": 5}, timeout=_TIMEOUT)
        except requests.RequestException:
            time.sleep(1.5)
            continue
        if r.status_code == 429:
            time.sleep(3 + attempt * 3)
            continue
        if r.status_code != 200:
            return None
        try:
            data = r.json()
        except ValueError:
            return None
        total = int(data.get("totalFound", 0) or 0)
        if total <= 0:
            return None
        content = data.get("content") or []
        titles = [p.get("name") for p in content]
        board_name = next((p.get("company", {}).get("name") for p in content
                           if p.get("company", {}).get("name")), None)
        return {
            "companyId": company_id,
            "totalFound": total,
            "board_name": board_name,
            "sample_titles": titles,
        }
    return None


_TEST_TITLE = re.compile(
    r"(\btest job\b|\bdemo job\b|\bdummy\b|\bdo not apply\b|\bplaceholder\b|xxxx)", re.I
)


def _assess(name: str, hit: dict, kind: str) -> dict:
    """Cross-check a live hit against the queried company name, in the
    same spirit as the Greenhouse/Lever board-name fuzzy check: how well
    does the SR board's own display name match, does the board look like a
    real hiring board vs. a sandbox tenant, and how loose was the guess
    that found it (a bare first word / acronym is collision-prone -
    "Sia" matched for "SIA Engineering" but is really Sia Partners)."""
    board = hit.get("board_name") or ""
    sim = token_set_ratio(_norm(name), _norm(board)) if board else 0.0
    looks_test = sum(bool(_TEST_TITLE.search(t or "")) for t in hit.get("sample_titles") or [])
    total = hit["totalFound"]
    loose = kind in ("first-word-only", "acronym")

    if looks_test:
        conf = "low-suspect (sandbox/test board)"
    elif sim < 55:
        conf = "low-suspect (board name doesn't match the company)"
    elif loose:
        conf = "medium (loose guess - confirm it's the right entity)"
    elif sim >= 85:
        conf = "high"
    else:
        conf = "medium"
    # informational only, not a confidence downgrade: a small board can be
    # a genuinely small firm OR a big company whose real hiring moved off
    # SmartRecruiters and left a vestigial board - a human should glance.
    small_board = total < 5
    return {"board_name": board, "name_similarity": round(sim, 1),
            "winning_candidate_kind": kind, "small_board": small_board,
            "test_titles_in_sample": looks_test, "confidence": conf}


def discover(name: str, forced_id: str | None = None) -> dict:
    """Resolve `name` -> verified companyId. If forced_id is given (e.g.
    from a web search) it is tried first, still via the same live check."""
    tried: list[str] = []
    ordered: list[tuple[str, str]] = (
        [(forced_id, "forced")] if forced_id else []) + candidate_ids(name)
    for cid, kind in ordered:
        if cid in tried:
            continue
        tried.append(cid)
        hit = check_id(cid)
        if hit:
            assessment = _assess(name, hit, kind)
            return {
                "name": name,
                "resolved_id": hit["companyId"],
                "total_found": hit["totalFound"],
                "first_title": (hit["sample_titles"] or [None])[0],
                "method": "forced_id" if kind == "forced" else "slug_guess",
                "candidates_tried": tried,
                **assessment,
            }
    return {"name": name, "resolved_id": None, "total_found": 0, "first_title": None,
            "method": "unresolved", "candidates_tried": tried,
            "board_name": None, "name_similarity": 0.0, "confidence": "unresolved"}


def _print(res: dict) -> None:
    if res["resolved_id"]:
        print(f"  {res['name']!r}")
        print(f"    -> companyId={res['resolved_id']!r}  totalFound={res['total_found']}"
              f"{'  [small board - glance]' if res.get('small_board') else ''}  "
              f"({res['method']})  confidence={res['confidence']}")
        print(f"    board name: {res['board_name']!r}  name_similarity={res['name_similarity']}"
              f"  winning_candidate={res.get('winning_candidate_kind')}")
        print(f"    first posting: {res['first_title']!r}")
        print(f"    candidates tried: {res['candidates_tried']}")
    else:
        print(f"  {res['name']!r} -> UNRESOLVED  (tried: {res['candidates_tried']})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="company name to resolve")
    ap.add_argument("--id", dest="forced_id", help="a companyId (e.g. from a web search) to verify first")
    ap.add_argument("--batch", help="file with one company name per line (optionally 'name\\tforced_id')")
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
        jobs.append((args.name, args.forced_id))
    else:
        ap.error("give a name or --batch")

    results = []
    for nm, fid in jobs:
        res = discover(nm, fid)
        results.append(res)
        _print(res)
        time.sleep(0.4)

    resolved = [r for r in results if r["resolved_id"]]
    by_slug = [r for r in resolved if r["method"] == "slug_guess"]
    by_forced = [r for r in resolved if r["method"] == "forced_id"]
    hi = [r for r in resolved if not r["confidence"].startswith("low")]
    print(f"\n{len(resolved)}/{len(results)} resolved to a live companyId  "
          f"(slug-guess {len(by_slug)}, forced/web-search {len(by_forced)}, "
          f"unresolved {len(results) - len(resolved)})")
    print(f"  of those, {len(hi)} pass the name-similarity / not-a-sandbox cross-check; "
          f"{len(resolved) - len(hi)} are low-confidence "
          f"(short-slug collision or near-empty/test board - need manual confirm)")
    for r in resolved:
        if r["confidence"].startswith("low"):
            print(f"    low-confidence: {r['name']!r} -> {r['resolved_id']!r} "
                  f"(board {r['board_name']!r}, {r['total_found']} postings, {r['confidence']})")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"results": results}, fh, indent=2)
        print(f"report: {args.json_out}")


if __name__ == "__main__":
    sys.exit(main())

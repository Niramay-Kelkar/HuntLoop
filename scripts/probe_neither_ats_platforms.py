"""
Feasibility measurement (NOT a spider build): of the sponsor employers
that did NOT resolve to Greenhouse or Lever in
scripts/detect_ats_for_sponsors.py, which OTHER ATS platform is most
common - Ashby, Workday, SmartRecruiters, or iCIMS?

Takes a random sample of the "neither" employers (employers with
>= --min-filings LCA filings that scripts/detect_ats_for_sponsors.py's
Greenhouse/Lever probe missed) and attempts name-derived slug detection
against each platform's public surface:

  Ashby           GET  https://api.ashbyhq.com/posting-api/job-board/{slug}
                       -> 200 == real job board  (clean, like GH/Lever)

  Workday         POST https://{tenant}.{dc}.myworkdayjobs.com
                         /wday/cxs/{tenant}/__nosuchsite__/jobs
                       -> 404 == tenant+dc exist, site name just wrong
                          422 == tenant/dc wrong
                       dc is brute-forced over the ~6 real data-centre
                       subdomains; the site name is NOT needed to confirm
                       existence (it IS needed to actually scrape - see
                       the feasibility notes in SESSIONS.md).

  SmartRecruiters GET  https://api.smartrecruiters.com/v1/companies/{id}
                         /postings?limit=1  -> totalFound > 0 == real
                       (the endpoint 200s with totalFound:0 for unknown
                       ids, so a positive count is the only real signal;
                       ids are often NOT the plain company name -
                       "Ubisoft" is "Ubisoft2" - so this is a weak lower
                       bound)

  iCIMS           GET  https://{careers-,}{slug}.icims.com/jobs/search
                       -> 200 + "icims" in body  (subdomains are largely
                       opaque; treat any hit rate here as a floor)

Run:  PYTHONPATH=src python scripts/probe_neither_ats_platforms.py \
          [--sample 400] [--min-filings 20] [--seed 12345]

Writes scratch_neither_ats_probe.json (gitignored) and prints the
breakdown. Never writes to the database - this is measurement only.
"""
import argparse
import json
import logging
import random
import sys
from concurrent.futures import ThreadPoolExecutor

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

load_dotenv()

from huntloop.settings import DATABASE_URL

# Reuse the exact same name -> candidate-slug derivation the proven
# Greenhouse/Lever coverage probe uses, so this is an apples-to-apples
# feasibility comparison (same slug guesses, different platforms).
sys.path.insert(0, "scripts")
from detect_ats_for_sponsors import _COMMON_WORDS, _TRAILING_NOISE, slug_candidates  # noqa: E402
import re as _re


def all_candidates(name):
    """slug_candidates() plus the LOOSER guesses it deliberately withholds
    to avoid Greenhouse/Lever false positives: the bare first word, and
    the first word joined with the next meaningful word. For a feasibility
    census we want to know the ceiling of what name-derived guessing can
    reach, then judge the false-positive risk per platform separately."""
    cands = list(slug_candidates(name))
    words = [w.lower() for w in _re.split(r"[\s/]+", name.upper()) if w and w != "&"]
    meaningful = [w for w in words if w.upper() not in _TRAILING_NOISE]
    extra = []
    if meaningful:
        w0 = meaningful[0]
        if len(w0) >= 3 and w0 not in _COMMON_WORDS:
            extra.append(w0)
        if len(meaningful) >= 2:
            extra.append(w0 + meaningful[1])
            extra.append(f"{w0}-{meaningful[1]}")
    for c in extra:
        if c and c not in cands:
            cands.append(c)
    return cands

logger = logging.getLogger(__name__)

REPORT_PATH = "scratch_neither_ats_probe.json"
_UA = "HuntLoop-ATS-feasibility/1.0 (+sponsorship-matching research)"
_TIMEOUT = 12
_MAX_WORKERS = 20

# Real Workday data-centre subdomains, most common first. A tenant lives
# on exactly one; we brute-force this short list.
_WORKDAY_DCS = ["wd1", "wd3", "wd5", "wd12", "wd103", "wd101", "wd2", "wd10"]
_WORKDAY_NOSITE = "huntloop__nosuchsite__probe"


def _get(url, **kw):
    try:
        return requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT, **kw)
    except Exception:
        return None


def _post(url, payload):
    try:
        return requests.post(
            url,
            headers={"User-Agent": _UA, "Content-Type": "application/json"},
            json=payload,
            timeout=_TIMEOUT,
        )
    except Exception:
        return None


def probe_ashby(slug):
    r = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if r is None or r.status_code != 200:
        return False
    try:
        data = r.json()
    except ValueError:
        return False
    return isinstance(data, dict) and isinstance(data.get("jobs"), list) and len(data["jobs"]) > 0


def probe_workday(slug):
    """Return the dc subdomain if {slug} is a live Workday tenant, else None."""
    for dc in _WORKDAY_DCS:
        r = _post(
            f"https://{slug}.{dc}.myworkdayjobs.com/wday/cxs/{slug}/{_WORKDAY_NOSITE}/jobs",
            {"limit": 1, "offset": 0},
        )
        if r is None:
            continue
        # 404 => host + tenant path resolved, only the site segment is
        # wrong. 422 => Workday rejected the tenant/dc outright.
        if r.status_code == 404:
            return dc
    return None


def probe_smartrecruiters(slug):
    for cand in {slug, slug.capitalize(), slug.upper()}:
        r = _get(f"https://api.smartrecruiters.com/v1/companies/{cand}/postings?limit=1")
        if r is None or r.status_code != 200:
            continue
        try:
            data = r.json()
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("totalFound", 0) > 0:
            return cand
    return None


def probe_icims(slug):
    for host in (f"careers-{slug}", slug):
        if len(host) > 63:  # DNS label limit - skip unbuildable hostnames
            continue
        r = _get(f"https://{host}.icims.com/jobs/search?ss=1", allow_redirects=True)
        if r is None or r.status_code != 200:
            continue
        if "icims" in r.text.lower() and ("job" in r.text.lower()):
            return host
    return None


def detect_one(row):
    try:
        return _detect_one(row)
    except Exception as exc:  # never let one bad row abort the whole census
        logger.warning("probe failed for %r: %s", row.get("employer"), exc)
        return {**row, "candidates": [], "ashby": None, "workday": None,
                "smartrecruiters": None, "icims": None, "platform": "error"}


def _detect_one(row):
    name = row["employer"]
    cands = all_candidates(name)
    out = {**row, "candidates": cands, "ashby": None, "workday": None,
           "smartrecruiters": None, "icims": None}
    for slug in cands:
        if out["ashby"] is None and probe_ashby(slug):
            out["ashby"] = slug
        if out["workday"] is None:
            dc = probe_workday(slug)
            if dc:
                out["workday"] = f"{slug}.{dc}"
        if out["smartrecruiters"] is None:
            sr = probe_smartrecruiters(slug)
            if sr:
                out["smartrecruiters"] = sr
        if out["icims"] is None and probe_icims(slug):
            out["icims"] = slug
    hits = [p for p in ("ashby", "workday", "smartrecruiters", "icims") if out[p]]
    out["platform"] = hits[0] if len(hits) == 1 else ("|".join(hits) if hits else "undetected")
    return out


def load_neither(session, min_filings, sample, seed):
    all_rows = session.execute(
        text(
            "SELECT employer_name_normalized AS employer, count(*) AS filings "
            "FROM lca_disclosures GROUP BY 1 HAVING count(*) >= :m ORDER BY filings DESC"
        ),
        {"m": min_filings},
    ).fetchall()

    # Reconstruct the "neither" set: everything the committed GH/Lever
    # probe already matched now lives in companies (name == discovered
    # slug). We can't map slug->employer perfectly, so instead exclude
    # employers whose slug candidates hit the prior report's employer list.
    with open("scratch_sponsor_ats_detection.json") as fh:
        prior = json.load(fh)
    resolved_employers = {h["employer"] for h in prior["hits"]}

    neither = [
        {"employer": r.employer, "filings": int(r.filings)}
        for r in all_rows
        if r.employer not in resolved_employers
    ]
    rng = random.Random(seed)
    rng.shuffle(neither)
    return neither[:sample], len(neither)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=400)
    ap.add_argument("--min-filings", type=int, default=20)
    ap.add_argument("--seed", type=int, default=12345)
    args = ap.parse_args()

    session = sessionmaker(bind=create_engine(DATABASE_URL))()
    sample, total_neither = load_neither(session, args.min_filings, args.sample, args.seed)
    session.close()
    logger.info("Neither-set size %d; probing random sample of %d (seed %d)",
                total_neither, len(sample), args.seed)

    results = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        for i, res in enumerate(pool.map(detect_one, sample), 1):
            results.append(res)
            if i % 50 == 0:
                logger.info("  ... %d/%d", i, len(sample))

    counts = {}
    for r in results:
        counts[r["platform"]] = counts.get(r["platform"], 0) + 1
    # per-platform any-hit tally (counts multi-hits under each platform)
    per_platform = {p: sum(1 for r in results if r[p]) for p in
                    ("ashby", "workday", "smartrecruiters", "icims")}

    report = {
        "min_filings": args.min_filings,
        "seed": args.seed,
        "neither_set_size": total_neither,
        "sample_size": len(sample),
        "platform_breakdown": counts,
        "per_platform_any_hit": per_platform,
        "results": results,
    }
    with open(REPORT_PATH, "w") as fh:
        json.dump(report, fh, indent=2)

    n = len(results)
    print(f"\n=== ATS platform of {n} random 'neither' sponsors (>= {args.min_filings} filings) ===")
    print(f"Neither-set size: {total_neither}   Sample: {n}   Seed: {args.seed}\n")
    for p in ("workday", "ashby", "smartrecruiters", "icims"):
        print(f"  {p:16s}: {per_platform[p]:4d}  ({per_platform[p]/n:5.1%})")
    undet = counts.get("undetected", 0)
    print(f"  {'undetected':16s}: {undet:4d}  ({undet/n:5.1%})")
    print(f"\n  full breakdown (incl. multi-hits): {counts}")
    print(f"  report: {REPORT_PATH}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()

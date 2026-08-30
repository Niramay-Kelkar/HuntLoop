"""
Proof-of-mechanism (NOT the spider, NOT a batch runner): given a single
Workday tenant slug, discover the full {tenant, dc, site} triple needed to
call the CXS jobs API, and print a sample page of real postings.

This exists to prove the discovery step described in SESSIONS.md
("Which ATS is most common among the 'neither' sponsors") actually works
end-to-end on real tenants before a Workday spider is built. It
deliberately handles ONE tenant per invocation.

Discovery, in order (each step fully automated):
  1. datacenter - POST .../wday/cxs/{tenant}/__nosuchsite__/jobs to each
     wd{N} subdomain; 404 == tenant lives here, 422 == not.
  2. site segment - POST .../wday/cxs/{tenant}/{site}/jobs for each name
     in _SITE_CANDIDATES; first 200 wins. Workday matches this segment
     case- and separator-insensitively for standard names, so a short
     list covers most tenants. Custom site names (e.g. Salesforce's
     "External_Career_Site") must be in the list explicitly or found via
     a "{company} site:myworkdayjobs.com" web search.

Run:  python scripts/discover_workday_triple.py <tenant-slug>
"""
import sys

import requests

_UA = {"User-Agent": "Mozilla/5.0 (HuntLoop ATS research)", "Content-Type": "application/json"}
_TIMEOUT = 15
_DCS = ["wd1", "wd3", "wd5", "wd12", "wd103", "wd101", "wd2", "wd10"]
_NOSITE = "huntloop__nosuchsite__probe"

# Curated from real tenants (see SESSIONS.md). Standard Workday site
# templates first, then observed customisations. Matched case/separator
# insensitively by Workday for the standard ones.
_SITE_CANDIDATES = [
    "careers", "jobs", "search", "searchjobs", "External_Career_Site",
    "CareerSite", "External", "External_Careers", "ExternalCareerSite",
    "Global_Careers", "external_experienced", "Search_Jobs", "job_search",
    "recruiting", "Professional_Careers", "Corporate_Careers",
    "External_Site", "Careers_External",
]


def _post(url, payload):
    try:
        return requests.post(url, json=payload, headers=_UA, timeout=_TIMEOUT)
    except requests.RequestException:
        return None


def find_dc(tenant):
    for dc in _DCS:
        r = _post(
            f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{_NOSITE}/jobs",
            {"limit": 1, "offset": 0, "appliedFacets": {}},
        )
        if r is not None and r.status_code == 404:
            return dc
    return None


def find_site(tenant, dc):
    for site in _SITE_CANDIDATES:
        r = _post(
            f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
            {"limit": 1, "offset": 0, "appliedFacets": {}},
        )
        if r is not None and r.status_code == 200:
            return site
    return None


def sample_jobs(tenant, dc, site, limit=5):
    base = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    p0 = _post(base, {"limit": limit, "offset": 0, "appliedFacets": {}}).json()
    p1 = _post(base, {"limit": limit, "offset": limit, "appliedFacets": {}}).json()
    return p0, p1


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: discover_workday_triple.py <tenant-slug>")
    tenant = sys.argv[1].strip().lower()

    dc = find_dc(tenant)
    if dc is None:
        sys.exit(f"no Workday tenant found for {tenant!r} on {_DCS}")
    print(f"tenant = {tenant}\ndc     = {dc}")

    site = find_site(tenant, dc)
    if site is None:
        sys.exit(f"tenant {tenant}.{dc} exists but no site name in the candidate "
                 f"list matched - run a '{tenant} site:myworkdayjobs.com' web search")
    print(f"site   = {site}")
    print(f"\nCXS jobs endpoint:\n  https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs")
    print(f"careers_url:\n  https://{tenant}.{dc}.myworkdayjobs.com/en-US/{site}")

    p0, p1 = sample_jobs(tenant, dc, site)
    print(f"\ntotal open postings: {p0.get('total')}")
    print("page 0 (offset 0):")
    for j in p0["jobPostings"]:
        print(f"  - {j['title']}  [{j.get('locationsText','?')}]  {j['externalPath']}")
    print("page 1 (offset 5)  -- must be different jobs:")
    for j in p1["jobPostings"]:
        print(f"  - {j['title']}  {j['externalPath']}")
    overlap = {j["externalPath"] for j in p0["jobPostings"]} & {j["externalPath"] for j in p1["jobPostings"]}
    print(f"\npage overlap: {len(overlap)} (0 == clean pagination)")


if __name__ == "__main__":
    main()

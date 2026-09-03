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
import re
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


# Site-name fragments that mark a NON-primary board (campus/intern/internal/
# alumni/private) - deprioritised when a tenant publishes several sites.
_SECONDARY_SITE_HINTS = (
    "campus", "intern", "student", "grad", "university", "private",
    "internal", "alumni", "returnship", "apprentic", "contingent",
    "temp", "referral", "military", "veteran",
)


def _site_total(tenant, dc, site):
    """Postings visible on this site, or None if the site path is invalid."""
    r = _post(
        f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs",
        {"limit": 1, "offset": 0, "appliedFacets": {}},
    )
    if r is None or r.status_code != 200:
        return None
    try:
        return int(r.json().get("total", 0))
    except (ValueError, AttributeError):
        return 0


def _verify_site(tenant, dc, site):
    return _site_total(tenant, dc, site) is not None


def site_from_robots(tenant, dc):
    """A Workday tenant's robots.txt lists every published site as
    ``Allow: /{Site}/`` and ``Sitemap: .../{Site}/siteMap.xml`` - the
    authoritative, guess-free way to get the site segment. Returns the
    best (primary) site name, verified against the CXS API, or None."""
    try:
        resp = requests.get(
            f"https://{tenant}.{dc}.myworkdayjobs.com/robots.txt", headers=_UA, timeout=_TIMEOUT
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None

    sites = []
    for line in resp.text.splitlines():
        line = line.strip()
        if line.lower().startswith("allow:"):
            seg = line.split(":", 1)[1].strip().strip("/")
            if seg and "/" not in seg:
                sites.append(seg)
        elif line.lower().startswith("sitemap:"):
            m = re.search(r"myworkdayjobs\.com/([A-Za-z0-9_-]+)/siteMap", line)
            if m:
                sites.append(m.group(1))

    seen = set()
    unique = [s for s in sites if not (s in seen or seen.add(s))]
    if not unique:
        return None

    # Score every valid site: primary sites (no campus/intern/private/...
    # fragment) beat secondary ones, then more postings wins - the main
    # careers board almost always carries the most open roles.
    scored = []
    for site in unique[:8]:  # cap probes for pathological multi-site tenants
        total = _site_total(tenant, dc, site)
        if total is None:
            continue
        secondary = any(h in site.lower() for h in _SECONDARY_SITE_HINTS)
        scored.append((not secondary, total, site))
    if not scored:
        return None
    scored.sort(reverse=True)
    return scored[0][2]


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
    # robots.txt is authoritative (lists every real site); fall back to
    # brute-forcing the common-name list for the rare tenant that blocks
    # or empties its robots.txt.
    site = site_from_robots(tenant, dc)
    if site:
        return site
    for site in _SITE_CANDIDATES:
        if _verify_site(tenant, dc, site):
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

"""
Pure parsing helpers behind the iCIMS spider - no network, no Scrapy, so
they can be unit-tested against captured HTML.

iCIMS has no usable public/third-party API (its documented Job Portal API
is HTTP-Basic-auth-gated - see SESSIONS.md "iCIMS discovery pass"). The
one legitimate surface is each tenant's OWN public career-portal HTML at

    https://careers-{slug}.icims.com/jobs/search?ss=1&in_iframe=1

which is server-rendered (no client-side JSON call). Confirmed live
2026-09-01 against Insmed / Herbalife / Persistent Systems / Judge /
Allegis / Clarkson:

  * Job rows are anchors:
      <a href=".../jobs/{id}/{title-slug}/job?in_iframe=1"
         class="iCIMS_Anchor" title="{id} - {Title}">
  * Pagination: ?pr={0-indexed page}&in_iframe=1, with
      <link rel="next" href=".../jobs/search?pr=1&in_iframe=1" />
    present while more pages exist; a "Page N of N" indicator marks the
    last page.
  * Each job detail page carries a schema.org JSON-LD ``JobPosting``
    block (``@type`` "JobPosting", possibly nested in an array / @graph)
    with title / description / datePosted / jobLocation / employmentType
    - the structured, template-stable source we parse first.
"""
from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

CAREERS_HOST_TEMPLATE = "careers-{slug}.icims.com"
LISTING_PATH = "/jobs/search"


def careers_base_url(slug: str) -> str:
    return f"https://{CAREERS_HOST_TEMPLATE.format(slug=slug)}"


def listing_url(slug: str, page: int = 0) -> str:
    """The server-rendered results page. ``page`` is the confirmed
    0-indexed ``pr`` param."""
    return f"{careers_base_url(slug)}{LISTING_PATH}?pr={page}&in_iframe=1"


def strip_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def with_in_iframe(url: str) -> str:
    """Force the bare iCIMS portal render rather than a tenant's branded
    wrapper page (some tenants redirect a plain hit to careers.<corp>.com
    marketing chrome; ``in_iframe=1`` returns the portal HTML directly)."""
    base = strip_query(url)
    return f"{base}?in_iframe=1"


# --- listing page -----------------------------------------------------

_JOB_ID_IN_HREF = re.compile(r"/jobs/(\d+)/[^/\"?]+/job", re.I)


def parse_listing_anchors(html: str) -> list[dict]:
    """Every distinct job on one results page, in document order.

    Returns dicts of ``{job_id, url, title_hint}`` where ``url`` is the
    query-stripped canonical detail URL and ``title_hint`` is the title
    text from the anchor's ``title="{id} - {Title}"`` attribute (a cheap,
    always-present fallback for the real title)."""
    out: list[dict] = []
    seen: set[str] = set()
    for tag in re.finditer(r"<a\b[^>]*>", html, re.I):
        raw = tag.group(0)
        if "iCIMS_Anchor" not in raw:
            continue
        href_m = re.search(r'href="([^"]+)"', raw, re.I)
        if not href_m:
            continue
        href = unescape(href_m.group(1))
        id_m = _JOB_ID_IN_HREF.search(href)
        if not id_m:
            continue
        job_id = id_m.group(1)
        if job_id in seen:
            continue
        seen.add(job_id)
        title_m = re.search(r'title="([^"]*)"', raw, re.I)
        title_hint = None
        if title_m:
            title_hint = unescape(title_m.group(1)).strip()
            # anchor titles are "20711 - Sr. Manager, HR Business Partner"
            m = re.match(r"^\d+\s*-\s*(.+)$", title_hint)
            if m:
                title_hint = m.group(1).strip()
        out.append({"job_id": job_id, "url": strip_query(href), "title_hint": title_hint or None})
    return out


def find_next_page_url(html: str, current_url: str) -> str | None:
    m = re.search(r'<link[^>]+rel="next"[^>]+href="([^"]+)"', html, re.I)
    if not m:
        m = re.search(r'<link[^>]+href="([^"]+)"[^>]+rel="next"', html, re.I)
    if not m:
        return None
    return urljoin(current_url, unescape(m.group(1)))


def parse_page_indicator(html: str) -> tuple[int, int] | None:
    """``(n, total)`` from a "Page N of M" indicator, else None."""
    m = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", html, re.I)
    return (int(m.group(1)), int(m.group(2))) if m else None


def listing_has_no_results(html: str) -> bool:
    return bool(re.search(r"no jobs were found", html, re.I))


# --- job detail: JSON-LD (primary) ----------------------------------

_LD_BLOCK = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)


def _walk(node):
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, dict):
        yield node
        if "@graph" in node:
            yield from _walk(node["@graph"])


def _is_jobposting(node: dict) -> bool:
    t = node.get("@type")
    return t == "JobPosting" or (isinstance(t, (list, tuple)) and "JobPosting" in t)


def extract_jobposting_jsonld(html: str) -> dict | None:
    """The first schema.org ``JobPosting`` node in any ld+json block, or
    None if there is none / all blocks are malformed."""
    for m in _LD_BLOCK.finditer(html):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for node in _walk(data):
            if isinstance(node, dict) and _is_jobposting(node):
                return node
    return None


def _clean(s):
    if not s:
        return None
    s = unescape(str(s)).strip()
    return s or None


def _employment_type(v):
    if isinstance(v, (list, tuple)):
        v = ", ".join(str(x) for x in v if x)
    return _clean(v)


def _org_name(v):
    if isinstance(v, dict):
        return _clean(v.get("name"))
    return _clean(v)


def _identifier(v):
    if isinstance(v, dict):
        return _clean(v.get("value") or v.get("name"))
    return _clean(v)


def _one_location(place: dict) -> str | None:
    addr = place.get("address")
    if isinstance(addr, list):
        addr = addr[0] if addr else None
    if not isinstance(addr, dict):
        return _clean(place.get("name"))
    parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
    parts = [p for p in parts if p and str(p).upper() != "UNAVAILABLE"]
    return ", ".join(str(p) for p in parts) or None


def jsonld_locations(node: dict) -> list[str]:
    jl = node.get("jobLocation")
    if not jl:
        return []
    if isinstance(jl, dict):
        jl = [jl]
    out: list[str] = []
    for place in jl:
        if not isinstance(place, dict):
            continue
        s = _one_location(place)
        if s and s not in out:
            out.append(s[:255])
    return out


def jobposting_fields(node: dict) -> dict:
    """Normalize a JSON-LD ``JobPosting`` node to the fields the spider
    needs. Missing keys come back as None / [] - the caller fills those
    from the HTML fallback and logs which ones."""
    return {
        "title": _clean(node.get("title")),
        "description": node.get("description") or None,
        "date_posted": _clean(node.get("datePosted")),
        "employment_type": _employment_type(node.get("employmentType")),
        "department": _clean(node.get("occupationalCategory")),
        "locations": jsonld_locations(node),
        "url": _clean(node.get("url")),
        "identifier": _identifier(node.get("identifier")),
        "hiring_organization": _org_name(node.get("hiringOrganization")),
        "valid_through": _clean(node.get("validThrough")),
    }


# --- job detail: visible HTML (per-field fallback only) -------------

def html_title(html: str) -> str | None:
    m = re.search(r'<h1[^>]*class="[^"]*iCIMS_Header[^"]*"[^>]*>(.*?)</h1>', html, re.I | re.S)
    if m:
        t = _clean(re.sub(r"<[^>]+>", " ", m.group(1)))
        if t:
            return re.sub(r"\s+", " ", t)
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html, re.I)
    if m:
        t = unescape(m.group(1))
        # "Title in City, State | Careers at ..."
        t = re.split(r"\s+in\s+.*?\|", t)[0]
        t = t.split("|")[0]
        return _clean(t)
    return None


def html_locations(html: str) -> list[str]:
    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html, re.I)
    if not m:
        return []
    t = unescape(m.group(1))
    loc = re.search(r"\bin\s+(.+?)\s*\|", t)
    if not loc:
        return []
    return [loc.group(1).strip()[:255]]


def html_description(html: str) -> str | None:
    chunks = re.findall(
        r'<div[^>]*class="[^"]*iCIMS_Expandable_Text[^"]*"[^>]*>(.*?)</div>\s*</div>',
        html,
        re.I | re.S,
    )
    if not chunks:
        chunks = re.findall(
            r'<div[^>]*class="[^"]*iCIMS_Expandable_Text[^"]*"[^>]*>(.*?)</div>', html, re.I | re.S
        )
    body = "\n".join(c.strip() for c in chunks if c.strip())
    if body:
        return body
    m = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', html, re.I)
    return _clean(m.group(1)) if m else None


# --- robots.txt -----------------------------------------------------

def robots_allows_listing(robots_txt: str, user_agent: str) -> bool:
    """True iff ``robots_txt`` permits ``user_agent`` to fetch
    ``/jobs/search``. An unparseable / empty robots body is treated as
    permissive (matches Scrapy + RFC behaviour). ``Disallow: /`` or any
    rule covering ``/jobs/search`` returns False."""
    rp = RobotFileParser()
    try:
        rp.parse((robots_txt or "").splitlines())
    except Exception:
        return True
    probe = f"https://{CAREERS_HOST_TEMPLATE.format(slug='x')}{LISTING_PATH}?ss=1&in_iframe=1"
    try:
        return rp.can_fetch(user_agent, probe)
    except Exception:
        return True


_ORG_FROM_LISTING_TITLE = re.compile(r"<title>\s*Job Listings? at\s+(.+?)\s*</title>", re.I)


def listing_org_name(html: str) -> str | None:
    """The tenant's own display name from the results page ``<title>``
    ("Job Listings at Herbalife" -> "Herbalife"). Used only as an
    onboarding cross-check signal, never by the spider."""
    m = _ORG_FROM_LISTING_TITLE.search(html or "")
    return _clean(m.group(1)) if m else None

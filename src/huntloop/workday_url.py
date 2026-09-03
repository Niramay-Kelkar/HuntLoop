"""
Pure helpers for the Workday spider - URL parsing and date normalization,
kept out of the spider module so they're unit-testable without Scrapy.

Storage decision (see SESSIONS.md 2026-08-30 "Build the Workday spider"):
Workday needs a 3-part identifier - tenant, data-centre subdomain, and
site segment - to hit the CXS jobs API
(``https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs``).
Rather than a new column or an encoded ``ats_token``, we store the full
careers URL in the existing ``companies.careers_url`` column - which
already holds exactly this for the one pre-existing Workday row
(``adobe`` -> ``https://adobe.wd5.myworkdayjobs.com/en-US/external_experienced``)
- and parse the triple back out here. ``ats_token`` stays the bare tenant
slug, consistent with every other platform's row.
"""
import re
from datetime import datetime, timedelta, timezone

# https://{tenant}.{dc}.myworkdayjobs.com[/{locale}]/{site}[/...]
# dc is the wd{N} data-centre subdomain (wd1, wd3, wd5, wd12, wd103, ...).
# An optional locale segment (en-US, en-GB, de-DE, ...) sits between host
# and site on the rendered URL but is NOT part of the CXS API path.
_CAREERS_URL_RE = re.compile(
    r"^https?://"
    r"(?P<tenant>[a-z0-9][a-z0-9-]*)"
    r"\.(?P<dc>wd\d+)"
    r"\.myworkdayjobs\.com"
    r"(?:/(?P<locale>[a-z]{2}-[A-Za-z]{2}))?"
    r"/(?P<site>[A-Za-z0-9_-]+)"
    r"(?:/.*)?$",
    re.IGNORECASE,
)


def parse_workday_careers_url(url: str) -> tuple[str, str, str]:
    """``https://nxp.wd3.myworkdayjobs.com/en-US/careers`` ->
    ``("nxp", "wd3", "careers")``. Raises ValueError on anything that
    isn't a recognisable Workday careers URL."""
    if not url:
        raise ValueError("empty careers URL")
    m = _CAREERS_URL_RE.match(url.strip())
    if not m:
        raise ValueError(f"not a Workday careers URL: {url!r}")
    return m.group("tenant").lower(), m.group("dc").lower(), m.group("site")


def build_cxs_base(tenant: str, dc: str, site: str) -> str:
    return f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"


_RELATIVE_DAYS_RE = re.compile(r"posted\s+(\d+)\+?\s+days?\s+ago", re.IGNORECASE)


def normalize_workday_date(
    start_date: str | None,
    posted_on: str | None,
    now: datetime | None = None,
) -> str | None:
    """Return an ISO-8601 UTC datetime string for a Workday posting, or
    None if neither field yields a date.

    ``jobPostingInfo.startDate`` (from the per-job detail endpoint) is an
    absolute ``YYYY-MM-DD`` and is the source of truth - use it whenever
    present.

    ``postedOn`` (present on both the list and detail responses) is
    RELATIVE TEXT - "Posted Today", "Posted Yesterday", "Posted N Days
    Ago", "Posted 30+ Days Ago" (verified against real API responses
    across 5 tenants / ~1,400 postings, 2026-08-30 - no "hours"/"weeks"/
    "months" variants seen). It's the fallback only. "30+ Days Ago"
    collapses every posting older than 30 days to exactly 30 days back -
    an acknowledged imprecision, but it only affects the fallback path
    (startDate covers ~100% of real rows) and only postings already
    outside the "new this week" window that daily-volume analysis cares
    about.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if start_date:
        try:
            d = datetime.strptime(start_date.strip(), "%Y-%m-%d")
            return d.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass

    if posted_on:
        text = posted_on.strip().lower()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if "today" in text:
            return midnight.isoformat()
        if "yesterday" in text:
            return (midnight - timedelta(days=1)).isoformat()
        m = _RELATIVE_DAYS_RE.search(text)
        if m:
            return (midnight - timedelta(days=int(m.group(1)))).isoformat()

    return None

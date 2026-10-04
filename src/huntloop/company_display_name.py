"""
Helpers for deriving `companies.display_name` - a human-readable label
to show instead of the lowercase ATS slug stored in `companies.name`.
`companies.name` is left completely untouched everywhere; this module
only produces a value for the separate, purely additive `display_name`
column.

Phase A covers exactly three platforms, each with a real, low-cost
proper-name source already confirmed live (see the investigation this
module's callers are built from):

  - Greenhouse: a separate board-metadata endpoint
    (`fetch_greenhouse_display_name`), not the `/jobs` endpoint the
    spider already calls for postings.
  - SmartRecruiters: already present on every posting the spider
    fetches (`pick_smartrecruiters_display_name`) - no extra call.
  - Gem: already fetched by the discovery script's confidence check
    (`teamDisplayName`) - handled at the call site, nothing here.

Lever, Ashby, iCIMS, and Workday are deliberately NOT covered here -
no reliable structured proper-name source was found for them without
new page-scraping, which is out of scope for this phase.
"""
import logging

import requests

logger = logging.getLogger(__name__)

_GREENHOUSE_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}"
_TIMEOUT = 10


def fetch_greenhouse_display_name(slug: str) -> str | None:
    """One GET to Greenhouse's board-metadata endpoint (not the /jobs
    endpoint the spider uses for postings). Returns the board's real
    `name` field, or None on a 404/malformed response/network error -
    never raises, so a single company's failure can't abort a batch
    backfill or onboarding run."""
    try:
        resp = requests.get(_GREENHOUSE_BOARD_URL.format(slug=slug), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        logger.warning(f"Greenhouse board-name fetch failed for {slug!r}: {exc}")
        return None
    if resp.status_code != 200:
        logger.info(f"Greenhouse board-name fetch for {slug!r} returned HTTP {resp.status_code}")
        return None
    try:
        data = resp.json()
    except ValueError:
        logger.warning(f"Greenhouse board-name fetch for {slug!r} returned non-JSON")
        return None
    name = data.get("name") if isinstance(data, dict) else None
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def pick_smartrecruiters_display_name(
    slug: str, identifier: str | None, name: str | None
) -> str | None:
    """SmartRecruiters' own posting responses carry two candidate fields
    - `company.identifier` and `company.name` - and real data shows
    either one can be the better-cased of the two for a given company
    (confirmed: `bytedance` has name still lowercase but identifier
    "Bytedance" correct; `citibankna` has the opposite, name "Citibank
    N.A" correct but identifier "CitibankNA" worse).

    Heuristic: prefer whichever field is NOT identical to the raw slug
    and NOT all-lowercase itself (i.e. it looks like it actually went
    through real casing); if both qualify, prefer `name` since it's
    SmartRecruiters' own designated display field. Returns None if
    neither qualifies."""

    def qualifies(value: str | None) -> bool:
        return bool(value) and value != slug and value.lower() != value

    if qualifies(name):
        return name
    if qualifies(identifier):
        return identifier
    return None

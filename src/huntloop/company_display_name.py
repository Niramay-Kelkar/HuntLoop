"""
Helpers for deriving `companies.display_name` - a human-readable label
to show instead of the lowercase ATS slug stored in `companies.name`.
`companies.name` is left completely untouched everywhere; this module
only produces a value for the separate, purely additive `display_name`
column.

Phase A covers three platforms, each with a real, low-cost proper-name
source already confirmed live:

  - Greenhouse: a separate board-metadata endpoint
    (`fetch_greenhouse_display_name`), not the `/jobs` endpoint the
    spider already calls for postings.
  - SmartRecruiters: already present on every posting the spider
    fetches (`pick_smartrecruiters_display_name`) - no extra call.
  - Gem: already fetched by the discovery script's confidence check
    (`teamDisplayName`) - handled at the call site, nothing here.

Phase B covers the remaining four platforms:

  - Lever: the public board page's bare `<title>` tag
    (`fetch_lever_display_name`).
  - Ashby: the public board page's `<title>` tag, minus a trailing
    " Jobs" suffix (`fetch_ashby_display_name`).
  - iCIMS: the portal page's `<title>` tag, minus a leading "Job
    Listings at " prefix (`fetch_icims_display_name`).
  - Workday: no ATS-side proper-name source exists at all (confirmed -
    no JSON field, empty rendered page title, name only buried in
    unstructured `og:description` prose). `casefold_legal_entity_name()`
    is a best-effort fallback over the existing
    `companies.matched_sponsor_employer_name` (DOL LCA fuzzy-match,
    ALL CAPS legal-entity name) - NOT a replacement for a real ATS
    source, and NOT guaranteed to be the brand name a human would
    recognize (e.g. "ACCORDION PARTNERS" for the brand "Accordion") -
    that's an accepted limitation of this fallback, not something this
    function tries to further correct.
"""
import logging
import re

import requests

logger = logging.getLogger(__name__)

_GREENHOUSE_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}"
_LEVER_BOARD_URL = "https://jobs.lever.co/{slug}"
_ASHBY_BOARD_URL = "https://jobs.ashbyhq.com/{slug}"
_ICIMS_PORTAL_URL = "https://careers-{slug}.icims.com/jobs/search?ss=1&in_iframe=1"
_TIMEOUT = 10
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_USER_AGENT = "HuntLoop/1.0 (sponsorship-aware job aggregator; display-name lookup)"


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


def _extract_title(html: str) -> str | None:
    """Pulls the content of the page's <title> tag, decoding the common
    HTML entities that can appear in a company name (e.g. "AT&amp;T").
    Returns None if there's no title tag or it's empty."""
    import html as html_module

    match = _TITLE_RE.search(html)
    if not match:
        return None
    title = html_module.unescape(match.group(1)).strip()
    return title or None


def fetch_lever_display_name(slug: str) -> str | None:
    """One GET to the public jobs.lever.co board page. The earlier
    investigation confirmed its <title> tag already holds the bare
    proper company name (e.g. "Palantir Technologies") - no prefix or
    suffix to strip. Returns None on a 404/empty board/network error/no
    title tag - never raises."""
    try:
        resp = requests.get(_LEVER_BOARD_URL.format(slug=slug), timeout=_TIMEOUT,
                             headers={"User-Agent": _USER_AGENT})
    except requests.RequestException as exc:
        logger.warning(f"Lever display-name fetch failed for {slug!r}: {exc}")
        return None
    if resp.status_code != 200:
        logger.info(f"Lever display-name fetch for {slug!r} returned HTTP {resp.status_code}")
        return None
    return _extract_title(resp.text)


_ASHBY_TITLE_SUFFIX_RE = re.compile(r"\s+Jobs\s*$", re.IGNORECASE)


def fetch_ashby_display_name(slug: str) -> str | None:
    """One GET to the public jobs.ashbyhq.com board page. Its <title>
    tag follows a confirmed "{Name} Jobs" pattern (e.g. "Ramp Jobs",
    "Notion Jobs", "Vanta Jobs") - strips that trailing " Jobs" suffix.
    Returns None on a 404/no title/a title that doesn't match the
    pattern - never raises, never guesses."""
    try:
        resp = requests.get(_ASHBY_BOARD_URL.format(slug=slug), timeout=_TIMEOUT,
                             headers={"User-Agent": _USER_AGENT})
    except requests.RequestException as exc:
        logger.warning(f"Ashby display-name fetch failed for {slug!r}: {exc}")
        return None
    if resp.status_code != 200:
        logger.info(f"Ashby display-name fetch for {slug!r} returned HTTP {resp.status_code}")
        return None
    title = _extract_title(resp.text)
    if title is None:
        return None
    stripped, count = _ASHBY_TITLE_SUFFIX_RE.subn("", title)
    if count == 0:
        logger.info(f"Ashby display-name fetch for {slug!r}: title {title!r} doesn't match the "
                     f"'{{Name}} Jobs' pattern, leaving display_name null")
        return None
    stripped = stripped.strip()
    return stripped or None


# Captures everything after the literal "Job Listings at " phrase through
# the end of the title, rather than requiring that phrase to be the very
# first characters (re.search, not anchored to ^). Real tenant titles
# vary beyond the original confirmed "Job Listings at {Name}" case - some
# prepend their own marketing copy ahead of it, e.g.
# "NYU Jobs - Careers - Job Listings at NYU" or
# "Working at HERE | Take your career further | Job Listings at HERE" -
# but the phrase, when present, still reliably precedes the real name at
# the end of the title. A title that never contains the phrase at all
# (a different template entirely, e.g. "Job Opportunities at Latham &
# Watkins LLP") still correctly doesn't match - this is a real pattern
# still, not a loosened guess.
_ICIMS_TITLE_PATTERN_RE = re.compile(r"Job Listings at\s+(.+)$", re.IGNORECASE)


def fetch_icims_display_name(slug: str) -> str | None:
    """One GET to the tenant's public iCIMS portal page (following
    redirects - one real tenant in the earlier investigation needed
    this). Its <title> tag reliably ends with "Job Listings at {Name}" -
    extracts whatever follows that phrase. Returns None on a 404/no
    title/a title that doesn't contain the phrase at all - never raises,
    never guesses."""
    try:
        resp = requests.get(_ICIMS_PORTAL_URL.format(slug=slug), timeout=_TIMEOUT,
                             headers={"User-Agent": _USER_AGENT}, allow_redirects=True)
    except requests.RequestException as exc:
        logger.warning(f"iCIMS display-name fetch failed for {slug!r}: {exc}")
        return None
    if resp.status_code != 200:
        logger.info(f"iCIMS display-name fetch for {slug!r} returned HTTP {resp.status_code}")
        return None
    title = _extract_title(resp.text)
    if title is None:
        return None
    match = _ICIMS_TITLE_PATTERN_RE.search(title)
    if match is None:
        logger.info(f"iCIMS display-name fetch for {slug!r}: title {title!r} doesn't contain the "
                     f"'Job Listings at {{Name}}' phrase, leaving display_name null")
        return None
    name = match.group(1).strip()
    return name or None


# Legal-entity / jurisdictional tokens to keep upper-cased rather than
# title-cased. Matched against a token with trailing periods/commas
# stripped (huntloop.matching.normalize.normalize_employer_name - the
# source of matched_sponsor_employer_name - already strips ALL periods/
# commas and drops a TRAILING suffix from this same list entirely, so in
# practice these mostly only fire for a suffix appearing mid-name; kept
# broader than that stripped set, and tolerant of punctuated input, in
# case this is ever reused on a less-normalized string). Explicitly NOT
# a brand-acronym dictionary (CDW, PWC, CIBC, UMB, NXP, BDO, RSM, ...) -
# those are real companies whose own name IS an acronym, which this
# function cannot distinguish from an ordinary word and will title-case
# like any other word; see this module's docstring and the caller's own
# report for named examples of this accepted limitation.
_LEGAL_ENTITY_TOKENS = {
    "LLC", "INC", "CORP", "CO", "LP", "LLP", "PLC", "LTD",
    "NV", "SA", "AG", "GMBH", "PC", "PLLC", "NA",
    "USA", "US", "UK",
}


def casefold_legal_entity_name(name: str) -> str:
    """Converts an ALL-CAPS legal-entity name (as stored in
    companies.matched_sponsor_employer_name) into a more readable form,
    WITHOUT naively title-casing every word - a legal-entity/
    jurisdictional token (LLC, INC, CORP, AG, GMBH, ...) is kept upper-
    cased instead of being mangled into "Llc"/"Ag"/"Gmbh". Every other
    word is title-cased via str.capitalize() (first letter upper, rest
    lower) - this does NOT special-case embedded punctuation within a
    word (e.g. "E&K" -> "E&k", not "E&K") or recognize a company whose
    entire brand name is itself an acronym (e.g. "CDW" -> "Cdw") - both
    are known, accepted gaps in this purely mechanical fallback, not
    bugs to chase further. Returns the input unchanged if it's empty."""
    if not name:
        return name
    words = []
    for token in name.split():
        bare = re.sub(r"[.,]", "", token).upper()
        if bare in _LEGAL_ENTITY_TOKENS:
            words.append(bare)
        else:
            words.append(token.capitalize())
    return " ".join(words)

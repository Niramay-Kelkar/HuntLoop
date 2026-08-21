"""
ATS (applicant tracking system) signature detection for a company's careers
page: static-HTML fetch first, a real browser render as a fallback.

Fetches the page once with a plain HTTP GET and pattern-matches known ATS
URL shapes against both the final response URL (after redirects) and the
raw HTML - some "careers" links redirect straight to the ATS-hosted board,
others just link to it or embed it in the page. That's fast and cheap, and
is enough for most pages.

Some career pages only expose their ATS link/embed after client-side
JavaScript runs (React/Next.js marketing sites that fetch job data at
runtime, rather than server-rendering a link to it) - the static fetch
correctly comes back "unknown" for these, not a wrong guess. For that case
only, detect_ats() retries with a headless-Chromium render via Playwright
(executes JS, waits for the page to settle) and re-runs the same
pattern-matching against the rendered HTML. This is deliberately
static-first, render-as-fallback: rendering a full browser is an order of
magnitude slower than an HTTP GET (see SESSIONS.md for measured numbers),
so it only runs on a static miss, never by default.
"""

import re

import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

_TIMEOUT_SECONDS = 10
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# How long to wait for the initial navigation, and how long to additionally
# wait for the page to go network-idle before giving up and reading
# whatever's in the DOM anyway - trackers/analytics beacons on real sites
# often keep a connection open indefinitely, so a strict "wait for idle or
# fail" would time out on pages that have, in practice, already finished
# rendering their content.
_RENDER_NAV_TIMEOUT_MS = 20_000
_RENDER_IDLE_TIMEOUT_MS = 5_000

# Checked in order; the first pattern to match either the final response
# URL or the page HTML wins. Each pattern's first capture group is the
# extracted token/company-slug.
_ATS_PATTERNS = [
    (
        "greenhouse",
        re.compile(
            r"(?:boards-api|job-boards|boards|api)\.greenhouse\.io/"
            r"(?:v1/boards/|embed/job_board\?for=)?([a-zA-Z0-9_-]+)",
            re.IGNORECASE,
        ),
    ),
    ("lever", re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]+)", re.IGNORECASE)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]+)", re.IGNORECASE)),
    ("workday", re.compile(r"https?://([a-zA-Z0-9-]+)\.(?:[a-zA-Z0-9-]+\.)?myworkdayjobs\.com", re.IGNORECASE)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers|www)\.smartrecruiters\.com/([a-zA-Z0-9_-]+)", re.IGNORECASE)),
]


def _match_ats(url: str, html: str):
    """Search `url` then `html` against every known ATS pattern. Returns
    (ats_name, identifier, matched_url) for the first match, or None."""
    for haystack in (url, html):
        for ats_name, pattern in _ATS_PATTERNS:
            match = pattern.search(haystack)
            if match:
                return ats_name, match.group(1), match.group(0)
    return None


def _fetch_rendered(url: str):
    """Render `url` in headless Chromium (executes JS, waits for the page
    to settle) and return (final_url, html, error). `error` is None on
    success; on any Playwright-specific failure (navigation timeout,
    browser launch failure, page crash) or other unexpected error, returns
    (None, None, error_message) instead of raising - rendering is a
    fallback path and must never take down detect_ats()."""
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=_USER_AGENT)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=_RENDER_NAV_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    # Navigation itself didn't settle in time - nothing
                    # rendered, this attempt is a genuine failure.
                    return None, None, "PlaywrightTimeoutError: navigation did not complete in time"
                try:
                    page.wait_for_load_state("networkidle", timeout=_RENDER_IDLE_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    # DOM already loaded above; a page that never goes
                    # fully idle (persistent analytics/tracking connections)
                    # doesn't mean nothing rendered - read the DOM as-is.
                    pass
                html = page.content()
                final_url = page.url
            finally:
                browser.close()
        return final_url, html, None
    except PlaywrightError as exc:
        return None, None, f"PlaywrightError: {exc}"
    except Exception as exc:  # pragma: no cover - defensive catch-all
        return None, None, f"{type(exc).__name__}: {exc}"


def detect_ats(company_careers_url: str) -> dict:
    """Fetch `company_careers_url` and detect which major ATS platform (if
    any) it points to or embeds. Static HTTP fetch first; if - and only if
    - that comes back with no known pattern matched, retries with a
    rendered (headless-browser) fetch before giving up.

    Always returns a dict - never raises. A fetch failure and a genuine
    "no known pattern matched" both come back as ats="unknown"; `error` is
    only set when a fetch (static or rendered) actually failed, so callers
    can tell "couldn't check" apart from "checked, nothing recognized".

    Returns a dict with keys:
        source_url: the URL passed in
        ats: one of "greenhouse", "lever", "ashby", "workday",
             "smartrecruiters", or "unknown"
        identifier: the extracted token/company-slug, or None
        matched_url: the specific substring that matched, or None
        http_status: the static fetch's response status code, or None if
             that request itself failed
        render_attempted: True if the Playwright rendering fallback was
             invoked (i.e. the static fetch succeeded but matched nothing)
        error: a short description of the fetch/render failure, or None
    """
    result = {
        "source_url": company_careers_url,
        "ats": "unknown",
        "identifier": None,
        "matched_url": None,
        "http_status": None,
        "render_attempted": False,
        "error": None,
    }

    try:
        response = requests.get(
            company_careers_url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    result["http_status"] = response.status_code

    match = _match_ats(response.url, response.text)
    if match:
        result["ats"], result["identifier"], result["matched_url"] = match
        return result

    # Static fetch succeeded but no known ATS signature was found anywhere
    # - retry with a rendered fetch before giving up, since some career
    # pages inject their ATS link/embed only after client-side JS runs
    # (see SESSIONS.md: checkr.com/company/careers).
    result["render_attempted"] = True
    rendered_url, rendered_html, render_error = _fetch_rendered(company_careers_url)
    if render_error is not None:
        result["error"] = render_error
        return result

    match = _match_ats(rendered_url, rendered_html)
    if match:
        result["ats"], result["identifier"], result["matched_url"] = match

    return result

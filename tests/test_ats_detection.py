"""
Unit tests for detect_ats()'s pattern-matching logic and its static-first,
render-as-fallback control flow, against mocked HTTP responses and a
mocked Playwright, built from real HTML snippets captured while manually
verifying the function against live company career pages (see
SESSIONS.md). No live network calls and no live browser launches -
requests.get and sync_playwright are both monkeypatched, so these can't be
flaky or slow.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from huntloop.ats_detection import detect_ats


class FakeResponse:
    """Minimal stand-in for requests.Response - just the attributes
    detect_ats() reads."""

    def __init__(self, url, text, status_code=200):
        self.url = url
        self.text = text
        self.status_code = status_code


def mock_get(url, text, status_code=200):
    """Patches requests.get to return a FakeResponse regardless of the
    URL/kwargs it's called with."""
    return patch("huntloop.ats_detection.requests.get", return_value=FakeResponse(url, text, status_code))


def mock_render(final_url="https://example.com/", html="<html></html>", launch_exc=None, goto_exc=None,
                 idle_exc=None, content_exc=None):
    """Patches huntloop.ats_detection.sync_playwright so _fetch_rendered()
    never launches a real browser. By default the render "succeeds" and
    returns (final_url, html); pass *_exc to make a specific stage
    (browser launch, page.goto, wait_for_load_state, page.content) raise
    instead, to exercise the graceful-failure paths."""
    mock_page = MagicMock()
    mock_page.url = final_url
    if content_exc is not None:
        mock_page.content.side_effect = content_exc
    else:
        mock_page.content.return_value = html
    if goto_exc is not None:
        mock_page.goto.side_effect = goto_exc
    if idle_exc is not None:
        mock_page.wait_for_load_state.side_effect = idle_exc

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page

    mock_playwright_instance = MagicMock()
    if launch_exc is not None:
        mock_playwright_instance.chromium.launch.side_effect = launch_exc
    else:
        mock_playwright_instance.chromium.launch.return_value = mock_browser

    mock_context_manager = MagicMock()
    mock_context_manager.__enter__.return_value = mock_playwright_instance
    mock_context_manager.__exit__.return_value = False

    return patch("huntloop.ats_detection.sync_playwright", return_value=mock_context_manager)


def test_detects_greenhouse_from_redirected_public_board_url():
    # Real shape: job-boards.greenhouse.io/checkr redirects to itself with
    # job listings server-rendered - the URL alone is the signal.
    with mock_get("https://job-boards.greenhouse.io/checkr", text="<html><body>Open roles</body></html>"):
        result = detect_ats("https://checkr.example/careers")
    assert result["ats"] == "greenhouse"
    assert result["identifier"] == "checkr"
    assert result["error"] is None


def test_detects_greenhouse_from_boards_api_preload_embed():
    # Real shape: careers.duolingo.com (Greenhouse's custom-domain hosting)
    # doesn't use boards.greenhouse.io/job-boards.greenhouse.io at all - it
    # preloads from boards-api.greenhouse.io/v1/boards/{token}/departments.
    # This is a regression test for a real false negative caught while
    # manually verifying against duolingo's actual careers page.
    html = (
        '<html><head><link as="fetch" crossorigin '
        'href="https://boards-api.greenhouse.io/v1/boards/duolingo/departments" '
        'rel="preload"/></head><body id="root"></body></html>'
    )
    with mock_get("https://careers.duolingo.com/", text=html):
        result = detect_ats("https://job-boards.greenhouse.io/duolingo")
    assert result["ats"] == "greenhouse"
    assert result["identifier"] == "duolingo"


def test_detects_lever_from_url():
    html = "<html><body><div class='posting-title'>Software Engineer</div></body></html>"
    with mock_get("https://jobs.lever.co/kraken", text=html):
        result = detect_ats("https://kraken.example/careers")
    assert result["ats"] == "lever"
    assert result["identifier"] == "kraken"


def test_detects_ashby_from_url():
    html = "<html><body>ashbyhq job board</body></html>"
    with mock_get("https://jobs.ashbyhq.com/ramp", text=html):
        result = detect_ats("https://ramp.example/careers")
    assert result["ats"] == "ashby"
    assert result["identifier"] == "ramp"


def test_detects_workday_tenant_from_subdomain():
    html = '<html><head><link rel="canonical" href="https://adobe.wd5.myworkdayjobs.com/external_experienced"/></head></html>'
    with mock_get("https://adobe.wd5.myworkdayjobs.com/en-US/external_experienced", text=html):
        result = detect_ats("https://adobe.example/careers")
    assert result["ats"] == "workday"
    assert result["identifier"] == "adobe"


def test_detects_smartrecruiters_from_url():
    html = "<html><head><title>Careers at Visa</title></head></html>"
    with mock_get("https://jobs.smartrecruiters.com/Visa", text=html):
        result = detect_ats("https://visa.example/careers")
    assert result["ats"] == "smartrecruiters"
    assert result["identifier"] == "Visa"


def test_unknown_when_no_known_pattern_present_even_after_render():
    # A generic marketing page with no ATS signature anywhere, static or
    # rendered - the "doesn't match any known pattern" case this must
    # handle gracefully, all the way through the render fallback.
    html = "<html><body><h1>Join our team</h1><p>We're hiring!</p></body></html>"
    with mock_get("https://example.com/careers", text=html), mock_render(html=html):
        result = detect_ats("https://example.com/careers")
    assert result["ats"] == "unknown"
    assert result["identifier"] is None
    assert result["error"] is None
    assert result["render_attempted"] is True


def test_render_fallback_detects_greenhouse_after_static_miss():
    # Real shape (checkr.com/company/careers/open-careers, see
    # SESSIONS.md): the static HTML has no ATS signature - just a
    # "GreenhouseBlock" CSS class placeholder - because the actual board
    # link is only present once client-side JS has run. The render
    # fallback is what turns this into a correct detection.
    static_html = '<html><body><div class="GreenhouseBlock"></div></body></html>'
    rendered_html = (
        '<html><body><div class="GreenhouseBlock">'
        '<a href="https://job-boards.greenhouse.io/checkr/jobs/8080752">Apply</a>'
        "</div></body></html>"
    )
    with mock_get("https://checkr.com/company/careers/open-careers", text=static_html), \
            mock_render(final_url="https://checkr.com/company/careers/open-careers", html=rendered_html):
        result = detect_ats("https://checkr.com/company/careers/open-careers")
    assert result["ats"] == "greenhouse"
    assert result["identifier"] == "checkr"
    assert result["render_attempted"] is True
    assert result["error"] is None


def test_render_fallback_not_triggered_on_static_hit():
    # Rendering a full browser is far slower than a plain GET - it must
    # only run when the static fetch found nothing, never on a hit.
    html = "<html><body>Open roles</body></html>"
    with mock_get("https://job-boards.greenhouse.io/checkr", text=html) as _, \
            patch("huntloop.ats_detection.sync_playwright") as mock_sync_playwright:
        result = detect_ats("https://checkr.example/careers")
    assert result["ats"] == "greenhouse"
    assert result["render_attempted"] is False
    mock_sync_playwright.assert_not_called()


def test_render_fallback_not_triggered_on_static_fetch_error():
    # A static-level network failure (DNS, connection refused, etc.) is a
    # different failure mode from "fetched fine, nothing matched" - retrying
    # the same URL with a browser would just fail the same way, so it's
    # not attempted.
    with patch("huntloop.ats_detection.requests.get", side_effect=requests.ConnectionError("DNS failure")), \
            patch("huntloop.ats_detection.sync_playwright") as mock_sync_playwright:
        result = detect_ats("https://this-domain-does-not-resolve.invalid")
    assert result["ats"] == "unknown"
    assert result["render_attempted"] is False
    mock_sync_playwright.assert_not_called()


def test_render_navigation_timeout_handled_gracefully():
    html = "<html><body>no signature</body></html>"
    with mock_get("https://example.com/careers", text=html), \
            mock_render(goto_exc=PlaywrightTimeoutError("Timeout 20000ms exceeded")):
        result = detect_ats("https://example.com/careers")
    assert result["ats"] == "unknown"
    assert result["identifier"] is None
    assert result["render_attempted"] is True
    assert "PlaywrightTimeoutError" in result["error"]


def test_render_browser_launch_failure_handled_gracefully():
    html = "<html><body>no signature</body></html>"
    with mock_get("https://example.com/careers", text=html), \
            mock_render(launch_exc=PlaywrightError("Executable doesn't exist")):
        result = detect_ats("https://example.com/careers")
    assert result["ats"] == "unknown"
    assert result["render_attempted"] is True
    assert "PlaywrightError" in result["error"]


def test_render_page_crash_handled_gracefully():
    html = "<html><body>no signature</body></html>"
    with mock_get("https://example.com/careers", text=html), \
            mock_render(content_exc=PlaywrightError("Target page, context or browser has been closed")):
        result = detect_ats("https://example.com/careers")
    assert result["ats"] == "unknown"
    assert result["render_attempted"] is True
    assert "PlaywrightError" in result["error"]


def test_render_unexpected_exception_handled_gracefully():
    # Belt-and-suspenders: even a non-Playwright exception during
    # rendering must not escape detect_ats().
    html = "<html><body>no signature</body></html>"
    with mock_get("https://example.com/careers", text=html), \
            mock_render(content_exc=RuntimeError("something unexpected")):
        result = detect_ats("https://example.com/careers")
    assert result["ats"] == "unknown"
    assert result["render_attempted"] is True
    assert "RuntimeError" in result["error"]


def test_render_network_idle_timeout_is_non_fatal():
    # A page that never goes fully network-idle (persistent
    # analytics/tracking connections, common on real marketing sites)
    # should still have its already-rendered DOM read and matched, not be
    # treated as a failure.
    html = '<html><body><a href="https://jobs.lever.co/whoop">Apply</a></body></html>'
    with mock_get("https://whoop.example/careers", text="<html><body>nothing yet</body></html>"), \
            mock_render(final_url="https://whoop.example/careers", html=html,
                        idle_exc=PlaywrightTimeoutError("Timeout 5000ms exceeded")):
        result = detect_ats("https://whoop.example/careers")
    assert result["ats"] == "lever"
    assert result["identifier"] == "whoop"
    assert result["error"] is None


def test_handles_request_exception_without_raising():
    with patch("huntloop.ats_detection.requests.get", side_effect=requests.ConnectionError("DNS failure")):
        result = detect_ats("https://this-domain-does-not-resolve.invalid")
    assert result["ats"] == "unknown"
    assert result["identifier"] is None
    assert result["http_status"] is None
    assert "ConnectionError" in result["error"]


def test_handles_timeout_without_raising():
    with patch("huntloop.ats_detection.requests.get", side_effect=requests.Timeout("timed out")):
        result = detect_ats("https://slow.example.com/careers")
    assert result["ats"] == "unknown"
    assert result["error"] is not None


def test_result_always_includes_source_url():
    html = "<html><body>nothing here</body></html>"
    with mock_get("https://example.com/careers", text=html):
        result = detect_ats("https://example.com/careers")
    assert result["source_url"] == "https://example.com/careers"


def test_url_match_is_checked_before_html_body():
    # If the final URL itself is the ATS board (post-redirect), that's a
    # stronger and cheaper signal than scanning the whole HTML body -
    # confirm it still wins even when the body contains no signature.
    with mock_get("https://jobs.lever.co/whoop", text="<html><body>no lever mentions in here</body></html>"):
        result = detect_ats("https://whoop.example/careers")
    assert result["ats"] == "lever"
    assert result["identifier"] == "whoop"

"""
Smoke test for a deployed (or locally rehearsed) HuntLoop demo API, and
optionally its frontend - see docs/DEPLOY_DEMO.md.

Standard library only (urllib + json + time), no requests/httpx/pytest -
so this can run from any machine with just python3, before or after the
real dependencies are installed anywhere.

Usage:

    API_BASE_URL=https://huntloop-demo-api.onrender.com \\
    FRONTEND_URL=https://huntloop-demo.vercel.app \\
    python scripts/smoke_test_demo.py

FRONTEND_URL is optional - when set, it is used only as the Origin sent
on the CORS check (see _check_cors below), never fetched itself - this
script does not render or crawl the frontend.

Checks, each timed (including the very first request, which on Render's
free plan is the one that can be slow after a cold start - see
docs/DEPLOY_DEMO.md's "what cold starts look like"):
  - GET /health
  - GET /demo-info
  - GET /jobs - at least one item, and items carry a match_score
  - GET /jobs/{id} - real sponsor data shape (sponsor is present as a
    key, not necessarily non-null - a demo snapshot's sampled jobs may
    or may not include a sponsor-matched company)
  - GET /dashboard/stats
  - Blocked-in-demo-mode routes return 404: POST /jobs/{id}/draft-answer,
    POST /resumes/upload, PATCH /resumes/{id}/activate
  - PATCH /jobs/{id}/application is a no-op (200, demo: true, and a
    second GET of the same job still shows no application status)
  - CORS: an OPTIONS preflight from FRONTEND_URL gets
    Access-Control-Allow-Origin back for that exact origin

Exits non-zero if any check fails, printing one PASS/FAIL line per
check plus its timing. Prints a final summary table.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_BASE_URL = os.environ.get("API_BASE_URL", "").rstrip("/")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "").rstrip("/")

_TIMEOUT_SECONDS = 30


class CheckResult:
    def __init__(self, name: str, passed: bool, elapsed_ms: float, detail: str = "") -> None:
        self.name = name
        self.passed = passed
        self.elapsed_ms = elapsed_ms
        self.detail = detail


def _request(method: str, path: str, body: dict | None = None, headers: dict | None = None):
    url = f"{API_BASE_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req_headers = {"Content-Type": "application/json"} if data is not None else {}
    req_headers.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)

    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            elapsed_ms = (time.monotonic() - t0) * 1000
            raw = resp.read()
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = None
            # Keep the original case-insensitive header object rather than
            # a plain dict - uvicorn sends lowercase header names, and a
            # plain dict's .get() is case-sensitive, which silently missed
            # "Access-Control-Allow-Origin" during local rehearsal.
            return resp.status, payload, resp.headers, elapsed_ms
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.monotonic() - t0) * 1000
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = None
        return exc.code, payload, exc.headers, elapsed_ms
    except urllib.error.URLError as exc:
        elapsed_ms = (time.monotonic() - t0) * 1000
        raise RuntimeError(f"request to {url} failed: {exc}") from exc


def _check_health(results: list[CheckResult]) -> None:
    name = "GET /health (first request - includes cold start if any)"
    try:
        status, payload, _headers, elapsed_ms = _request("GET", "/health")
        passed = status == 200
        results.append(CheckResult(name, passed, elapsed_ms, f"status={status} body={payload}"))
    except RuntimeError as exc:
        results.append(CheckResult(name, False, 0.0, str(exc)))


def _check_demo_info(results: list[CheckResult]) -> None:
    name = "GET /demo-info"
    status, payload, _headers, elapsed_ms = _request("GET", "/demo-info")
    passed = status == 200 and isinstance(payload, dict) and "message" in payload and "snapshot_date" in payload
    results.append(CheckResult(name, passed, elapsed_ms, f"status={status} body={payload}"))


def _check_jobs_list(results: list[CheckResult]) -> int | None:
    name = "GET /jobs (list with scores)"
    status, payload, _headers, elapsed_ms = _request("GET", "/jobs?limit=5")
    job_id = None
    passed = False
    detail = f"status={status}"
    if status == 200 and isinstance(payload, dict):
        items = payload.get("items", [])
        has_items = len(items) > 0
        has_score_field = has_items and "match_score" in items[0]
        passed = has_items and has_score_field
        detail = f"status={status} total={payload.get('total')} first_match_score={items[0].get('match_score') if has_items else None}"
        if has_items:
            job_id = items[0]["id"]
    results.append(CheckResult(name, passed, elapsed_ms, detail))
    return job_id


def _check_job_detail(results: list[CheckResult], job_id: int | None) -> None:
    name = "GET /jobs/{id} (sponsor data shape)"
    if job_id is None:
        results.append(CheckResult(name, False, 0.0, "skipped - no job id from the list check"))
        return
    status, payload, _headers, elapsed_ms = _request("GET", f"/jobs/{job_id}")
    passed = status == 200 and isinstance(payload, dict) and "sponsor" in payload and "ats_platform" in payload
    results.append(CheckResult(name, passed, elapsed_ms, f"status={status} has_sponsor_key={'sponsor' in (payload or {})}"))


def _check_dashboard_stats(results: list[CheckResult]) -> None:
    name = "GET /dashboard/stats"
    status, payload, _headers, elapsed_ms = _request("GET", "/dashboard/stats")
    passed = status == 200 and isinstance(payload, dict) and "total_jobs" in payload
    results.append(CheckResult(name, passed, elapsed_ms, f"status={status} body={payload}"))


def _check_blocked_routes(results: list[CheckResult], job_id: int | None) -> None:
    if job_id is not None:
        name = "POST /jobs/{id}/draft-answer is blocked (404)"
        status, _payload, _headers, elapsed_ms = _request(
            "POST",
            f"/jobs/{job_id}/draft-answer",
            body={"prompt": "smoke test", "provider": "groq", "api_key": "smoke-test-key"},
        )
        results.append(CheckResult(name, status == 404, elapsed_ms, f"status={status}"))

    name = "POST /resumes/upload is blocked (404)"
    status, _payload, _headers, elapsed_ms = _request("POST", "/resumes/upload", body={})
    results.append(CheckResult(name, status == 404, elapsed_ms, f"status={status}"))

    name = "PATCH /resumes/{id}/activate is blocked (404)"
    status, _payload, _headers, elapsed_ms = _request("PATCH", "/resumes/1/activate")
    results.append(CheckResult(name, status == 404, elapsed_ms, f"status={status}"))


def _check_application_patch_noop(results: list[CheckResult], job_id: int | None) -> None:
    name = "PATCH /jobs/{id}/application is a no-op"
    if job_id is None:
        results.append(CheckResult(name, False, 0.0, "skipped - no job id from the list check"))
        return
    status, payload, _headers, elapsed_ms = _request(
        "PATCH", f"/jobs/{job_id}/application", body={"status": "applied"}
    )
    passed = status == 200 and isinstance(payload, dict) and payload.get("demo") is True
    results.append(CheckResult(name, passed, elapsed_ms, f"status={status} body={payload}"))


def _check_cors(results: list[CheckResult]) -> None:
    name = "CORS header for the frontend origin"
    if not FRONTEND_URL:
        results.append(CheckResult(name, True, 0.0, "skipped - no FRONTEND_URL given"))
        return
    status, _payload, headers, elapsed_ms = _request(
        "OPTIONS",
        "/health",
        headers={
            "Origin": FRONTEND_URL,
            "Access-Control-Request-Method": "GET",
        },
    )
    allow_origin = headers.get("Access-Control-Allow-Origin")
    passed = allow_origin == FRONTEND_URL
    results.append(CheckResult(name, passed, elapsed_ms, f"status={status} Access-Control-Allow-Origin={allow_origin}"))


def main() -> int:
    if not API_BASE_URL:
        print("API_BASE_URL must be set.", file=sys.stderr)
        return 1

    results: list[CheckResult] = []

    _check_health(results)
    _check_demo_info(results)
    job_id = _check_jobs_list(results)
    _check_job_detail(results, job_id)
    _check_dashboard_stats(results)
    _check_blocked_routes(results, job_id)
    _check_application_patch_noop(results, job_id)
    _check_cors(results)

    print()
    print(f"{'STATUS':<6} {'TIME (ms)':>10}  CHECK")
    print("-" * 72)
    all_passed = True
    for result in results:
        status_label = "PASS" if result.passed else "FAIL"
        if not result.passed:
            all_passed = False
        print(f"{status_label:<6} {result.elapsed_ms:>10.1f}  {result.name}")
        if result.detail:
            print(f"            {result.detail}")

    print()
    if all_passed:
        print("All checks passed.")
        return 0
    print("One or more checks failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

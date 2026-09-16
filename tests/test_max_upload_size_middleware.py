"""
Unit tests for huntloop.api.routers.resumes.MaxUploadSizeMiddleware -
exercised directly at the ASGI layer (not through TestClient/httpx, whose
exact body-chunking behavior we don't want this test to depend on) so we
can deterministically prove the real claim: the middleware rejects with
a 413 as soon as cumulative streamed bytes cross the cap, WITHOUT ever
letting the downstream app (here, a fake one that just counts what it
was handed) see the rest of the body. A fake `receive()` that yields one
chunk per call is a faithful stand-in for how a real ASGI server
delivers a streaming request body incrementally as it arrives on the
wire - this is the mechanism (not Content-Length) the middleware relies
on, and this test proves it operates on it correctly.

No async test-runner plugin (pytest-asyncio/anyio) is installed in this
project (checked directly, not assumed) - these tests use plain
asyncio.run() from ordinary sync test functions instead of needing one.
"""
import asyncio

from huntloop.api.routers.resumes import MaxUploadSizeMiddleware


def _make_receive(chunks: list[bytes]):
    """An ASGI receive() callable that yields one http.request message
    per chunk in `chunks` (more_body=True on all but the last), matching
    how a real server streams a request body in as it arrives."""
    remaining = list(chunks)

    async def receive():
        chunk = remaining.pop(0) if remaining else b""
        return {"type": "http.request", "body": chunk, "more_body": bool(remaining)}

    return receive


class _RecordingApp:
    """Fake downstream ASGI app - just drains whatever the middleware
    hands it via receive(), recording every chunk actually seen, so a
    test can assert how much of the body reached it before the
    middleware cut things off."""

    def __init__(self):
        self.received_chunks: list[bytes] = []

    async def __call__(self, scope, receive, send):
        while True:
            message = await receive()
            self.received_chunks.append(message["body"])
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _http_scope(path: str) -> dict:
    return {"type": "http", "path": path, "headers": []}


def _run(app, scope, receive):
    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def test_allows_a_request_under_the_cap_through_unchanged():
    inner = _RecordingApp()
    middleware = MaxUploadSizeMiddleware(inner, path="/resumes/upload", max_bytes=1000)
    chunks = [b"a" * 300, b"b" * 300, b"c" * 300]  # 900 total, under the 1000 cap
    sent = _run(middleware, _http_scope("/resumes/upload"), _make_receive(chunks))

    assert inner.received_chunks == chunks
    assert sent[0]["status"] == 200


def test_rejects_with_413_once_cumulative_bytes_exceed_the_cap():
    inner = _RecordingApp()
    middleware = MaxUploadSizeMiddleware(inner, path="/resumes/upload", max_bytes=1000)
    # 5 chunks of 300 bytes = 1500 total, well past the 1000 cap - the
    # 4th chunk (1200 cumulative) is where this should trip.
    chunks = [b"a" * 300 for _ in range(5)]
    sent = _run(middleware, _http_scope("/resumes/upload"), _make_receive(chunks))

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 413

    # The real point of this test: the downstream app must NOT have been
    # allowed to drain the entire oversized body before the rejection -
    # it should have seen strictly fewer bytes than were "sent", proving
    # this is enforced mid-stream, not after the fact.
    total_seen_by_app = sum(len(c) for c in inner.received_chunks)
    total_sent = sum(len(c) for c in chunks)
    assert total_seen_by_app < total_sent
    assert total_seen_by_app <= 1000 + 300  # cut off at (or just past) the cap, not the full 1500


def test_ignores_requests_to_other_paths():
    inner = _RecordingApp()
    middleware = MaxUploadSizeMiddleware(inner, path="/resumes/upload", max_bytes=10)
    chunks = [b"x" * 1000]  # far over the cap, but this path is not gated
    sent = _run(middleware, _http_scope("/jobs"), _make_receive(chunks))

    assert inner.received_chunks == chunks
    assert sent[0]["status"] == 200

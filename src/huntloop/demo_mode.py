"""
Demo mode configuration, read from environment variables.

DEMO_MODE turns the live API into a safe, read-only public demo. off
(the default) means every route behaves exactly as it did before this
module existed. on means the three unauthenticated write routes and the
BYOK drafting route are not mounted, the application-tracker PATCH
becomes a no-op, sponsorship figures are served from small precomputed
aggregates instead of raw LCA rows, and a generous per-IP rate limit
applies to every route.

is_demo_mode() reads the environment fresh on every call rather than
caching a value at import time, so tests can flip DEMO_MODE between
cases without reloading this module.
"""
import os

_TRUTHY = ("1", "true", "yes", "on")


def is_demo_mode() -> bool:
    return os.getenv("DEMO_MODE", "false").strip().lower() in _TRUTHY


def demo_rate_limit_max_requests() -> int:
    return int(os.getenv("DEMO_RATE_LIMIT_MAX_REQUESTS", "120"))


def demo_rate_limit_window_seconds() -> float:
    return float(os.getenv("DEMO_RATE_LIMIT_WINDOW_SECONDS", "60"))

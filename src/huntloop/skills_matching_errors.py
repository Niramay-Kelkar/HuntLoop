"""
Shared exception types for the skills-matching backends (Groq, Gemini) and
the router that dispatches between them.

Kept in a dependency-free module of its own so every backend can raise the
*same* class (not one private copy each) - the router catches these by
identity, and a backend importing this never drags in another backend's
API-key requirement.
"""


class DailyQuotaExhausted(Exception):
    """A provider reports its per-DAY quota is spent (Groq: a 429 whose body
    mentions "tokens per day"; Gemini: a 429 naming a per-day request
    metric). Distinct from every other failure, which just returns
    ``[None] * n`` and gets retried on the next run: once a daily budget is
    gone, retrying the same provider for the rest of the day is waste, not
    resilience. The router marks that provider exhausted for the run and
    fails over; the caller stops the run only once *every* provider has
    raised this (``AllProvidersExhausted``)."""


class ProviderResponseInvalid(Exception):
    """A provider hard-failed a request in a way that is per-request flaky,
    not a daily wall - specifically Groq's ``json_validate_failed`` 400
    (its JSON-mode validator rejecting gpt-oss-20b's own output; ~18% of
    calls in the Step I test). Without a distinct signal these silently
    became ``[None] * n`` and got retried against the *same* flaky
    provider next run. The router fails just this one batch over to the
    next provider and keeps the raising provider primary for later
    batches."""

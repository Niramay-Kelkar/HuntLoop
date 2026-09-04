"""
Provider-routing wrapper for skills matching: Groq gpt-oss-120b primary,
Groq gpt-oss-20b second, Gemini fallback, optional Mistral fourth stage.

WIRED into scripts/backfill_skills_matching.py as of 2026-08-29 (Step K,
see SESSIONS.md + huntloop-architecture-decisions.md). It became
load-bearing, not a nice-to-have, once ATS coverage went 9 -> 380
companies: real relevant-postings volume is ~120/day while Groq's real
free-tier throughput at the wider set's ~9.5k-char job descriptions is
only ~55/day, so Groq alone can no longer keep up.

Public surface (mirrors a single backend, plus a per-run `state`):

    make_run_state() -> dict
    match_skills_batch(resume_text, job_descriptions, state) -> list[dict|None]
    active_provider(state) -> str
    batch_limits(state) -> (max_batch_size, max_batch_estimated_tokens, target_tpm)
    AllProvidersExhausted
    DailyQuotaExhausted            (re-exported from skills_matching_errors)
    MODEL_NAME, _BATCH_SYSTEM_PROMPT   (from the first configured provider)

Failover rules:

  * DailyQuotaExhausted (either backend's per-day wall) -> that provider
    is marked exhausted for the REST OF THIS RUN; the current batch and
    every later one go to the next provider. When every provider is
    exhausted, raise AllProvidersExhausted so the caller stops the run
    for the day - exactly what it did on Groq's DailyQuotaExhausted
    before routing existed.

  * ProviderResponseInvalid (Groq json_validate_failed - ~18% of calls
    in the Step I test) -> fail over JUST THIS BATCH to the next
    provider; the raising provider stays primary for later batches,
    since this failure is per-request flaky, not a daily wall. This is
    the Step I open item: before, these silently became [None]*n and got
    retried against the same flaky provider on the next run.

  * Anything else (transient None entries, per-minute 429, timeout,
    unparseable response) -> returned straight through as-is. NOT a
    failover trigger: switching providers on noise would burn the
    fallback's finite daily quota for nothing.

Config: SKILLS_MATCHING_PROVIDERS env, comma-separated, default
"groq_120b,groq,gemini" (as of 2026-09-04 - see SESSIONS.md for why
groq_120b was promoted ahead of groq in the default chain). Setting it
to "groq" alone reproduces the original pre-routing behavior and needs
no GEMINI_API_KEY (the gemini backend is imported lazily, only if it's
in the chain).

"groq_120b" (huntloop.skills_matching_groq_120b, openai/gpt-oss-120b,
built 2026-09-04) is a same-account, same-key Groq capacity stage -
confirmed live to hold its OWN independent rate-limit bucket from
"groq" (openai/gpt-oss-20b), needing no separate API key. Its 11-job
validation came back clean (0/11 full-resume dumps, fixed the standing
Palantir "Deployment Strategist" failure case every other backend has
hit - see SESSIONS.md 2026-09-04), so it is now the DEFAULT primary
stage, tried before the original "groq" stage - two independent buckets
on the same Groq account, tried in size order (bigger model first).

"mistral" is a known further stage (huntloop.skills_matching_mistral,
built 2026-09-03) that can be appended
("groq_120b,groq,gemini,mistral"); it is NOT in the default chain and
needs MISTRAL_API_KEY only when actually listed. Its 11-job validation
came back POOR (ministral-8b full-resume-dumps on 7/11 - see
SESSIONS.md 2026-09-03), so it is wired-but-not-recommended: a
last-resort capacity bucket if every other stage is ever walled, not a
quality peer.
"""
import logging
import os
from collections import Counter

from huntloop.skills_matching_errors import DailyQuotaExhausted, ProviderResponseInvalid

logger = logging.getLogger(__name__)

PROVIDER_CHAIN = [
    p.strip().lower()
    for p in os.getenv("SKILLS_MATCHING_PROVIDERS", "groq_120b,groq,gemini").split(",")
    if p.strip()
]
if not PROVIDER_CHAIN:
    raise RuntimeError("SKILLS_MATCHING_PROVIDERS resolved to an empty provider chain")

_KNOWN = {"groq", "gemini", "mistral", "groq_120b"}
_unknown = [p for p in PROVIDER_CHAIN if p not in _KNOWN]
if _unknown:
    raise RuntimeError(f"SKILLS_MATCHING_PROVIDERS has unknown provider(s): {_unknown} (known: {sorted(_KNOWN)})")


class AllProvidersExhausted(Exception):
    """Every provider in PROVIDER_CHAIN has raised DailyQuotaExhausted
    within this run. The caller stops the run for the day and re-runs
    once the quota windows reset (the backfill is already designed to be
    re-run daily and is interrupt-safe)."""


_LOADED: dict = {}


def _backend(name: str):
    """Lazy-import a backend module. gemini/mistral/groq_120b are only
    imported if actually in the chain / actually reached - so a groq-only
    config never needs GEMINI_API_KEY, and the default groq,gemini config
    never needs MISTRAL_API_KEY. groq_120b reuses GROQ_API_KEY (same
    account/key as the primary "groq" stage), so it needs no new env var
    at all."""
    m = _LOADED.get(name)
    if m is None:
        if name == "groq":
            from huntloop import skills_matching as m
        elif name == "gemini":
            from huntloop import skills_matching_gemini as m
        elif name == "mistral":
            from huntloop import skills_matching_mistral as m
        elif name == "groq_120b":
            from huntloop import skills_matching_groq_120b as m
        else:  # pragma: no cover - guarded at import
            raise ValueError(f"unknown skills-matching provider {name!r}")
        _LOADED[name] = m
    return m


def make_run_state() -> dict:
    """Fresh per-run routing state. Held in memory only - a new backfill
    invocation starts over (correct: quota windows reset on their own)."""
    return {
        "exhausted": set(),            # provider names that hit their daily wall this run
        "jobs_by": Counter(),          # provider -> non-None results returned
        "batches_by": Counter(),       # provider -> batches completed
        "daily_quota_hits": Counter(),  # provider -> DailyQuotaExhausted count
        "batch_failover": Counter(),   # provider -> ProviderResponseInvalid failovers
        "batch_giveups": 0,            # batches every available provider structural-failed
        "last_provider": None,         # which provider actually handled the most recent batch
    }


def active_provider(state: dict) -> str:
    """The provider a batch would be tried on right now: first in the
    chain not yet exhausted this run. Raises AllProvidersExhausted if
    every provider is exhausted."""
    for name in PROVIDER_CHAIN:
        if name not in state["exhausted"]:
            return name
    raise AllProvidersExhausted(f"all providers exhausted: {sorted(state['exhausted'])}")


def batch_limits(state: dict) -> tuple[int, int, int, int]:
    """(max_batch_size, max_batch_estimated_tokens, target_tpm, max_rpm)
    for the currently-active provider - so the caller chunks/paces to
    whatever provider will actually handle the next batch. These flip
    when a provider gets exhausted mid-run (Groq's 7k cap / 6k TPM / 30
    RPM -> Gemini's 16k / 200k TPM / 14 RPM)."""
    m = _backend(active_provider(state))
    return (m.MAX_BATCH_SIZE, m.MAX_BATCH_ESTIMATED_TOKENS, m.TARGET_TPM, m.MAX_RPM)


def match_skills_batch(resume_text: str, job_descriptions: list[str], state: dict) -> list[dict | None]:
    """Route one batch through the provider chain. Same return contract as
    a single backend's match_skills_batch (list same length/order as
    job_descriptions; entries are result dicts or None). Raises
    AllProvidersExhausted only when the chain is fully spent."""
    n = len(job_descriptions)
    attempted: list[str] = []

    for name in PROVIDER_CHAIN:
        if name in state["exhausted"]:
            continue
        attempted.append(name)
        backend = _backend(name)
        try:
            results = backend.match_skills_batch(resume_text, job_descriptions)
        except DailyQuotaExhausted as e:
            state["exhausted"].add(name)
            state["daily_quota_hits"][name] += 1
            logger.warning(
                "provider %r hit its daily quota (after %d jobs this run) - marking exhausted, "
                "failing over: %s", name, state["jobs_by"][name], str(e)[:200],
            )
            continue  # retry THIS batch on the next provider
        except ProviderResponseInvalid as e:
            state["batch_failover"][name] += 1
            logger.warning(
                "provider %r structural failure on a batch of %d (%s) - failing this batch over "
                "to the next provider; %r stays primary for later batches",
                name, n, str(e)[:160], name,
            )
            continue  # retry THIS batch on the next provider, keep `name` primary

        state["jobs_by"][name] += sum(1 for r in results if r is not None)
        state["batches_by"][name] += 1
        state["last_provider"] = name
        return results

    # Fell through without a provider handling the batch.
    if all(p in state["exhausted"] for p in PROVIDER_CHAIN):
        raise AllProvidersExhausted(f"all providers exhausted: {sorted(state['exhausted'])}")
    # Some provider(s) were available but every one structural-failed this
    # batch. Honour the old contract: leave the rows NULL for the next run.
    state["batch_giveups"] += 1
    state["last_provider"] = None
    logger.warning(
        "every available provider %s structural-failed this batch of %d - leaving NULL for reprocess",
        attempted, n,
    )
    return [None] * n


def run_summary(state: dict) -> str:
    """One-line human summary of a run's routing outcome, for the caller's
    end-of-run log."""
    parts = [f"{name}: {state['jobs_by'][name]} jobs / {state['batches_by'][name]} batches"
             for name in PROVIDER_CHAIN]
    extras = []
    if state["daily_quota_hits"]:
        extras.append(f"daily-quota hits {dict(state['daily_quota_hits'])}")
    if state["batch_failover"]:
        extras.append(f"json-validate failovers {dict(state['batch_failover'])}")
    if state["batch_giveups"]:
        extras.append(f"batches left NULL {state['batch_giveups']}")
    return "; ".join(parts + extras)


# --- compat re-exports for scripts/backfill_skills_matching.py ---
# Only the FIRST provider is loaded at import (so a "groq" chain never
# touches GEMINI_API_KEY; the fallback loads lazily when first reached).
# MODEL_NAME only labels logs; _BATCH_SYSTEM_PROMPT feeds the backfill's
# token estimation and is byte-identical across the groq/gemini backends
# by construction.
_first = _backend(PROVIDER_CHAIN[0])
MODEL_NAME = _first.MODEL_NAME
_BATCH_SYSTEM_PROMPT = _first._BATCH_SYSTEM_PROMPT

__all__ = [
    "PROVIDER_CHAIN", "AllProvidersExhausted", "DailyQuotaExhausted",
    "make_run_state", "match_skills_batch", "active_provider", "batch_limits",
    "run_summary", "MODEL_NAME",
]

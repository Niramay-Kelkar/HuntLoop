"""
Tavily-backed company research fetch, for scripts/backfill_company_research.py.

A standalone integration, unrelated to huntloop.feedback_triage /
huntloop.skills_matching* - it shares no code or prompt with either.
Public surface:

    fetch_company_research(company_name, website=None) -> CompanyResearchResult | None

Returns None on any failure (network error, non-200, malformed body) -
same "never crash the caller, just report no result" contract every
other external-API-calling module in this project uses. The caller
(the backfill script) decides what counts against the request budget.

Credit budget: this account has a hard 1000 Tavily credits/month, and
there are already several hundred companies in the database - a single
pass over all of them is a meaningful fraction of that budget. Two
choices follow directly from that:

  - search_depth="basic", not "advanced" - Tavily's documented pricing
    charges more credits per "advanced" call; "basic" is the cheaper
    tier and its results were good enough in testing (see SESSIONS.md)
    to produce a usable summary and signal extraction.
  - Exactly ONE Tavily call per company, not one call per signal, so
    funding_signal/hiring_signal are extracted locally from that same
    response (see _extract_signal below) rather than spending a second
    or third credit per company on separate funding/hiring queries.

Tavily's /search response on this account does not include a per-call
credit-cost field (confirmed by inspecting a real response before
writing this module) - so there is nothing to reconcile a local request
counter against; the caller counts 1 real call = 1 credit.
"""
import logging
import os
import re

from dataclasses import dataclass, field

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
if not TAVILY_API_KEY:
    raise RuntimeError(
        "TAVILY_API_KEY is not set. Copy .env.example to .env and fill in your Tavily API key."
    )

TAVILY_SEARCH_URL = "https://api.tavily.com/search"

# "basic" (not "advanced") - see module docstring for why.
TAVILY_SEARCH_DEPTH = "basic"
TAVILY_MAX_RESULTS = 5
_REQUEST_TIMEOUT_SECONDS = 30

_FUNDING_KEYWORDS = (
    "funding", "raised", "raise", "series a", "series b", "series c", "series d",
    "valuation", "valued at", "investment", "investor", "acquisition", "acquired",
    "ipo", "round", "venture capital",
)
_HIRING_KEYWORDS = (
    "hiring", "headcount", "layoff", "layoffs", "job openings", "open positions",
    "expanding its team", "growing its team", "workforce", "new hires", "recruiting",
)


@dataclass
class NewsItem:
    title: str
    url: str
    content: str
    score: float | None = None


@dataclass
class CompanyResearchResult:
    summary: str | None
    recent_news: list[NewsItem] = field(default_factory=list)
    funding_signal: str | None = None
    hiring_signal: str | None = None


def _build_query(company_name: str, website: str | None) -> str:
    query = f"{company_name} company recent news funding hiring"
    if website:
        query = f"{query} ({website})"
    return query


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    # Good enough for short answer/snippet text - not a general-purpose
    # sentence tokenizer, just enough to pull out the sentence(s) that
    # mention a funding/hiring keyword.
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _extract_signal(texts: list[str], keywords: tuple[str, ...], max_chars: int = 400) -> str | None:
    """Pulls out sentences mentioning any of `keywords` from `texts`
    (the answer plus each result's content) - a cheap local heuristic,
    not a second LLM/Tavily call, since the monthly credit budget here
    is tight enough that a second or third call per company isn't
    affordable (see module docstring). Returns None if nothing matched,
    rather than fabricating a signal."""
    matched: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for sentence in _split_sentences(text):
            lowered = sentence.lower()
            if any(kw in lowered for kw in keywords) and sentence not in seen:
                matched.append(sentence)
                seen.add(sentence)
    if not matched:
        return None
    joined = " ".join(matched)
    return joined[:max_chars].rstrip()


def fetch_company_research(
    company_name: str, website: str | None = None
) -> tuple[bool, CompanyResearchResult | None]:
    """One Tavily /search call for this company.

    Returns (credit_spent, result). `credit_spent` is True whenever
    Tavily actually returned an HTTP response (even an error or
    malformed one) - that's a real request Tavily served, so it counts
    against the monthly budget regardless of whether `result` ends up
    usable. `credit_spent` is False only when no response ever came back
    at all (a network/timeout failure before Tavily had a chance to
    serve the request) - never raises."""
    query = _build_query(company_name, website)
    try:
        response = requests.post(
            TAVILY_SEARCH_URL,
            headers={"Content-Type": "application/json"},
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": TAVILY_SEARCH_DEPTH,
                "include_answer": True,
                "max_results": TAVILY_MAX_RESULTS,
                "topic": "general",
            },
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        logger.warning(f"Tavily request for {company_name!r} failed before any response ({type(e).__name__}): {e}")
        return False, None

    # A response WAS received at this point - count one credit spent,
    # whether or not the body below turns out to be usable.
    if response.status_code != 200:
        logger.warning(f"Tavily search for {company_name!r} returned HTTP {response.status_code}: {response.text[:300]!r}")
        return True, None

    try:
        body = response.json()
    except ValueError as e:
        logger.warning(f"Tavily response for {company_name!r} was not valid JSON ({e}): {response.text[:300]!r}")
        return True, None

    raw_results = body.get("results") or []
    news_items = [
        NewsItem(
            title=r.get("title") or "",
            url=r.get("url") or "",
            content=(r.get("content") or "")[:500],
            score=r.get("score"),
        )
        for r in raw_results
        if r.get("url")
    ]

    answer = body.get("answer") or None
    signal_texts = ([answer] if answer else []) + [item.content for item in news_items]
    funding_signal = _extract_signal(signal_texts, _FUNDING_KEYWORDS)
    hiring_signal = _extract_signal(signal_texts, _HIRING_KEYWORDS)

    if not answer and not news_items:
        logger.warning(f"Tavily search for {company_name!r} returned no answer and no results")

    return True, CompanyResearchResult(
        summary=answer,
        recent_news=news_items,
        funding_signal=funding_signal,
        hiring_signal=hiring_signal,
    )

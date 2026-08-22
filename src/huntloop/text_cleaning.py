"""
Text cleaning shared by anything that embeds resume or job-posting text
(see huntloop.embeddings) - HTML markup and PDF-extraction glyph
artifacts pollute embeddings if left in, so this runs before any text
reaches the embedding model.

Checked real data before writing this, not assumed:
- The active resume's extracted_text (scripts/ingest_resume.py, via
  pdfplumber) contains literal "(cid:127)" in place of every bullet
  point - pdfminer/pdfplumber couldn't map that PDF's bullet glyph to a
  real Unicode codepoint in the font's ToUnicode CMap. No other (cid:N)
  values appeared in that resume, but the pattern below is generalized
  to any (cid:<digits>) rather than hardcoding 127, since a different
  PDF/font could hit a different id.
- job_postings.job_description is HTML, not plain text, in every
  non-trivial row across all 5 real scraped companies (checked via a
  direct query, see SESSIONS.md) - and in two different forms depending
  on source: Greenhouse-sourced rows (checkr/duolingo/figma) come back
  HTML-entity-escaped (e.g. "&lt;p&gt;...&lt;/p&gt;"), while
  Lever-sourced rows (palantir/wealthfront) come back as raw HTML
  ("<div>...</div>"). A single html.unescape() call turns the escaped
  form into the same raw-HTML shape the Lever rows already have, so one
  code path handles both.
"""
import html
import re

_CID_ARTIFACT_RE = re.compile(r"\(cid:\d+\)")
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def clean_text(text: str) -> str:
    """Decode HTML entities, strip HTML tags, replace pdfminer (cid:N)
    glyph-mapping artifacts with a plain "-", and collapse repeated
    whitespace. Safe on plain text with none of the above - a no-op for
    anything that doesn't match. Returns "" for None/empty input."""
    if not text:
        return ""
    cleaned = html.unescape(text)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = _CID_ARTIFACT_RE.sub(" - ", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned

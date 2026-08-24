"""
Resume-PDF text extraction and storage, shared by scripts/ingest_resume.py
(manual, one-off ingestion) and huntloop.api.routers.resumes (the real
POST /resumes/upload endpoint) - factored out here so both call the exact
same logic instead of maintaining two copies. extract_text() is moved
here unchanged from scripts/ingest_resume.py's original version (see
git history) - same pdfplumber-based extraction, same behavior.

Text extraction uses pdfplumber (not pypdf) - it's layout-aware (built on
pdfminer.six), which handles multi-column resume sections and irregular
whitespace far better than pypdf's more basic extraction; a
mis-ordered/garbled extraction here would silently corrupt everything
built on top of it later, so this favors the more careful extractor.
"""
import logging
import os

import pdfplumber

logger = logging.getLogger(__name__)

# Same repo-root resolution pattern as huntloop.logging_config - this
# module lives at src/huntloop/resume_ingestion.py, three dirname() calls
# up is the repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESUMES_DIR = os.path.join(_REPO_ROOT, "data", "resumes")


def extract_text(pdf_path: str) -> str:
    """Extract text from every page of the PDF, joined with blank lines
    between pages. A page with no extractable text (e.g. a scanned image
    with no text layer) contributes nothing rather than raising - the
    caller decides what to do with a suspiciously short result."""
    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text()
            if text:
                pages_text.append(text)
            else:
                logger.warning(f"No extractable text on page {page_number} of {pdf_path}")
    return "\n\n".join(pages_text)


def save_uploaded_pdf(version_number: int, filename: str, content: bytes) -> str:
    """Writes `content` to RESUMES_DIR (data/resumes/ - same location
    scripts/ingest_resume.py has always expected resumes to live) under
    a version-number-prefixed, basename-only version of `filename` - the
    prefix guarantees each version gets its own file even if two uploads
    share a filename; basename-only (no directory components from the
    client-supplied name) avoids writing outside RESUMES_DIR. Returns
    the path written to."""
    os.makedirs(RESUMES_DIR, exist_ok=True)
    safe_name = os.path.basename(filename)
    path = os.path.join(RESUMES_DIR, f"v{version_number}_{safe_name}")
    with open(path, "wb") as f:
        f.write(content)
    return path

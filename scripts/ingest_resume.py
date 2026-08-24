"""
One-off script: extract text from a resume PDF and insert it as a new
row in `resume_versions` (see huntloop.db_models.ResumeVersion).

Not part of the app's ongoing pipeline - run manually via:

    python scripts/ingest_resume.py data/resumes/<your-resume>.pdf

Each run inserts a new version rather than overwriting the last one:
version_number auto-increments from the current max, the new row is
marked is_active=True, and any previously-active row is flipped to
is_active=False (never deleted) - so resume history is preserved and
there's always exactly one active version to eventually match against.

Ingestion only, per this step's scope: no embeddings, no matching, no
LLM-suggestion or skills-extraction logic. That's all later work once
this data exists to build on.

Text extraction (pdfplumber-based - layout-aware, handles multi-column
resume sections and irregular whitespace far better than pypdf's more
basic extraction) lives in huntloop.resume_ingestion.extract_text(),
shared with the real POST /resumes/upload API endpoint added later (see
huntloop.api.routers.resumes, CLAUDE.md) so both call the same logic
rather than maintaining two copies.
"""
import logging
import os
import sys

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import ResumeVersion
from huntloop.resume_ingestion import extract_text
from huntloop.settings import DATABASE_URL

logger = logging.getLogger(__name__)


def ingest_resume(session, pdf_path: str) -> ResumeVersion:
    extracted_text = extract_text(pdf_path)
    if not extracted_text.strip():
        raise ValueError(
            f"No text could be extracted from {pdf_path} - it may be a scanned "
            f"image with no text layer, or an empty/corrupt PDF."
        )

    current_max = session.query(func.max(ResumeVersion.version_number)).scalar()
    next_version = (current_max or 0) + 1

    session.query(ResumeVersion).filter_by(is_active=True).update({"is_active": False})

    resume_version = ResumeVersion(
        version_number=next_version,
        file_path=pdf_path,
        extracted_text=extracted_text,
        is_active=True,
    )
    session.add(resume_version)
    session.commit()

    logger.info(
        f"Ingested resume version {next_version} from {pdf_path} "
        f"({len(extracted_text)} chars extracted), marked active"
    )
    return resume_version


def main():
    if len(sys.argv) != 2:
        logger.error("Usage: python scripts/ingest_resume.py <path-to-resume.pdf>")
        sys.exit(1)

    pdf_path = sys.argv[1]

    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        ingest_resume(session, pdf_path)
    finally:
        session.close()


if __name__ == "__main__":
    main()

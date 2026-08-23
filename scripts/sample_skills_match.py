"""
One-off verification script: run huntloop.skills_matching.match_skills()
against a representative SAMPLE of jobs spanning Step 3's score range
(a few top-scoring, a few bottom-scoring, a few middle), not all 605 -
full backfill is a separate future step once this is validated.

Not part of the app's ongoing pipeline - run manually via:

    python scripts/sample_skills_match.py
"""
import logging
import os
import sys

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from huntloop.settings import DATABASE_URL
from huntloop.skills_matching import match_skills

logger = logging.getLogger(__name__)

# (company, job_title) pairs picked from a real query against the
# active resume's embedding vs. every job_postings.embedding
# (1 - cosine_distance, see SESSIONS.md for the exact query) - top 3,
# middle 3, bottom 3, spanning the full score range.
SAMPLE_JOB_TITLES = [
    ("palantir", "Software Engineer - Defense Applications"),
    ("palantir", "Software Engineer - Frontend Developer Productivity"),
    ("palantir", "Software Engineer, Internship - Production Infrastructure"),
    ("checkr", "Engineering Manager, Verifications"),
    ("checkr", "Chief of Staff"),
    ("checkr", "AI Conversation Designer"),
    ("wealthfront", "Fraud Operations Specialist"),
    ("wealthfront", "Senior Designer - Editorial, Creative"),
    ("duolingo", "Creative Director, Marketing"),
]


def main():
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        resume_row = session.execute(
            text("SELECT extracted_text FROM resume_versions WHERE is_active = true")
        ).first()
        if resume_row is None:
            logger.error("No active resume_versions row - nothing to match against.")
            return
        resume_text = resume_row[0]

        for company, job_title in SAMPLE_JOB_TITLES:
            row = session.execute(
                text(
                    """
                    SELECT j.job_title, j.job_description,
                           round((1 - (r.embedding <=> j.embedding))::numeric, 4) AS score
                    FROM job_postings j
                    JOIN companies c ON j.company_id = c.id
                    CROSS JOIN (SELECT embedding FROM resume_versions WHERE is_active = true) r
                    WHERE c.name = :company AND j.job_title = :job_title
                    LIMIT 1
                    """
                ),
                {"company": company, "job_title": job_title},
            ).first()

            if row is None:
                logger.warning(f"No match found for {company} / {job_title!r} - skipping")
                continue

            title, description, score = row
            result = match_skills(resume_text, description)

            print(f"\n=== {company} — {title} (score={score}) ===")
            if result is None:
                print("  Groq call failed - see warning log above.")
            else:
                print(f"  matched_skills: {result['matched_skills']}")
                print(f"  missing_skills: {result['missing_skills']}")
    finally:
        session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()

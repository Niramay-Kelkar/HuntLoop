# Manual smoke-test script - NOT part of the automated pytest suite.
# pytest.ini scopes collection to tests/, so this file (despite its
# test_ prefix) is never picked up by `pytest`.
#
# Run it directly (`python -m huntloop.test_db_insert`) to sanity-check
# end-to-end connectivity against your real local database, using
# DATABASE_URL from .env. Unlike tests/test_pipeline.py, which runs
# against an isolated schema and never touches real data, this script
# intentionally writes to the real job_postings table - that's the point
# of keeping it: a quick, no-fixtures way to confirm the actual local DB
# is reachable and insertable, separate from the automated suite.
import logging

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from huntloop.db_models import Base, Company, JobSource, JobPosting, JobLocation, JobSkill

load_dotenv()

from huntloop.settings import DATABASE_URL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    try:
        # Create engine and session
        engine = create_engine(DATABASE_URL, echo=True)  # echo=True logs SQL
        Base.metadata.create_all(engine)

        Session = sessionmaker(bind=engine)
        session = Session()

        logger.info("Connected to PostgreSQL successfully ✅")

        # 🧱 Create or find a Company
        company_name = "OpenAI"
        company = session.query(Company).filter_by(name=company_name).first()
        if not company:
            company = Company(name=company_name)
            session.add(company)
            session.commit()
            logger.info(f"Created new company: {company_name}")

        # 🌐 Create or find a Job Source
        source = session.query(JobSource).filter_by(name="Greenhouse").first()
        if not source:
            source = JobSource(name="Greenhouse")
            session.add(source)
            session.commit()
            logger.info("Created Job Source: Greenhouse")

        # 💼 Create a Job Posting
        job = JobPosting(
            job_title="Software Engineer",
            job_url="https://example.com/jobs/1234",
            job_description="Develop and deploy large-scale AI systems.",
            date_posted="2025-11-02",
            company_id=company.id,
            source_id=source.id
        )
        session.add(job)
        session.commit()
        logger.info("✅ Inserted Job Posting successfully")

        # 🌍 Add Locations
        locations = ["San Francisco, CA", "Remote - US"]
        for loc in locations:
            session.add(JobLocation(job_id=job.id, location_name=loc))
        session.commit()

        # 🧠 Add Skills
        skills = ["Python", "Machine Learning", "FastAPI"]
        for skill in skills:
            session.add(JobSkill(job_id=job.id, skill=skill))
        session.commit()

        logger.info("✅ Added job locations and skills successfully")

    except Exception as e:
        logger.error(f"❌ Error during DB test: {e}", exc_info=True)
    finally:
        session.close()
        logger.info("🛑 Session closed")

if __name__ == "__main__":
    main()

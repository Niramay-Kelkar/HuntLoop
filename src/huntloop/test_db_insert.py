import logging
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from huntloop.db_models import Base, Company, JobSource, JobPosting, JobLocation, JobSkill

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy .env.example to .env and fill in your local Postgres credentials."
    )

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

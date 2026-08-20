# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html


# useful for handling different item types with a single interface


import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from .db_models import (
    JobPosting,
    Company,
    JobLocation,
    JobSkill,
    JobSource,
    JobMetadata,
)
from sqlalchemy import create_engine

logger = logging.getLogger(__name__)

class JobDataPipeline:
    def __init__(self, database_url):
        self.database_url = database_url
        self.engine = create_engine(self.database_url, echo=False)
        self.Session = sessionmaker(bind=self.engine)

    @classmethod
    def from_crawler(cls, crawler):
        # Read Postgres credentials from settings.py
        database_url = crawler.settings.get("DATABASE_URL")
        return cls(database_url)

    def open_spider(self, spider):
        logger.warning("🚀 [DEBUG] JobDataPipeline successfully initialized.")

    def close_spider(self, spider):
        logger.info("🛑 JobDataPipeline closed")

    def process_item(self, item, spider):
        logger.warning(f"🚀 [PIPELINE TRIGGERED] Processing item: {item.get('job_title')}")
        session = self.Session()

        try:
            # 1️⃣ Company
            company_name = item.get("company_name", "").strip()
            if not company_name:
                logger.warning("⚠️ Skipping item — no company name found")
                return item

            company = session.query(Company).filter_by(name=company_name).first()
            if not company:
                company = Company(name=company_name)
                session.add(company)
                session.commit()

            # 2️⃣ Source (Greenhouse / Lever)
            source_name = item.get("name", "Greenhouse")
            source = session.query(JobSource).filter_by(name=source_name).first()
            if not source:
                source = JobSource(name=source_name)
                session.add(source)
                session.commit()

            # 3️⃣ Deduplication
            # existing_job = session.query(JobPosting).filter_by(
            #     job_title=item["job_title"],
            #     job_url=item["job_url"],
            #     company_id=company.id
            # ).first()
            #
            # if existing_job:
            #     logger.info(f"🟡 Duplicate job found, skipping: {item['job_title']} ({company_name})")
            #     session.close()
            #     return item

            existing_job = session.query(JobPosting).filter_by(gh_job_id=str(item["job_id"])).first()
            if existing_job:
                logger.info(f"🟡 Skipping reposted job {item['job_id']}")
                return item

            # 4️⃣ Create JobPosting entry
            job_post = JobPosting(
                job_title=item.get("job_title"),
                job_url=item.get("job_url"),
                gh_job_id=item.get("job_id"),
                job_description=item.get("job_description"),
                date_posted=item.get("date_posted"),
                company_id=company.id,
                source_id=source.id
            )

            session.add(job_post)
            session.commit()

            # 5️⃣ Job Locations
            for loc in item.get("job_locations", []):
                session.add(JobLocation(job_id=job_post.id, location_name=loc))
            session.commit()

            # 6️⃣ Job Skills
            for skill in item.get("job_skills", []):
                session.add(JobSkill(job_id=job_post.id, skill=skill))
            session.commit()

            # Job Meta data
            job_meta = JobMetadata(job_id=job_post.id, metadata_json=item.get("metadata_json"))
            session.add(job_meta)
            session.commit()

            logger.info(f"✅ Inserted job: {item['job_title']} for {company_name}")
        except IntegrityError as e:
            session.rollback()
            logger.error(f"❌ Integrity error: {str(e)}")
        except Exception as e:
            session.rollback()
            logger.error(f"❌ Unexpected error inserting item: {e}", exc_info=True)
        finally:
            session.close()

        return item
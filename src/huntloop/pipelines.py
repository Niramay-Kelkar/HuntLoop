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

from . import metrics
from .relevance_filter import REFERENCE_TEXT, classify_relevance, cosine_similarity

logger = logging.getLogger(__name__)

class JobDataPipeline:
    def __init__(self, database_url):
        self.database_url = database_url
        self.engine = create_engine(self.database_url, echo=False)
        self.Session = sessionmaker(bind=self.engine)
        # Cached per pipeline instance (one instance per spider run - see
        # from_crawler) so the reference embedding is computed at most
        # once per spider, not once per inserted row. _relevance_embedding_unavailable
        # is a separate cache for the "torch/sentence-transformers isn't
        # installed in this environment" case (see _get_reference_embedding)
        # so a scrape run only logs that warning once, not per item.
        self._reference_embedding = None
        self._relevance_embedding_unavailable = False

    @classmethod
    def from_crawler(cls, crawler):
        # Read Postgres credentials from settings.py
        database_url = crawler.settings.get("DATABASE_URL")
        return cls(database_url)

    def open_spider(self, spider):
        logger.warning("[DEBUG] JobDataPipeline successfully initialized.")

    def close_spider(self, spider):
        logger.info("JobDataPipeline closed")

    def _get_reference_embedding(self):
        """Lazily compute and cache REFERENCE_TEXT's embedding once per
        spider run (see __init__). Returns None if sentence-transformers/
        torch isn't installed in this environment (this project's local
        .venv doesn't have it - see huntloop.embeddings' docstring and
        CLAUDE.md) - callers treat that as "can't classify this row right
        now", not a fatal error, so a torch-less environment still
        completes a real scrape; unclassified rows are picked up later
        by scripts/backfill_relevance.py."""
        if self._reference_embedding is not None:
            return self._reference_embedding
        if self._relevance_embedding_unavailable:
            return None
        try:
            from .embeddings import embed_texts
        except ImportError:
            logger.warning(
                "sentence-transformers/torch not installed in this environment - "
                "job_postings.is_relevant will be left NULL for this run's inserts "
                "and needs a later scripts/backfill_relevance.py run."
            )
            self._relevance_embedding_unavailable = True
            return None
        self._reference_embedding = embed_texts([REFERENCE_TEXT])[0]
        return self._reference_embedding

    def _classify_and_embed(self, job_title, job_description):
        """Compute ``(is_relevant, match_embedding)`` for a newly-scraped
        row in a single model call:
          - ``is_relevant``  - the relevance-gate classification (needs a
            title+description embedding vs. REFERENCE_TEXT)
          - ``match_embedding`` - the all-MiniLM-L6-v2 embedding of the
            cleaned ``job_description`` alone, stored on
            ``job_postings.embedding`` for query-time resume-match scoring

        Either element is ``None`` if it can't be computed right now
        (sentence-transformers/torch not installed - this project's local
        .venv - or a per-row embedding failure). A ``None`` leaves that
        column NULL and it's picked up later by
        ``scripts/backfill_relevance.py`` / ``scripts/backfill_embeddings.py``.

        Wired here (not only in the backfills) as of 2026-08-31 - see
        SESSIONS.md. Before, every new ATS source needed a manual
        ``backfill_embeddings.py`` pass after its first scrape (Greenhouse/
        Lever, then Workday); now the daily Docker scrape populates
        ``embedding`` at insert exactly like ``is_relevant``. The
        backfill script is still the right tool for bulk re-scrapes
        (batches of 100 vs. the pipeline's one-row-at-a-time) and for
        torch-less runs, so it stays."""
        reference_embedding = self._get_reference_embedding()
        if reference_embedding is None:
            return None, None
        from .embeddings import embed_texts

        desc = job_description or ""
        try:
            # One batched call: [0] title+desc for the relevance gate,
            # [1] desc alone for the stored resume-match embedding.
            rel_vec, match_vec = embed_texts([f"{job_title or ''}\n{desc}", desc])
        except Exception:
            # An isolated embedding failure for this one row shouldn't
            # roll back the whole insert - leave both columns NULL for it,
            # same defensive principle as the surrounding process_item
            # try/except.
            logger.warning(
                f"Embedding failed for {job_title!r} - is_relevant/embedding left NULL", exc_info=True
            )
            return None, None

        is_relevant = None
        if job_title:
            is_relevant = classify_relevance(
                job_title, cosine_similarity(reference_embedding, rel_vec)
            )
        return is_relevant, match_vec

    def process_item(self, item, spider):
        logger.warning(f"[PIPELINE TRIGGERED] Processing item: {item.get('job_title')}")

        # Computed up front (not just inside the try) so every metrics
        # label below - including the error-path ones - has a company/
        # source to attach to, not just the happy path.
        company_name = (item.get("company_name") or "").strip()
        source_name = item.get("name") or "Greenhouse"
        metrics.jobs_scraped_total.labels(company=company_name or "unknown", source=source_name).inc()

        session = self.Session()

        try:
            # 1️⃣ Company
            if not company_name:
                logger.warning("Skipping item — no company name found")
                metrics.scrape_errors_total.labels(company="unknown", source=source_name).inc()
                return item

            company = session.query(Company).filter_by(name=company_name).first()
            if not company:
                company = Company(name=company_name)
                session.add(company)
                session.commit()

            # 2️⃣ Source (Greenhouse / Lever)
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
                logger.info(f"Skipping reposted job {item['job_id']}")
                metrics.jobs_skipped_duplicate_total.labels(company=company_name, source=source_name).inc()
                return item

            # 4️⃣ Create JobPosting entry
            is_relevant, job_embedding = self._classify_and_embed(
                item.get("job_title"), item.get("job_description")
            )

            job_post = JobPosting(
                job_title=item.get("job_title"),
                job_url=item.get("job_url"),
                gh_job_id=item.get("job_id"),
                job_description=item.get("job_description"),
                date_posted=item.get("date_posted"),
                company_id=company.id,
                source_id=source.id,
                is_relevant=is_relevant,
                embedding=job_embedding,
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

            logger.info(f"Inserted job: {item['job_title']} for {company_name}")
            metrics.jobs_inserted_total.labels(company=company_name, source=source_name).inc()
        except IntegrityError as e:
            session.rollback()
            logger.error(f"Integrity error: {str(e)}")
            metrics.scrape_errors_total.labels(company=company_name or "unknown", source=source_name).inc()
        except Exception as e:
            session.rollback()
            logger.error(f"Unexpected error inserting item: {e}", exc_info=True)
            metrics.scrape_errors_total.labels(company=company_name or "unknown", source=source_name).inc()
        finally:
            session.close()

        return item
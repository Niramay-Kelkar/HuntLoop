"""
db_models.py
Defines SQLAlchemy ORM models for job data pipeline.
"""

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Date,
    DateTime,
    Boolean,
    ForeignKey,
    JSON,
    Numeric,
    func,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import declarative_base, relationship
from pgvector.sqlalchemy import Vector as _Vector

Base = declarative_base()

# all-MiniLM-L6-v2's output dimension - confirmed against the model's own
# published config (1_Pooling/config.json: "word_embedding_dimension": 384
# on https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2), not
# assumed. See huntloop.embeddings.
EMBEDDING_DIM = 384


class Vector(_Vector):
    """pgvector's Vector type, but always emits a schema-qualified
    `public.vector(n)` in DDL instead of the bare `vector(n)` the base
    class emits. Needed for tests/conftest.py's isolated-schema fixture:
    its search_path deliberately never includes `public` (see that
    file's comment for why - adding it broke test isolation instead), so
    an unqualified `vector` type reference wouldn't resolve there even
    though the `vector` extension (CREATE EXTENSION vector) is installed
    and lives in `public` on both this project's Postgres instances.
    Schema-qualifying only affects DDL (get_col_spec) - value
    binding/result processing is inherited unchanged from pgvector's
    Vector, so this is a safe drop-in everywhere, not just for tests."""

    def get_col_spec(self, **kw):
        return f"public.{super().get_col_spec(**kw)}"


# ----------------------------------------------------------------------
# 1️⃣ Company Table
# ----------------------------------------------------------------------
class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    website = Column(String(255), nullable=True)
    # Kept for now even though real sponsorship data will live in a
    # separate table later.
    h1b_sponsorship = Column(Boolean, nullable=True, default=False, server_default="false")
    # ATS-detection result (see detect_ats(), huntloop.ats_detection).
    # Nullable - not every company has a detected value yet.
    ats_platform = Column(String(50), nullable=True)
    ats_token = Column(String(255), nullable=True)
    careers_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now())

    # Relationships
    jobs = relationship("JobPosting", back_populates="company")

    def __repr__(self):
        return f"<Company(name={self.name})>"


# ----------------------------------------------------------------------
# 2️⃣ Job Source Table
# ----------------------------------------------------------------------
class JobSource(Base):
    __tablename__ = "job_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)

    jobs = relationship("JobPosting", back_populates="source")


# ----------------------------------------------------------------------
# 3️⃣ Job Posting Table
# ----------------------------------------------------------------------
class JobPosting(Base):
    __tablename__ = "job_postings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # gh_job_id = Column(String(100), nullable=True)
    gh_job_id = Column(String(50))
    job_title = Column(String(300), nullable=False)
    job_url = Column(String(500), nullable=False)
    department = Column(String(255), nullable=True)
    job_description = Column(Text, nullable=True)
    date_posted = Column(DateTime, nullable=True)
    is_active = Column(Boolean, nullable=True, default=True, server_default="true")
    scraped_at = Column(DateTime(timezone=True), server_default=func.now())
    last_checked = Column(DateTime, nullable=True)
    # all-MiniLM-L6-v2 embedding of the cleaned job_description (see
    # huntloop.text_cleaning, huntloop.embeddings). Nullable - backfilled
    # separately (scripts/backfill_job_embeddings.py), not computed at
    # insert time by JobDataPipeline yet.
    embedding = Column(Vector(EMBEDDING_DIM), nullable=True)

    # Foreign Keys
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"))
    source_id = Column(Integer, ForeignKey("job_sources.id", ondelete="SET NULL"))

    # Relationships
    company = relationship("Company", back_populates="jobs")
    source = relationship("JobSource", back_populates="jobs")
    locations = relationship("JobLocation", back_populates="job", cascade="all, delete-orphan")
    skills = relationship("JobSkill", back_populates="job", cascade="all, delete-orphan")

    # Avoid duplicate entries. Matches the constraint already enforced live.
    __table_args__ = (UniqueConstraint('job_url', name='job_postings_job_url_key'),)

    def __repr__(self):
        return f"<JobPosting(title={self.job_title}, company={self.company_id})>"


# ----------------------------------------------------------------------
# 4️⃣ Job Locations Table
# ----------------------------------------------------------------------
class JobLocation(Base):
    __tablename__ = "job_locations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job_postings.id", ondelete="CASCADE"))
    location_name = Column(String(255), nullable=False)

    job = relationship("JobPosting", back_populates="locations")

    def __repr__(self):
        return f"<JobLocation(location={self.location_name})>"


# ----------------------------------------------------------------------
# 5️⃣ Job Skills Table
# ----------------------------------------------------------------------
class JobSkill(Base):
    __tablename__ = "job_skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job_postings.id", ondelete="CASCADE"))
    skill = Column(String(255), nullable=False)

    job = relationship("JobPosting", back_populates="skills")

    def __repr__(self):
        return f"<JobSkill(skill={self.skill})>"


# ----------------------------------------------------------------------
# 6️⃣ (Optional) Raw Metadata JSON Storage Table
# ----------------------------------------------------------------------
class JobMetadata(Base):
    __tablename__ = "job_metadata"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("job_postings.id", ondelete="CASCADE"))
    metadata_json = Column(JSON, nullable=True)

    def __repr__(self):
        return f"<JobMetadata(job_id={self.job_id})>"


# ----------------------------------------------------------------------
# 7️⃣ LCA Disclosure Table
# ----------------------------------------------------------------------
class LcaDisclosure(Base):
    """
    One row per DOL LCA disclosure record, as filed. Stands alone for now -
    no FK to companies until the employer-name normalization/matching logic
    (Step 5) exists. employer_name_normalized is nullable and left unset
    until that same step.
    """
    __tablename__ = "lca_disclosures"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_number = Column(String(50), unique=True, nullable=False)
    employer_name = Column(String(255), nullable=False, index=True)
    employer_name_normalized = Column(String(255), nullable=True, index=True)
    trade_name_dba = Column(String(255), nullable=True)
    case_status = Column(String(50), nullable=False)
    job_title = Column(String(300), nullable=False)
    soc_code = Column(String(20), nullable=True)
    soc_title = Column(String(255), nullable=True)
    worksite_city = Column(String(255), nullable=True)
    worksite_state = Column(String(10), nullable=True)
    worksite_postal_code = Column(String(20), nullable=True)
    wage_rate_of_pay_from = Column(Numeric, nullable=True)
    wage_rate_of_pay_to = Column(Numeric, nullable=True)
    wage_unit_of_pay = Column(String(20), nullable=True)
    received_date = Column(Date, nullable=True)
    decision_date = Column(Date, nullable=True)
    fiscal_year = Column(Integer, nullable=False, index=True)
    quarter = Column(Integer, nullable=False)
    source_file = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_lca_disclosures_fiscal_year_quarter", "fiscal_year", "quarter"),
    )

    def __repr__(self):
        return f"<LcaDisclosure(case_number={self.case_number}, employer={self.employer_name})>"


# ----------------------------------------------------------------------
# 8️⃣ Sponsor Name Override Table
# ----------------------------------------------------------------------
class SponsorNameOverride(Base):
    """
    Manual correction layer for company-name -> employer_name_normalized
    matching. Not populated with curated entries yet - this table is just
    the mechanism (see find_matching_employers in
    huntloop.matching.fuzzy_match), which checks this table first and
    short-circuits to it before falling back to fuzzy matching against
    lca_disclosures.employer_name_normalized.
    """
    __tablename__ = "sponsor_name_overrides"

    id = Column(Integer, primary_key=True, autoincrement=True)
    raw_company_name = Column(String(255), unique=True, nullable=False, index=True)
    employer_name_normalized = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return (
            f"<SponsorNameOverride(raw_company_name={self.raw_company_name}, "
            f"employer_name_normalized={self.employer_name_normalized})>"
        )


# ----------------------------------------------------------------------
# 9️⃣ Resume Version Table
# ----------------------------------------------------------------------
class ResumeVersion(Base):
    """
    Ingested resume text, versioned so a resume update never overwrites
    history - see scripts/ingest_resume.py. `is_active` marks the single
    version to match against; ingesting a new version flips the previous
    active row to inactive rather than deleting it. `embedding` is an
    all-MiniLM-L6-v2 embedding of the cleaned extracted_text (see
    huntloop.text_cleaning, huntloop.embeddings,
    scripts/backfill_job_embeddings.py) - computed for the active version
    only. Match scoring is computed at query time via pgvector's `<=>`
    operator against job_postings.embedding, not stored - no skills-list
    or LLM-suggestion logic lives here yet (deliberately out of scope,
    see SESSIONS.md).
    """
    __tablename__ = "resume_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version_number = Column(Integer, nullable=False, unique=True)
    uploaded_at = Column(DateTime, server_default=func.now())
    file_path = Column(String(500), nullable=False)
    extracted_text = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False, server_default="false")
    embedding = Column(Vector(EMBEDDING_DIM), nullable=True)

    def __repr__(self):
        return (
            f"<ResumeVersion(version_number={self.version_number}, "
            f"is_active={self.is_active})>"
        )
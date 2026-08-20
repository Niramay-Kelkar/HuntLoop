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

Base = declarative_base()


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
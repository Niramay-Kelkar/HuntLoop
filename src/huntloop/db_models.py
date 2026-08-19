"""
db_models.py
Defines SQLAlchemy ORM models for job data pipeline.
"""

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Boolean,
    ForeignKey,
    JSON,
    func,
    UniqueConstraint
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
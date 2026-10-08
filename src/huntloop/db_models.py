"""
db_models.py
Defines SQLAlchemy ORM models for job data pipeline.
"""

import enum

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Date,
    DateTime,
    Boolean,
    Enum,
    Float,
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
    `public.vector(n)` in DDL, and schema-qualified `OPERATOR(public.<op>)`
    syntax for distance comparisons, instead of the bare `vector(n)` /
    `<=>` etc. the base class emits. Needed for tests/conftest.py's
    isolated-schema fixture: its search_path deliberately never includes
    `public` (see that file's comment for why - adding it broke test
    isolation instead), so an unqualified `vector` type OR an unqualified
    `<=>`/`<->`/etc. operator wouldn't resolve there - confirmed directly
    (2026-08-22, see SESSIONS.md): even with both operands explicitly
    cast to `public.vector`, a bare `<=>` still failed with "operator
    does not exist" under a search_path without `public`, while
    `OPERATOR(public.<=>)` resolved correctly regardless of search_path
    (Postgres's schema-qualified-operator syntax bypasses name lookup via
    search_path entirely, including for the right operand's "unknown"-
    typed bind parameter). Both fixes only affect DDL/SQL generation -
    value binding/result processing are inherited unchanged from
    pgvector's Vector, so this is a safe drop-in everywhere, not just
    for tests."""

    # Explicit even though the base class already sets this - subclassing
    # otherwise triggers a "will not produce a cache key" SAWarning on
    # every query using this type.
    cache_ok = True

    def get_col_spec(self, **kw):
        return f"public.{super().get_col_spec(**kw)}"

    class Comparator(_Vector.Comparator):
        def l2_distance(self, other, /):
            return self.op("OPERATOR(public.<->)", return_type=Float)(other)

        def max_inner_product(self, other, /):
            return self.op("OPERATOR(public.<#>)", return_type=Float)(other)

        def cosine_distance(self, other, /):
            return self.op("OPERATOR(public.<=>)", return_type=Float)(other)

        def l1_distance(self, other, /):
            return self.op("OPERATOR(public.<+>)", return_type=Float)(other)

    comparator_factory = Comparator


# ----------------------------------------------------------------------
# 1️⃣ Company Table
# ----------------------------------------------------------------------
class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    # Human-readable company name for display only - NOT a replacement for
    # `name` (the lowercase ATS slug), which stays the dedup/sync/filter key
    # everywhere. Nullable - populated per-platform where a reliable source
    # exists (see scripts/backfill_company_display_names.py); NULL means
    # "no reliable source found yet", not "blank on purpose" - callers
    # should fall back to `name` when this is NULL.
    display_name = Column(String(255), nullable=True)
    website = Column(String(255), nullable=True)
    # Kept for now even though real sponsorship data will live in a
    # separate table later.
    h1b_sponsorship = Column(Boolean, nullable=True, default=False, server_default="false")
    # ATS-detection result (see detect_ats(), huntloop.ats_detection).
    # Nullable - not every company has a detected value yet.
    ats_platform = Column(String(50), nullable=True)
    ats_token = Column(String(255), nullable=True)
    careers_url = Column(String(500), nullable=True)
    # Resolved via find_matching_employers() (huntloop.matching.fuzzy_match)
    # - the top-scoring lca_disclosures.employer_name_normalized match for
    # this company, or the sponsor_name_overrides value when one exists.
    # Nullable: left NULL when no match clears the threshold, rather than
    # forced to a low-confidence guess. Populated by
    # scripts/resolve_sponsor_matches.py, not automatically kept fresh.
    matched_sponsor_employer_name = Column(String(255), nullable=True)
    # When scripts/resolve_sponsor_matches.py last checked this company -
    # set every time it processes a row, whether or not a match cleared
    # the threshold. NULL means the matcher has never run against this
    # company at all, which is a distinct state from "checked, no match"
    # (matched_sponsor_employer_name NULL but sponsor_checked_at set).
    sponsor_checked_at = Column(DateTime, nullable=True)
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
    # Canonical, controlled-vocabulary category the raw `department`
    # free-text string is mapped onto (see
    # huntloop.department_categorization) - one of the ~18 canonical
    # categories or "Other". Additive: `department` above is kept
    # unchanged. NULL when `department` itself is NULL (nothing to
    # categorize) or not yet categorized.
    department_category = Column(String(50), nullable=True)
    # Normalized employment type (see huntloop.employment_type) - one of
    # Full-time/Part-time/Contract/Internship/Other, or NULL when the
    # source genuinely exposes no employment-type signal at all for this
    # posting (as opposed to a real-but-unrecognized raw label, which
    # normalizes to "Other" rather than NULL - see that module's
    # docstring for the full reasoning).
    employment_type = Column(String(50), nullable=True)
    job_description = Column(Text, nullable=True)
    date_posted = Column(DateTime, nullable=True)
    is_active = Column(Boolean, nullable=True, default=True, server_default="true")
    scraped_at = Column(DateTime(timezone=True), server_default=func.now())
    last_checked = Column(DateTime, nullable=True)
    # all-MiniLM-L6-v2 embedding of the cleaned job_description (see
    # huntloop.text_cleaning, huntloop.embeddings). Computed at insert
    # time by JobDataPipeline._classify_and_embed (2026-08-31), alongside
    # is_relevant. Nullable: left NULL when the pipeline runs without
    # torch (this project's local .venv) or an isolated per-row embedding
    # failure - scripts/backfill_embeddings.py fills those, and is still
    # the right tool for bulk re-scrapes (batches of 100).
    embedding = Column(Vector(EMBEDDING_DIM), nullable=True)
    # Precomputed matched/missing skills vs. the active resume (see
    # huntloop.skills_matching, scripts/backfill_skills_matching.py).
    # Stored, not computed live per view - nullable because not every
    # row has been backfilled (or a backfill attempt may have failed for
    # that specific row; see that script for how failures are handled).
    matched_skills = Column(JSON, nullable=True)
    missing_skills = Column(JSON, nullable=True)
    # Hybrid keyword + embedding-similarity relevance pre-filter (see
    # huntloop.relevance_filter, scripts/backfill_relevance.py) - flags
    # whether a posting looks like a software-engineering/technical role
    # at all, before resume-matching or skills-analysis spend any effort
    # on it. Nullable - not every row has been classified yet; a NULL
    # here is "not yet classified", not "unknown/irrelevant". Flags only,
    # never filters rows out of this table.
    is_relevant = Column(Boolean, nullable=True)

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
    # Canonical grouping derived from the messy free-text location_name by
    # huntloop.location_normalization (offline gazetteer, no geocoding).
    # location_name itself is never modified - these are additive.
    location_city = Column(String(120), nullable=True)
    location_region = Column(String(64), nullable=True)  # 2-letter US state / CA province
    location_country = Column(String(80), nullable=True)
    location_is_remote = Column(Boolean, nullable=True)
    location_canonical = Column(String(255), nullable=True)

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
    # Schema groundwork only for a future multi-user direction that has
    # been discussed but not committed to - see
    # huntloop-architecture-decisions.md. Nullable, no default, and
    # deliberately no ForeignKey yet (there is no users table to
    # reference). Nothing reads or writes this column yet - added now
    # while resume_versions is small and cheap to alter, not because
    # multi-user support is being built.
    owner_id = Column(Integer, nullable=True)

    def __repr__(self):
        return (
            f"<ResumeVersion(version_number={self.version_number}, "
            f"is_active={self.is_active})>"
        )


# ----------------------------------------------------------------------
# 🔟 Job Application Table
# ----------------------------------------------------------------------
class ApplicationStatus(str, enum.Enum):
    """A job's application status, as tracked by the user - not the
    job's own is_active/scraped state. Default is not_applied for any
    job_postings row with no job_applications row yet (see
    huntloop.api.routers.jobs, which applies that default at query time
    - a job_postings row with no application isn't required to have one
    inserted just to represent "not applied yet")."""

    NOT_APPLIED = "not_applied"
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    REJECTED = "rejected"
    OFFER = "offer"


class JobApplication(Base):
    """
    Tracks the user's application status for a job posting - separate
    from job_postings itself (which only tracks whether the posting is
    still live/scraped). One row per job_posting_id at most
    (job_posting_id is unique - "the" application status for a job, not
    a history of every status change); PATCH /jobs/{id}/application
    (huntloop.api.routers.jobs) upserts this row rather than always
    inserting a new one. A job_postings row with no matching
    job_applications row is treated as not_applied by the API, not
    backfilled with one - see ApplicationStatus.
    """
    __tablename__ = "job_applications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_posting_id = Column(
        Integer,
        ForeignKey("job_postings.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status = Column(
        # values_callable is required here: SQLAlchemy's Enum otherwise
        # maps a Python Enum's *member name* (e.g. "NOT_APPLIED") to/from
        # the DB by default, not its .value - but the Postgres enum type
        # (created by the alembic migration with literal lowercase labels
        # like 'not_applied') and server_default below both use .value.
        # Without this, reading any row back raises LookupError: '...'
        # is not among the defined enum values - caught by actually
        # hitting GET /jobs, not just by writing the migration.
        Enum(ApplicationStatus, name="application_status", values_callable=lambda cls: [e.value for e in cls]),
        nullable=False,
        default=ApplicationStatus.NOT_APPLIED,
        server_default=ApplicationStatus.NOT_APPLIED.value,
    )
    applied_at = Column(DateTime, nullable=True)
    status_updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    notes = Column(Text, nullable=True)

    job_posting = relationship("JobPosting")

    def __repr__(self):
        return (
            f"<JobApplication(job_posting_id={self.job_posting_id}, "
            f"status={self.status})>"
        )


# ----------------------------------------------------------------------
# Demo mode sponsorship aggregates
# ----------------------------------------------------------------------
class SponsorFiscalYearAggregate(Base):
    """
    One row per (employer, fiscal year), used only in demo mode (see
    huntloop.demo_mode, scripts/build_demo_dataset.py) in place of the
    raw lca_disclosures rows a demo deployment never copies. Carries the
    per-year numbers a future multi-year sponsorship trend feature would
    need: total filings, distinct job titles, and median annual wage for
    that year. Empty in a non-demo database.
    """
    __tablename__ = "sponsor_fiscal_year_aggregates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    employer_name_normalized = Column(String(255), nullable=False, index=True)
    fiscal_year = Column(Integer, nullable=False)
    total_filings = Column(Integer, nullable=False)
    distinct_titles = Column(Integer, nullable=False)
    median_wage = Column(Numeric, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "employer_name_normalized", "fiscal_year", name="sponsor_fiscal_year_aggregates_employer_year_key"
        ),
    )

    def __repr__(self):
        return (
            f"<SponsorFiscalYearAggregate(employer={self.employer_name_normalized}, "
            f"fiscal_year={self.fiscal_year})>"
        )


class SponsorOverallAggregate(Base):
    """
    One row per employer, used only in demo mode in place of
    huntloop.api.sponsor_summary.get_sponsorship_summary()'s live
    lca_disclosures query. Holds exactly the fields that function
    returns, precomputed at demo-dataset build time with the same
    aggregation logic run read-only against production, so the numbers
    a demo deployment shows match production. Empty in a non-demo
    database.
    """
    __tablename__ = "sponsor_overall_aggregates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    employer_name_normalized = Column(String(255), nullable=False, unique=True, index=True)
    most_recent_fiscal_year = Column(Integer, nullable=False)
    total_lcas_most_recent_fiscal_year = Column(Integer, nullable=False)
    median_wage = Column(Numeric, nullable=True)
    most_frequent_job_title = Column(String(300), nullable=True)
    latest_case_status = Column(String(50), nullable=True)

    def __repr__(self):
        return f"<SponsorOverallAggregate(employer={self.employer_name_normalized})>"


# ----------------------------------------------------------------------
# Demo mode snapshot metadata
# ----------------------------------------------------------------------
class DemoMeta(Base):
    """
    A single row describing the demo dataset currently loaded, read by
    GET /demo-info (only mounted when DEMO_MODE is on, see
    huntloop.api.routers.demo_info) for the frontend banner. Written
    once by scripts/build_demo_dataset.py. Empty in a non-demo database.
    """
    __tablename__ = "demo_meta"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(Date, nullable=False)
    built_at = Column(DateTime, server_default=func.now())

    def __repr__(self):
        return f"<DemoMeta(snapshot_date={self.snapshot_date})>"


# ----------------------------------------------------------------------
# Feedback capture + triage
# ----------------------------------------------------------------------
class FeedbackCategory(str, enum.Enum):
    """Fixed small set a submitter must pick from - enforced at the API
    layer (POST /feedback rejects anything else with a 400), not just by
    this column's DB-level check."""

    BUG = "bug"
    FEATURE = "feature"
    QUESTION = "question"
    OTHER = "other"


class FeedbackTriageStatus(str, enum.Enum):
    """Whether the async LLM triage pass (scripts/triage_feedback.py) has
    run on this row yet - distinct from `status` below, which tracks
    human review/resolution, not the LLM pass."""

    PENDING = "pending"
    DONE = "done"
    SKIPPED_BUDGET = "skipped_budget"


class FeedbackStatus(str, enum.Enum):
    """Human-facing lifecycle, set via scripts/review_feedback.py (there
    is no admin web UI yet - see CLAUDE.md)."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    WONT_FIX = "wont_fix"


class Feedback(Base):
    """
    A public visitor's feedback report, captured immediately by POST
    /feedback (huntloop.api.routers.feedback) and triaged asynchronously
    by scripts/triage_feedback.py - see that endpoint's and that
    script's docstrings for the full design.

    `user_id` is nullable with no FK constraint: there is no users table
    in this project yet, so this column exists only so a future auth
    migration can backfill it without a schema change - nothing writes
    to it today.
    """
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True)
    category = Column(
        Enum(FeedbackCategory, name="feedback_category", values_callable=lambda cls: [e.value for e in cls]),
        nullable=False,
    )
    raw_text = Column(Text, nullable=False)
    # What the frontend sent at submission time - current page path,
    # active filter/query state (if given), and any recent client-side
    # errors it chose to attach. Server-populated per request, not
    # client-trusted-as-is beyond being valid JSON - see the router.
    context = Column(JSON, nullable=True)
    llm_summary = Column(Text, nullable=True)
    triage_status = Column(
        Enum(FeedbackTriageStatus, name="feedback_triage_status", values_callable=lambda cls: [e.value for e in cls]),
        nullable=False,
        default=FeedbackTriageStatus.PENDING,
        server_default=FeedbackTriageStatus.PENDING.value,
    )
    status = Column(
        Enum(FeedbackStatus, name="feedback_status", values_callable=lambda cls: [e.value for e in cls]),
        nullable=False,
        default=FeedbackStatus.OPEN,
        server_default=FeedbackStatus.OPEN.value,
    )
    is_public = Column(Boolean, nullable=False, default=False, server_default="false")
    # A hash (never the raw IP) of the submitter's IP - used only for
    # rate limiting (see huntloop.api.routers.feedback._check_rate_limit).
    ip_hash = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<Feedback(id={self.id}, category={self.category}, status={self.status})>"


class CompanyResearch(Base):
    """
    One company-level research snapshot, fetched via Tavily's search API
    (see huntloop.company_research, scripts/backfill_company_research.py -
    an independent integration, unrelated to the feedback/triage pipeline
    above, sharing no code with it). Enrichment is deliberately keyed on
    `company_id`, not `job_posting_id` - recent news/funding/hiring
    signals are a property of a company, not of an individual job
    listing, and many postings share one company, so fetching per-posting
    would multiply external API calls for no benefit.

    One row per company (`company_id` unique) - a refresh REPLACES the
    existing row rather than adding a new one, same "upsert, not a
    history table" shape as `job_applications`. A separate table (rather
    than new nullable columns on `companies`) was chosen because this is
    a distinct, independently-refreshed concern with its own lifecycle
    (fetched_at/staleness), matching this project's existing convention
    of a separate table for that shape of concern (`feedback`,
    `resume_versions`) rather than bolting an unrelated field set onto a
    core identity table.
    """
    __tablename__ = "company_research"

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, unique=True)
    summary = Column(Text, nullable=True)
    # List of {title, url, content, score} dicts - Tavily's own search
    # result shape for the single query this fetch makes (see
    # huntloop.company_research). Tavily does not return a reliable
    # per-result published-date field on this account, so none is stored.
    recent_news = Column(JSON, nullable=True)
    # Short heuristic extracts from the same single search response (see
    # huntloop.company_research._extract_signal) - not a second Tavily
    # call, to keep this at one credit per company given the tight
    # monthly credit budget. null when no funding/hiring-related sentence
    # was found in the response.
    funding_signal = Column(Text, nullable=True)
    hiring_signal = Column(Text, nullable=True)
    fetched_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    company = relationship("Company", backref="research")

    def __repr__(self):
        return f"<CompanyResearch(company_id={self.company_id}, fetched_at={self.fetched_at})>"


class TavilyUsage(Base):
    """
    A durable monthly request-count ledger for huntloop.company_research /
    scripts/backfill_company_research.py - NOT an in-memory counter, since
    the whole point is that it must survive the script being re-run or
    the process restarting (Tavily's free/paid tier resets credits on a
    CALENDAR MONTH, not a rolling window, and the account's hard cap is
    1000 credits/month - see CLAUDE.md). One row per calendar month
    (`month`, e.g. "2026-10"), incremented by exactly 1 immediately after
    each real Tavily HTTP call that gets a response (whether or not that
    response was usable) - a request that errors before any response
    (timeout/connection failure) is not counted, since Tavily charges no
    credit for a request it never received a chance to serve. Tavily's
    `/search` response does not include a per-call credit-cost field on
    this account (confirmed by inspecting a real response before writing
    this), so this counter IS the running credit count - there is nothing
    further to reconcile it against.
    """
    __tablename__ = "tavily_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    month = Column(String(7), nullable=False, unique=True)
    requests_used = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<TavilyUsage(month={self.month}, requests_used={self.requests_used})>"


class ModalUsage(Base):
    """
    A durable monthly invocation-count ledger for
    huntloop.modal_resume_processing (resume parsing + embedding
    generation moved to a Modal function - see CLAUDE.md), modeled
    directly on TavilyUsage above. One row per calendar month (`month`,
    e.g. "2026-10"), incremented by exactly 1 for each Modal invocation
    this process reserves (see
    huntloop.modal_resume_processing._reserve_invocation_slot) - counting
    INVOCATIONS, not a provider-reported credit, since Modal has no
    per-call cost field to reconcile against the way Tavily's response
    doesn't either, and resume parsing+embedding is a short, predictable
    operation where invocation count is a reasonable cost proxy.

    Unlike TavilyUsage (read/incremented only by a single serial backfill
    script, with no locking), this ledger is read/incremented from live,
    potentially-concurrent HTTP requests (POST /resumes/upload, PATCH
    /resumes/{id}/activate) - so the check-then-increment here is done
    under a `SELECT ... FOR UPDATE` row lock, not a bare read then write.
    """
    __tablename__ = "modal_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    month = Column(String(7), nullable=False, unique=True)
    invocations_used = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<ModalUsage(month={self.month}, invocations_used={self.invocations_used})>"


class ResumeAtsReport(Base):
    """
    A cache row for huntloop.resume_ats_report / GET
    /resumes/active/ats-report - one LLM-generated ATS-compatibility
    report per resume_versions.id, computed once and served from cache
    on every later request (see that router's docstring). `score` is a
    0-100 integer; `keyword_feedback`/`wording_feedback`/
    `formatting_feedback` are each a JSON list of short, concrete
    strings - kept as three separate typed columns rather than one
    blob, the same convention job_postings.matched_skills/missing_skills
    already uses for this project's other LLM-derived JSON fields.

    This is a brand-new, purely additive table with its own unique
    constraint on resume_version_id - nothing here reads from or writes
    to matched_skills/missing_skills, resume_versions.is_active, or any
    other column the skill-gap backfill job or resumes.activate() touch.
    """
    __tablename__ = "resume_ats_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    resume_version_id = Column(Integer, ForeignKey("resume_versions.id"), nullable=False, unique=True)
    score = Column(Integer, nullable=False)
    keyword_feedback = Column(JSON, nullable=False)
    wording_feedback = Column(JSON, nullable=False)
    formatting_feedback = Column(JSON, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self):
        return f"<ResumeAtsReport(resume_version_id={self.resume_version_id}, score={self.score})>"
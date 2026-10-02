/**
 * TypeScript types mirroring the backend's real Pydantic response
 * schemas (src/huntloop/api/schemas/jobs.py) and enum
 * (src/huntloop/db_models.ApplicationStatus). Kept hand-in-sync with
 * the backend for now - there's no shared schema/codegen step yet (see
 * SESSIONS.md for what's still deferred).
 */

export type ApplicationStatus =
  | "not_applied"
  | "applied"
  | "interviewing"
  | "rejected"
  | "offer";

export interface HealthResponse {
  status: string;
}

export interface SalaryEstimate {
  amount: number;
  basis: string;
}

export interface JobSummary {
  id: number;
  job_title: string;
  company_name: string;
  job_url: string;
  department: string | null;
  // Canonical category the raw department is mapped onto (see
  // huntloop.department_categorization) - one of ~18 controlled values
  // or "Other". null when there's no raw department or it isn't
  // categorized yet.
  department_category: string | null;
  employment_type: string | null;
  date_posted: string | null;
  // Composite match score in [0, 1] against the active resume (see
  // huntloop.match_scoring): a calibrated blend of embedding similarity
  // and, when available, the matched/missing skills ratio. null with no
  // active resume.
  match_score: number | null;
  // "full" = match_score is the full composite; "partial" = the
  // embedding-only fallback (no skills analysis yet), shown with a
  // "score provisional" marker; null = no score at all.
  score_basis: "full" | "partial" | null;
  matched_skills: string[] | null;
  missing_skills: string[] | null;
  locations: string[];
  application_status: ApplicationStatus;
  // Free-text notes the user saved for this application (job_applications.notes).
  // null when there's no application row or no note.
  application_notes: string | null;
  // When the application row was last touched. A single timestamp, not a
  // history of past statuses. null when there's no application row.
  status_updated_at: string | null;
  has_sponsor_history: boolean;
  // Employer-level estimate from DOL wage filings (see the detail page /
  // filter panel labeling) - never a real posted salary. null when the
  // company has no resolved sponsor match or no annual-wage filings.
  salary_estimate: SalaryEstimate | null;
}

export interface SponsorSummary {
  matched_employer_name: string;
  most_recent_fiscal_year: number;
  total_lcas_most_recent_fiscal_year: number;
  median_wage: number | null;
  most_frequent_job_title: string | null;
  latest_case_status: string | null;
}

// Three honest states for a job's company, distinct from the bare
// has_sponsor_history boolean - mirrors
// huntloop.api.schemas.jobs.JobDetail.sponsor_check_status. "not_checked"
// must never be presented as "no sponsor history" - most companies (734
// of 743 as of this field's introduction) fall into that state simply
// because scripts/resolve_sponsor_matches.py hasn't been (re-)run against
// them since they were onboarded, not because they were checked and found
// wanting.
export type SponsorCheckStatus = "confirmed" | "checked_no_match" | "not_checked";

export interface JobDetail extends JobSummary {
  job_description: string | null;
  ats_platform: string | null;
  sponsor: SponsorSummary | null;
  sponsor_check_status: SponsorCheckStatus;
  // salary_estimate is inherited from JobSummary.
}

export interface JobListResponse {
  items: JobSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApplicationStatusUpdate {
  status?: ApplicationStatus;
  notes?: string | null;
}

export interface ApplicationStatusResponse {
  job_posting_id: number;
  status: ApplicationStatus;
  applied_at: string | null;
  status_updated_at: string;
  notes: string | null;
  // True only when the API is running with DEMO_MODE on - this response
  // reflects what would have been written, but nothing was persisted.
  demo?: boolean;
}

export interface ApplicationStatusCounts {
  not_applied: number;
  applied: number;
  interviewing: number;
  rejected: number;
  offer: number;
}

export interface DashboardStats {
  total_jobs: number;
  total_companies: number;
  applications_by_status: ApplicationStatusCounts;
  new_jobs_last_7_days: number;
}

// Sentinel value for GET /jobs' `department` query param, meaning
// "postings with no department set" - mirrors
// huntloop.api.routers.jobs.UNSPECIFIED_DEPARTMENT.
export const UNSPECIFIED_DEPARTMENT = "__unspecified__";

// Same idea for the `employment_type` query param - mirrors
// huntloop.api.routers.jobs.UNSPECIFIED_EMPLOYMENT_TYPE. Same literal
// value as UNSPECIFIED_DEPARTMENT (both are "__unspecified__" on the
// backend), kept as a separate exported constant so each filter reads
// its own name at the call site rather than sharing one generic sentinel.
export const UNSPECIFIED_EMPLOYMENT_TYPE = "__unspecified__";

// Same idea for the `location` query param - mirrors
// huntloop.api.routers.jobs.UNSPECIFIED_LOCATION. Selecting it filters
// down to postings with no location scraped at all. The `location`
// param is multi-value (repeated params, OR'd together); a real value
// is an EXACT match on a canonical group from GET /jobs/locations, and
// is never radius/geocoding search.
export const UNSPECIFIED_LOCATION = "__unspecified__";

// One country's canonical location groups from GET /jobs/locations.
// Countries come back alphabetical ("Other" last); `locations` is
// alphabetized within each country. Mirrors
// huntloop.api.schemas.jobs.LocationGroup.
export interface LocationGroup {
  country: string;
  locations: string[];
}

export interface ResumeVersionSummary {
  id: number;
  version_number: number;
  uploaded_at: string;
  is_active: boolean;
  text_preview: string;
}

// POST /jobs/{id}/draft-answer (huntloop.api.routers.drafting) - slice 2
// of the job-detail chat assistant. BYOK: api_key is the user's OWN
// third-party provider key, sent per-request, never stored by this app
// (not in this type, not anywhere server-side - see CLAUDE.md's TLS
// warning on this endpoint before ever pointing this at a non-localhost
// API).
export type DraftAnswerProvider = "groq" | "gemini";

export interface DraftAnswerRequest {
  prompt: string;
  provider: DraftAnswerProvider;
  api_key: string;
}

export interface DraftAnswerResponse {
  answer: string;
  provider: DraftAnswerProvider;
}

// GET /demo-info (huntloop.api.routers.demo_info) - only exists when the
// API is running with DEMO_MODE on. snapshot_date is null if the demo
// database has no demo_meta row at all.
export interface DemoInfoResponse {
  snapshot_date: string | null;
  message: string;
}

// POST /feedback (huntloop.api.routers.feedback) - public, no auth.
// category is validated server-side against the fixed set; sent here as
// a plain string (not a union) since an invalid value is a 400 from the
// API, not something the type system needs to pre-empt.
export type FeedbackCategory = "bug" | "feature" | "question" | "other";

export interface FeedbackSubmitRequest {
  category: FeedbackCategory;
  description: string;
  page_path?: string;
  filters?: Record<string, unknown>;
  recent_errors?: string[];
}

export interface FeedbackSubmitResponse {
  id: number;
  triage_status: string;
  status: string;
  is_public: boolean;
}

// GET /feedback/public - deliberately narrow: never raw_text. Status
// values mirror huntloop.db_models.FeedbackStatus.
export type FeedbackStatus = "open" | "in_progress" | "resolved" | "wont_fix";

export interface PublicFeedbackItem {
  category: FeedbackCategory;
  llm_summary: string | null;
  status: FeedbackStatus;
  created_at: string;
}

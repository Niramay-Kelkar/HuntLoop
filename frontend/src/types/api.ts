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

export interface JobSummary {
  id: number;
  job_title: string;
  company_name: string;
  job_url: string;
  department: string | null;
  date_posted: string | null;
  match_score: number | null;
  matched_skills: string[] | null;
  missing_skills: string[] | null;
  locations: string[];
  application_status: ApplicationStatus;
  has_sponsor_history: boolean;
}

export interface SponsorSummary {
  matched_employer_name: string;
  most_recent_fiscal_year: number;
  total_lcas_most_recent_fiscal_year: number;
  median_wage: number | null;
  most_frequent_job_title: string | null;
  latest_case_status: string | null;
}

export interface SalaryEstimate {
  amount: number;
  basis: string;
}

export interface JobDetail extends JobSummary {
  job_description: string | null;
  ats_platform: string | null;
  sponsor: SponsorSummary | null;
  salary_estimate: SalaryEstimate | null;
}

export interface JobListResponse {
  items: JobSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApplicationStatusUpdate {
  status: ApplicationStatus;
  notes?: string | null;
}

export interface ApplicationStatusResponse {
  job_posting_id: number;
  status: ApplicationStatus;
  applied_at: string | null;
  status_updated_at: string;
  notes: string | null;
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

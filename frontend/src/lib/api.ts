/**
 * Minimal fetch-based client for the real HuntLoop API
 * (src/huntloop/api/, GET /health, GET /jobs, GET /jobs/{id},
 * PATCH /jobs/{id}/application). Not mocked anywhere - every function
 * here hits the actual running API service.
 *
 * Base URL comes from NEXT_PUBLIC_API_URL (must be NEXT_PUBLIC_-prefixed
 * to reach the browser, since these calls run client-side via
 * TanStack Query - see src/app/providers.tsx), defaulting to
 * http://localhost:8000 to match both `uvicorn`'s default local port and
 * docker-compose's `api` service's published port.
 */
import type {
  ApplicationStatusResponse,
  ApplicationStatusUpdate,
  DashboardStats,
  HealthResponse,
  JobDetail,
  JobListResponse,
  ResumeVersionSummary,
} from "@/types/api";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// How long any single request is allowed to hang before we give up and
// surface an error, rather than leaving a view stuck on its loading
// skeleton indefinitely when the API is unreachable or wedged.
const REQUEST_TIMEOUT_MS = 20_000;
const UPLOAD_TIMEOUT_MS = 60_000;

/**
 * Every failed API call rejects with one of these, so UI can tell a
 * "this resource doesn't exist" (404) apart from "the server is down or
 * erroring" (5xx / status null) and word the message accordingly.
 * `message` is kept short and human-ish; the raw response body lives in
 * `detail` for logging, never for dumping onto the screen.
 */
export class ApiError extends Error {
  readonly status: number | null;
  readonly detail: string;

  constructor(message: string, status: number | null, detail = "") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      ...init,
    });
  } catch {
    throw new ApiError("Could not reach the server.", null);
  }
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new ApiError(
      `${init?.method ?? "GET"} ${path} failed with ${response.status}`,
      response.status,
      body,
    );
  }
  return response.json() as Promise<T>;
}

export function getHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health");
}

// Matches GET /jobs' real query params (huntloop.api.routers.jobs) -
// not used by the minimal connectivity page yet, set up for the real
// job-list UI this is scaffolding toward.
export interface ListJobsParams {
  company?: string;
  department?: string;
  employment_type?: string;
  location?: string;
  min_score?: number;
  salary_min?: number;
  salary_max?: number;
  salary_unspecified?: boolean;
  // When true, only postings the user has actively tracked (a
  // job_applications row with status other than not_applied) - the
  // applications tracker's data source.
  tracked?: boolean;
  // All descending. "-score" = best resume match first (default),
  // "-date" = most recently posted first, "-salary" = highest estimated
  // salary first. Each sorts postings missing that value last.
  sort?: "-score" | "-date" | "-salary";
  limit?: number;
  offset?: number;
}

export function getJobs(params: ListJobsParams = {}): Promise<JobListResponse> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.set(key, String(value));
  }
  const queryString = query.toString();
  return apiFetch<JobListResponse>(`/jobs${queryString ? `?${queryString}` : ""}`);
}

// The real, distinct department values currently in use (GET
// /jobs/departments) - used to populate the department filter's options
// from real data instead of a hardcoded list.
export function getDepartments(): Promise<string[]> {
  return apiFetch<string[]>("/jobs/departments");
}

// The real, distinct employment_type values currently in use (GET
// /jobs/employment-types) - same reasoning/pattern as getDepartments().
export function getEmploymentTypes(): Promise<string[]> {
  return apiFetch<string[]>("/jobs/employment-types");
}

// The real, distinct location values in common use (GET /jobs/locations)
// - same reasoning/pattern as getDepartments(). The backend caps this to
// locations appearing on many postings (job_locations.location_name is
// very messy free text), and the `location` filter matches as a
// substring, so these are options, not an exhaustive exact list.
export function getLocations(): Promise<string[]> {
  return apiFetch<string[]>("/jobs/locations");
}

export function getJob(id: number): Promise<JobDetail> {
  return apiFetch<JobDetail>(`/jobs/${id}`);
}

export function getDashboardStats(): Promise<DashboardStats> {
  return apiFetch<DashboardStats>("/dashboard/stats");
}

export function updateApplicationStatus(
  id: number,
  payload: ApplicationStatusUpdate,
): Promise<ApplicationStatusResponse> {
  return apiFetch<ApplicationStatusResponse>(`/jobs/${id}/application`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function getResumes(): Promise<ResumeVersionSummary[]> {
  return apiFetch<ResumeVersionSummary[]>("/resumes");
}

// Real multipart upload (POST /resumes/upload) - deliberately bypasses
// apiFetch, which always sends Content-Type: application/json. A
// multipart/form-data request needs the browser-generated boundary in
// its own Content-Type header (set automatically when the body is a
// FormData and no Content-Type is specified manually), so this can't
// share that helper.
export async function uploadResume(file: File): Promise<ResumeVersionSummary> {
  const formData = new FormData();
  formData.append("file", file);
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/resumes/upload`, {
      method: "POST",
      body: formData,
      signal: AbortSignal.timeout(UPLOAD_TIMEOUT_MS),
    });
  } catch {
    throw new ApiError("Could not reach the server.", null);
  }
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new ApiError(`POST /resumes/upload failed with ${response.status}`, response.status, body);
  }
  return response.json() as Promise<ResumeVersionSummary>;
}

export function activateResume(id: number): Promise<ResumeVersionSummary> {
  return apiFetch<ResumeVersionSummary>(`/resumes/${id}/activate`, { method: "PATCH" });
}

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
  HealthResponse,
  JobDetail,
  JobListResponse,
} from "@/types/api";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${init?.method ?? "GET"} ${path} failed: ${response.status} ${body}`);
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
  min_score?: number;
  sort?: "score" | "-score";
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

export function getJob(id: number): Promise<JobDetail> {
  return apiFetch<JobDetail>(`/jobs/${id}`);
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

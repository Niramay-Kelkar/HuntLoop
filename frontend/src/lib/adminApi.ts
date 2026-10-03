/**
 * Fetch client for the Clerk-protected admin feedback-review API
 * (GET/PATCH /admin/feedback*, huntloop.api.routers.admin_feedback).
 *
 * A separate module from lib/api.ts, not a few extra functions bolted
 * onto apiFetch() there: every call here needs an `Authorization:
 * Bearer <clerk session token>` header, which apiFetch's fixed
 * Content-Type-only header object doesn't carry (same reasoning
 * uploadResume() in lib/api.ts already uses to justify bypassing
 * apiFetch for multipart requests). The token itself comes from
 * Clerk's own useAuth().getToken() in the component calling these -
 * this module never touches Clerk directly, it just takes the token as
 * a plain string argument.
 *
 * huntloop.api.admin_auth verifies that token server-side on every
 * call - a 401 here means "no/invalid Clerk session", a 403 means "a
 * real Clerk session, but this account's email isn't on
 * ADMIN_ALLOWED_EMAILS". Both surface as a normal ApiError via the
 * shared status/detail shape, for the admin page to show.
 */
import { API_BASE_URL, ApiError } from "@/lib/api";
import type { AdminFeedbackItem, AdminFeedbackUpdateRequest } from "@/types/api";

const REQUEST_TIMEOUT_MS = 20_000;

async function adminFetch<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
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

export function getAdminFeedback(token: string): Promise<AdminFeedbackItem[]> {
  return adminFetch<AdminFeedbackItem[]>("/admin/feedback", token);
}

export function updateAdminFeedback(
  token: string,
  id: number,
  payload: AdminFeedbackUpdateRequest,
): Promise<AdminFeedbackItem> {
  return adminFetch<AdminFeedbackItem>(`/admin/feedback/${id}`, token, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useApplicationStatusMutation } from "./useApplicationStatus";
import { ToastProvider } from "@/components/Toast";
import * as api from "@/lib/api";
import type { JobListResponse, JobSummary } from "@/types/api";

/**
 * This hook's whole reason to exist is the optimistic-update/rollback
 * behavior around PATCH /jobs/{id}/application - a plain mutation wrapper
 * with no rollback would be low-value to test, but the actual cache
 * surgery in onMutate/onError is real logic worth pinning down.
 */
function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: 1,
    job_title: "Software Engineer",
    company_name: "Checkr",
    job_url: "https://example.com/job/1",
    department: null,
    employment_type: null,
    date_posted: null,
    match_score: 0.5,
    matched_skills: null,
    missing_skills: null,
    locations: [],
    application_status: "not_applied",
    has_sponsor_history: false,
    ...overrides,
  };
}

function renderMutationHook(queryClient: QueryClient) {
  return renderHook(() => useApplicationStatusMutation(), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>
        <ToastProvider>{children}</ToastProvider>
      </QueryClientProvider>
    ),
  });
}

describe("useApplicationStatusMutation", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("optimistically updates every cached jobs query before the request resolves", async () => {
    const queryClient = new QueryClient();
    const listResponse: JobListResponse = { items: [makeJob()], total: 1, limit: 20, offset: 0 };
    queryClient.setQueryData(["jobs", { company: "" }], listResponse);

    let resolvePatch!: (value: Awaited<ReturnType<typeof api.updateApplicationStatus>>) => void;
    vi.spyOn(api, "updateApplicationStatus").mockReturnValue(
      new Promise((resolve) => {
        resolvePatch = resolve;
      }),
    );

    const { result } = renderMutationHook(queryClient);

    act(() => {
      result.current.mutate({ jobId: 1, status: "applied" });
    });

    // Before the (still-pending) request resolves, the cache should already
    // reflect the optimistic status.
    await waitFor(() => {
      const cached = queryClient.getQueryData<JobListResponse>(["jobs", { company: "" }]);
      expect(cached?.items[0].application_status).toBe("applied");
    });

    resolvePatch({
      job_posting_id: 1,
      status: "applied",
      applied_at: null,
      status_updated_at: "now",
      notes: null,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });

  it("rolls back the optimistic update when the request fails", async () => {
    const queryClient = new QueryClient();
    const listResponse: JobListResponse = { items: [makeJob()], total: 1, limit: 20, offset: 0 };
    queryClient.setQueryData(["jobs", { company: "" }], listResponse);

    vi.spyOn(api, "updateApplicationStatus").mockRejectedValue(new Error("network error"));

    const { result } = renderMutationHook(queryClient);

    act(() => {
      result.current.mutate({ jobId: 1, status: "applied" });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));

    const cached = queryClient.getQueryData<JobListResponse>(["jobs", { company: "" }]);
    expect(cached?.items[0].application_status).toBe("not_applied");
  });
});

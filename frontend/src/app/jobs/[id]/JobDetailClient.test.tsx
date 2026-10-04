import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "@/lib/api";
import { ToastProvider } from "@/components/Toast";
import { JobDetailClient } from "./JobDetailClient";
import type { JobDetail } from "@/types/api";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getJob: vi.fn(),
}));

function makeDetail(overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    id: 1,
    job_title: "Software Engineer",
    company_name: "Palantir",
    company_display_name: null,
    job_url: "https://example.com/job/1",
    department: null,
    department_category: null,
    employment_type: null,
    date_posted: null,
    match_score: 0.5,
    score_basis: "partial",
    matched_skills: null,
    missing_skills: null,
    locations: ["New York, NY"],
    application_status: "not_applied",
    application_notes: null,
    status_updated_at: null,
    has_sponsor_history: false,
    salary_estimate: null,
    job_description: null,
    ats_platform: null,
    sponsor: null,
    company_research: null,
    sponsor_check_status: "not_checked",
    ...overrides,
  };
}

function renderDetail(detail: JobDetail) {
  vi.mocked(api.getJob).mockResolvedValue(detail);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider>
        <JobDetailClient jobId={detail.id} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("JobDetailClient header meta", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows the employment type badge in the header when the posting has one", async () => {
    renderDetail(makeDetail({ employment_type: "Internship" }));
    expect(await screen.findByText("Internship")).toBeInTheDocument();
  });

  it("omits the employment type badge when the posting has none", async () => {
    renderDetail(makeDetail({ employment_type: null, job_title: "Backend Engineer" }));
    // wait for the detail to render before asserting absence
    expect(await screen.findByRole("heading", { name: "Backend Engineer" })).toBeInTheDocument();
    expect(screen.queryByText("Internship")).not.toBeInTheDocument();
    expect(screen.queryByText("N/A")).not.toBeInTheDocument();
  });
});

describe("JobDetailClient in demo mode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "true");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("does not render the AI assistant panel", async () => {
    renderDetail(makeDetail({ job_title: "Backend Engineer" }));
    expect(await screen.findByRole("heading", { name: "Backend Engineer" })).toBeInTheDocument();
    expect(screen.queryByText("Ask about this job")).not.toBeInTheDocument();
    expect(screen.queryByText("Draft an application answer")).not.toBeInTheDocument();
  });
});

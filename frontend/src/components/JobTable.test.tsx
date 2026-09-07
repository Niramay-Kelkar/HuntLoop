import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "./Toast";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { JobTable } from "./JobTable";
import type { JobSummary } from "@/types/api";

function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: 1,
    job_title: "Software Engineer",
    company_name: "Palantir",
    job_url: "https://example.com/job/1",
    department: null,
    department_category: null,
    employment_type: null,
    date_posted: null,
    match_score: 0.5,
    matched_skills: null,
    missing_skills: null,
    locations: ["New York, NY"],
    application_status: "not_applied",
    application_notes: null,
    status_updated_at: null,
    has_sponsor_history: true,
    salary_estimate: null,
    ...overrides,
  };
}

function renderTable(jobs: JobSummary[]) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider>
        <JobTable jobs={jobs} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("JobTable scannable columns", () => {
  it("has an estimated-salary column with the estimate caveat spelled out", () => {
    renderTable([makeJob()]);
    expect(screen.getByText("Est. salary")).toBeInTheDocument();
    expect(
      screen.getByText(
        /employer-level estimate from DOL filings, not a posted salary/,
      ),
    ).toBeInTheDocument();
  });

  it("renders the salary estimate as a '~' figure with the basis tooltip, or a dash when absent", () => {
    renderTable([
      makeJob({
        id: 1,
        salary_estimate: {
          amount: 150000,
          basis:
            "Estimated from DOL wage filings for this employer, not job-specific",
        },
      }),
      makeJob({ id: 2, salary_estimate: null }),
    ]);
    const figure = screen.getByText("~$150k");
    expect(figure).toHaveAttribute(
      "title",
      expect.stringContaining("not job-specific"),
    );
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("appends the employment type to the company line when present", () => {
    renderTable([makeJob({ employment_type: "Full-time" })]);
    expect(screen.getByText(/palantir · Full-time/i)).toBeInTheDocument();
  });

  it("shows the sponsor badge per row", () => {
    renderTable([makeJob({ has_sponsor_history: true })]);
    expect(screen.getByText("Sponsors H-1B")).toBeInTheDocument();
  });
});

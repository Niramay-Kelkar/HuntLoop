import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "./Toast";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { JobCard } from "./JobCard";
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
    has_sponsor_history: false,
    salary_estimate: null,
    ...overrides,
  };
}

function renderCard(job: JobSummary) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider>
        <JobCard job={job} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("JobCard scannable meta", () => {
  it("shows the employment type when the posting has one", () => {
    renderCard(makeJob({ employment_type: "Internship" }));
    expect(screen.getByText("Internship")).toBeInTheDocument();
  });

  it("omits the employment type when the posting has none", () => {
    renderCard(makeJob({ employment_type: null }));
    expect(screen.queryByText("Internship")).not.toBeInTheDocument();
  });

  it("shows the salary estimate labeled as an estimate, with the basis as a tooltip", () => {
    renderCard(
      makeJob({
        salary_estimate: {
          amount: 150000,
          basis:
            "Estimated from DOL wage filings for this employer, not job-specific",
        },
      }),
    );
    const chip = screen.getByText(/Est\. ~\$150k/);
    expect(chip).toBeInTheDocument();
    expect(chip).toHaveAttribute(
      "title",
      expect.stringContaining("not job-specific"),
    );
  });

  it("shows no salary figure when there is no estimate", () => {
    renderCard(makeJob({ salary_estimate: null }));
    expect(screen.queryByText(/Est\. ~\$/)).not.toBeInTheDocument();
  });

  it("always shows the sponsor badge", () => {
    renderCard(makeJob({ has_sponsor_history: true }));
    expect(screen.getByText("Sponsors H-1B")).toBeInTheDocument();
  });
});

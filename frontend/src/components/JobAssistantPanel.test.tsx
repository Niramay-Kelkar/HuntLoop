import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { JobDetail } from "@/types/api";
import { JobAssistantPanel } from "./JobAssistantPanel";

function makeDetail(overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    id: 1,
    job_title: "Software Engineer",
    company_name: "Acme Corp",
    job_url: "https://example.com/job/1",
    department: null,
    department_category: null,
    employment_type: null,
    date_posted: null,
    match_score: 0.62,
    score_basis: "full",
    matched_skills: ["Python"],
    missing_skills: ["Go"],
    locations: [],
    application_status: "not_applied",
    application_notes: null,
    status_updated_at: null,
    has_sponsor_history: false,
    salary_estimate: null,
    job_description: null,
    ats_platform: null,
    sponsor: null,
    sponsor_check_status: "not_checked",
    ...overrides,
  };
}

describe("JobAssistantPanel", () => {
  it("renders all four question buttons", () => {
    render(<JobAssistantPanel detail={makeDetail()} />);
    expect(screen.getByText("Does this company sponsor visas?")).toBeInTheDocument();
    expect(screen.getByText("What's the salary estimate?")).toBeInTheDocument();
    expect(screen.getByText("What skills am I missing?")).toBeInTheDocument();
    expect(screen.getByText("What's my match score based on?")).toBeInTheDocument();
  });

  it("says skills analysis hasn't run yet when missing_skills is null (partial basis), not 'no gaps'", () => {
    render(
      <JobAssistantPanel
        detail={makeDetail({ missing_skills: null, matched_skills: null, score_basis: "partial" })}
      />,
    );
    fireEvent.click(screen.getByText("What skills am I missing?"));
    expect(screen.getByText(/hasn't run for this job yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/no skill gaps identified/i)).not.toBeInTheDocument();
  });

  it("distinguishes a genuine zero-gap result from a not-yet-run result", () => {
    render(<JobAssistantPanel detail={makeDetail({ missing_skills: [] })} />);
    fireEvent.click(screen.getByText("What skills am I missing?"));
    expect(screen.getByText(/no skill gaps identified/i)).toBeInTheDocument();
  });

  it("lists real missing skills when present", () => {
    render(<JobAssistantPanel detail={makeDetail({ missing_skills: ["Kubernetes", "Go"] })} />);
    fireEvent.click(screen.getByText("What skills am I missing?"));
    expect(screen.getByText(/Kubernetes, Go/)).toBeInTheDocument();
  });

  it("frames a partial score basis as provisional, not final", () => {
    render(<JobAssistantPanel detail={makeDetail({ score_basis: "partial", match_score: 0.4 })} />);
    fireEvent.click(screen.getByText("What's my match score based on?"));
    expect(screen.getByText(/provisional/i)).toBeInTheDocument();
    expect(screen.getByText(/hasn't run for this job yet/i)).toBeInTheDocument();
  });

  it("reports no active resume when match_score is null", () => {
    render(<JobAssistantPanel detail={makeDetail({ match_score: null, score_basis: null })} />);
    fireEvent.click(screen.getByText("What's my match score based on?"));
    expect(screen.getByText(/no active resume/i)).toBeInTheDocument();
  });

  it("says 'haven't checked' rather than 'no sponsor history' when sponsor_check_status is not_checked", () => {
    render(<JobAssistantPanel detail={makeDetail({ sponsor_check_status: "not_checked" })} />);
    fireEvent.click(screen.getByText("Does this company sponsor visas?"));
    expect(screen.getByText(/haven't checked sponsorship history/i)).toBeInTheDocument();
    expect(screen.queryByText(/found no sponsorship history on file/i)).not.toBeInTheDocument();
  });

  it("distinguishes checked-no-match from never-checked", () => {
    render(<JobAssistantPanel detail={makeDetail({ sponsor_check_status: "checked_no_match" })} />);
    fireEvent.click(screen.getByText("Does this company sponsor visas?"));
    expect(screen.getByText(/found no sponsorship history on file/i)).toBeInTheDocument();
    expect(screen.queryByText(/haven't checked/i)).not.toBeInTheDocument();
  });

  it("reports confirmed sponsor history with real numbers when resolved", () => {
    render(
      <JobAssistantPanel
        detail={makeDetail({
          sponsor_check_status: "confirmed",
          sponsor: {
            matched_employer_name: "ACME CORP",
            most_recent_fiscal_year: 2025,
            total_lcas_most_recent_fiscal_year: 12,
            median_wage: 150000,
            most_frequent_job_title: "Software Engineer",
            latest_case_status: "Certified",
          },
        })}
      />,
    );
    fireEvent.click(screen.getByText("Does this company sponsor visas?"));
    expect(screen.getByText(/Yes — Acme Corp has confirmed/i)).toBeInTheDocument();
    expect(screen.getByText(/12 LCA filing/i)).toBeInTheDocument();
  });

  it("gives an honest reason when there's no salary estimate", () => {
    render(<JobAssistantPanel detail={makeDetail({ salary_estimate: null })} />);
    fireEvent.click(screen.getByText("What's the salary estimate?"));
    expect(screen.getByText(/no resolved DOL sponsor match/i)).toBeInTheDocument();
  });

  it("shows a real salary estimate when present", () => {
    render(
      <JobAssistantPanel
        detail={makeDetail({ salary_estimate: { amount: 145000, basis: "Estimated from DOL wage filings" } })}
      />,
    );
    fireEvent.click(screen.getByText("What's the salary estimate?"));
    expect(screen.getByText(/\$145k/)).toBeInTheDocument();
  });

  it("accumulates multiple asked questions in a visible history", () => {
    render(<JobAssistantPanel detail={makeDetail()} />);
    fireEvent.click(screen.getByText("What's the salary estimate?"));
    fireEvent.click(screen.getByText("Does this company sponsor visas?"));
    expect(screen.getAllByText("What's the salary estimate?").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Does this company sponsor visas?").length).toBeGreaterThanOrEqual(1);
  });
});

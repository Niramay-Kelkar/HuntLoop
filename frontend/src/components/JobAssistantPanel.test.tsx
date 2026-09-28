import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "@/lib/api";
import { writeDraftSettings } from "@/lib/draftSettings";
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

function renderOpen(detail: JobDetail) {
  render(<JobAssistantPanel detail={detail} />);
  fireEvent.click(screen.getByRole("button", { name: /open job assistant/i }));
}

describe("JobAssistantPanel", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });
  afterEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    vi.restoreAllMocks();
  });

  it("renders as a closed floating launcher with no panel visible", () => {
    render(<JobAssistantPanel detail={makeDetail()} />);
    expect(screen.getByRole("button", { name: /open job assistant/i })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: /job assistant/i })).not.toBeInTheDocument();
    expect(screen.queryByText("Does this company sponsor visas?")).not.toBeInTheDocument();
  });

  it("opens the drawer with both FAQ buttons and the drafting form on launcher click", () => {
    renderOpen(makeDetail());
    expect(screen.getByRole("dialog", { name: /job assistant/i })).toBeInTheDocument();
    expect(screen.getByText("Does this company sponsor visas?")).toBeInTheDocument();
    expect(screen.getByText("What's the salary estimate?")).toBeInTheDocument();
    expect(screen.getByText("What skills am I missing?")).toBeInTheDocument();
    expect(screen.getByText("What's my match score based on?")).toBeInTheDocument();
    expect(screen.getByText("Draft an application answer")).toBeInTheDocument();
  });

  it("closes the drawer when the launcher is clicked again", () => {
    renderOpen(makeDetail());
    expect(screen.getByRole("dialog", { name: /job assistant/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /close job assistant/i }));
    expect(screen.queryByRole("dialog", { name: /job assistant/i })).not.toBeInTheDocument();
  });

  it("says skills analysis hasn't run yet when missing_skills is null (partial basis), not 'no gaps'", () => {
    renderOpen(makeDetail({ missing_skills: null, matched_skills: null, score_basis: "partial" }));
    fireEvent.click(screen.getByText("What skills am I missing?"));
    expect(screen.getByText(/hasn't run for this job yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/no skill gaps identified/i)).not.toBeInTheDocument();
  });

  it("distinguishes a genuine zero-gap result from a not-yet-run result", () => {
    renderOpen(makeDetail({ missing_skills: [] }));
    fireEvent.click(screen.getByText("What skills am I missing?"));
    expect(screen.getByText(/no skill gaps identified/i)).toBeInTheDocument();
  });

  it("lists real missing skills when present", () => {
    renderOpen(makeDetail({ missing_skills: ["Kubernetes", "Go"] }));
    fireEvent.click(screen.getByText("What skills am I missing?"));
    expect(screen.getByText(/Kubernetes, Go/)).toBeInTheDocument();
  });

  it("frames a partial score basis as provisional, not final", () => {
    renderOpen(makeDetail({ score_basis: "partial", match_score: 0.4 }));
    fireEvent.click(screen.getByText("What's my match score based on?"));
    expect(screen.getByText(/provisional/i)).toBeInTheDocument();
    expect(screen.getByText(/hasn't run for this job yet/i)).toBeInTheDocument();
  });

  it("reports no active resume when match_score is null", () => {
    renderOpen(makeDetail({ match_score: null, score_basis: null }));
    fireEvent.click(screen.getByText("What's my match score based on?"));
    expect(screen.getByText(/no active resume/i)).toBeInTheDocument();
  });

  it("says 'haven't checked' rather than 'no sponsor history' when sponsor_check_status is not_checked", () => {
    renderOpen(makeDetail({ sponsor_check_status: "not_checked" }));
    fireEvent.click(screen.getByText("Does this company sponsor visas?"));
    expect(screen.getByText(/haven't checked sponsorship history/i)).toBeInTheDocument();
    expect(screen.queryByText(/found no sponsorship history on file/i)).not.toBeInTheDocument();
  });

  it("distinguishes checked-no-match from never-checked", () => {
    renderOpen(makeDetail({ sponsor_check_status: "checked_no_match" }));
    fireEvent.click(screen.getByText("Does this company sponsor visas?"));
    expect(screen.getByText(/found no sponsorship history on file/i)).toBeInTheDocument();
    expect(screen.queryByText(/haven't checked/i)).not.toBeInTheDocument();
  });

  it("reports confirmed sponsor history with real numbers when resolved", () => {
    renderOpen(
      makeDetail({
        sponsor_check_status: "confirmed",
        sponsor: {
          matched_employer_name: "ACME CORP",
          most_recent_fiscal_year: 2025,
          total_lcas_most_recent_fiscal_year: 12,
          median_wage: 150000,
          most_frequent_job_title: "Software Engineer",
          latest_case_status: "Certified",
        },
      }),
    );
    fireEvent.click(screen.getByText("Does this company sponsor visas?"));
    expect(screen.getByText(/Yes — Acme Corp has confirmed/i)).toBeInTheDocument();
    expect(screen.getByText(/12 LCA filing/i)).toBeInTheDocument();
  });

  it("gives an honest reason when there's no salary estimate", () => {
    renderOpen(makeDetail({ salary_estimate: null }));
    fireEvent.click(screen.getByText("What's the salary estimate?"));
    expect(screen.getByText(/no resolved DOL sponsor match/i)).toBeInTheDocument();
  });

  it("shows a real salary estimate when present", () => {
    renderOpen(makeDetail({ salary_estimate: { amount: 145000, basis: "Estimated from DOL wage filings" } }));
    fireEvent.click(screen.getByText("What's the salary estimate?"));
    expect(screen.getByText(/\$145k/)).toBeInTheDocument();
  });

  it("replaces the previous answer instead of stacking when a different question is asked", () => {
    renderOpen(makeDetail());
    fireEvent.click(screen.getByText("What's the salary estimate?"));
    expect(screen.getByText(/no salary estimate is available/i)).toBeInTheDocument();

    fireEvent.click(screen.getByText("Does this company sponsor visas?"));
    expect(screen.getByText(/haven't checked sponsorship history/i)).toBeInTheDocument();
    // The previous answer's content is gone, not appended below the new one.
    expect(screen.queryByText(/no salary estimate is available/i)).not.toBeInTheDocument();
    // Only one answer block is rendered at a time.
    expect(screen.getAllByText(/haven't checked sponsorship history/i)).toHaveLength(1);
  });

  describe("drafting section - error messages", () => {
    async function submitDraftRequest() {
      fireEvent.change(screen.getByPlaceholderText(/Groq API key|Gemini API key/i), {
        target: { value: "some-key" },
      });
      fireEvent.change(screen.getByPlaceholderText(/why-this-company/i), {
        target: { value: "Draft a why-this-company answer." },
      });
      fireEvent.click(screen.getByRole("button", { name: /draft answer/i }));
      await waitFor(() => expect(screen.getByText(/rejected as invalid|didn't respond|rate limit|invalid/i)).toBeInTheDocument());
    }

    it("shows a plain-language message for a 401, not the raw provider error, with details collapsed by default", async () => {
      const rawGroqError =
        "Error code: 401 - {'error': {'message': 'Invalid API Key', 'type': 'invalid_request_error', 'code': 'invalid_api_key'}}";
      vi.spyOn(api, "draftAnswer").mockRejectedValue(
        new api.ApiError(
          "POST failed with 401",
          401,
          JSON.stringify({ detail: `The provider rejected this API key: ${rawGroqError}` }),
        ),
      );

      renderOpen(makeDetail());
      await submitDraftRequest();

      expect(screen.getByText(/your api key was rejected as invalid/i)).toBeInTheDocument();
      expect(screen.queryByText(new RegExp(rawGroqError.slice(0, 20)))).not.toBeInTheDocument();

      // Raw detail is still reachable, just collapsed until asked for.
      const toggle = screen.getByRole("button", { name: /show details/i });
      expect(screen.queryByText(/invalid_api_key/i)).not.toBeInTheDocument();
      fireEvent.click(toggle);
      expect(screen.getByText(/invalid_api_key/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /hide details/i })).toBeInTheDocument();
    });

    it("shows a plain-language message for a 429 provider rate limit", async () => {
      vi.spyOn(api, "draftAnswer").mockRejectedValue(
        new api.ApiError(
          "POST failed with 429",
          429,
          JSON.stringify({ detail: "The provider reported a rate limit: too many requests" }),
        ),
      );
      renderOpen(makeDetail());
      await submitDraftRequest();
      expect(screen.getByText(/hit this provider's rate limit/i)).toBeInTheDocument();
    });

    it("shows a plain-language message for a 502 provider failure", async () => {
      vi.spyOn(api, "draftAnswer").mockRejectedValue(
        new api.ApiError("POST failed with 502", 502, JSON.stringify({ detail: "The provider call failed: boom" })),
      );
      renderOpen(makeDetail());
      await submitDraftRequest();
      expect(screen.getByText(/didn't respond correctly/i)).toBeInTheDocument();
    });

    it("replaces the previous draft with the new one instead of stacking", async () => {
      vi.spyOn(api, "draftAnswer")
        .mockResolvedValueOnce({ answer: "First drafted answer.", provider: "groq" })
        .mockResolvedValueOnce({ answer: "Second drafted answer.", provider: "groq" });

      renderOpen(makeDetail());
      fireEvent.change(screen.getByPlaceholderText(/Groq API key|Gemini API key/i), {
        target: { value: "some-key" },
      });

      fireEvent.change(screen.getByPlaceholderText(/why-this-company/i), {
        target: { value: "Draft answer one." },
      });
      fireEvent.click(screen.getByRole("button", { name: /draft answer/i }));
      await waitFor(() => expect(screen.getByText("First drafted answer.")).toBeInTheDocument());

      fireEvent.change(screen.getByPlaceholderText(/why-this-company/i), {
        target: { value: "Draft answer two." },
      });
      fireEvent.click(screen.getByRole("button", { name: /draft answer/i }));
      await waitFor(() => expect(screen.getByText("Second drafted answer.")).toBeInTheDocument());
      expect(screen.queryByText("First drafted answer.")).not.toBeInTheDocument();
    });

    it("copies the drafted answer to the clipboard and shows a brief confirmation", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.assign(navigator, { clipboard: { writeText } });
      vi.spyOn(api, "draftAnswer").mockResolvedValue({ answer: "Drafted answer text.", provider: "groq" });

      renderOpen(makeDetail());
      fireEvent.change(screen.getByPlaceholderText(/Groq API key|Gemini API key/i), {
        target: { value: "some-key" },
      });
      fireEvent.change(screen.getByPlaceholderText(/why-this-company/i), {
        target: { value: "Draft a why-this-company answer." },
      });
      fireEvent.click(screen.getByRole("button", { name: /draft answer/i }));
      await waitFor(() => expect(screen.getByText("Drafted answer text.")).toBeInTheDocument());

      const copyButton = screen.getByRole("button", { name: /^copy$/i });
      fireEvent.click(copyButton);

      await waitFor(() => expect(writeText).toHaveBeenCalledWith("Drafted answer text."));
      expect(await screen.findByRole("button", { name: /copied!/i })).toBeInTheDocument();

      vi.advanceTimersByTime(2000);
      await waitFor(() => expect(screen.getByRole("button", { name: /^copy$/i })).toBeInTheDocument());

      vi.useRealTimers();
    });
  });

  describe("drafting section - saved settings auto-fill", () => {
    it("pre-fills provider and API key from the shared settings on mount", () => {
      writeDraftSettings({ provider: "gemini", apiKey: "saved-gemini-key" });
      renderOpen(makeDetail());

      expect(screen.getByDisplayValue("saved-gemini-key")).toBeInTheDocument();
      expect(screen.getByRole("combobox")).toHaveValue("gemini");
    });

    it("persists across a simulated navigation (unmount + remount, as happens between job pages)", () => {
      writeDraftSettings({ provider: "groq", apiKey: "saved-groq-key" });

      const { unmount } = render(<JobAssistantPanel detail={makeDetail({ id: 1 })} />);
      fireEvent.click(screen.getByRole("button", { name: /open job assistant/i }));
      expect(screen.getByDisplayValue("saved-groq-key")).toBeInTheDocument();
      unmount();

      renderOpen(makeDetail({ id: 2 }));
      expect(screen.getByDisplayValue("saved-groq-key")).toBeInTheDocument();
    });

    it("lets the user override the key for just one request without changing the saved default", () => {
      writeDraftSettings({ provider: "groq", apiKey: "saved-groq-key" });
      renderOpen(makeDetail());

      const input = screen.getByDisplayValue("saved-groq-key");
      fireEvent.change(input, { target: { value: "one-off-override-key" } });

      expect(screen.getByDisplayValue("one-off-override-key")).toBeInTheDocument();
      // The shared saved default is untouched by this local edit - this
      // form's own changes are session-local, never written back to
      // sessionStorage (only DraftSettingsModal's Save button does that).
      expect(window.sessionStorage.getItem("huntloop.draftAnswer.apiKey.groq")).toBe("saved-groq-key");
    });
  });
});

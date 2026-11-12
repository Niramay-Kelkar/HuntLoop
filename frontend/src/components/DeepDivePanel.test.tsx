import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DeepDivePanel } from "./DeepDivePanel";
import { ApiError } from "@/lib/api";
import * as api from "@/lib/api";
import type { DeepDiveGap, DeepDiveResponse, DeepDiveResult } from "@/types/api";

/**
 * The real logic worth pinning down here is the polling lifecycle -
 * that it stops on each terminal status rather than hammering the API
 * forever, that every distinct error/unavailable state renders its own
 * copy (the whole point of the structured {detail, code} error body),
 * and that retry re-dispatches. The markup itself is presentation and
 * isn't asserted field by field.
 */

function makeRecord(overrides: Partial<DeepDiveResponse> = {}): DeepDiveResponse {
  return {
    id: 7,
    job_posting_id: 1,
    resume_version_id: 2,
    status: "running",
    result: null,
    error: null,
    dispatched_at: "2026-11-07T10:00:00",
    created_at: "2026-11-07T10:00:00",
    updated_at: "2026-11-07T10:00:00",
    ...overrides,
  };
}

const RESULT: DeepDiveResult = {
  version: "deep-dive-v1",
  summary: "Strong backend overlap with one infrastructure gap.",
  fit_rating: "strong",
  strengths: [{ point: "Python depth", resume_evidence: "Four years of Django at Acme." }],
  gaps: [
    {
      skill: "Kubernetes",
      importance: "high",
      why_it_matters: "The team self-hosts its clusters.",
      how_to_close: "Deploy a small service to a managed cluster.",
    },
  ],
  resume_edits: [{ section: "Experience", suggestion: "Quantify the migration's impact." }],
  interview_focus: ["Walk through the Django migration"],
  caveats: ["Extracted from a PDF; may be incomplete."],
};

/**
 * fireEvent.click wrapped in act + a flush, so the mutation/query work
 * a click kicks off is settled before the assertion runs.
 * @testing-library/user-event is deliberately not used - it isn't a
 * dependency of this project (this suite uses fireEvent throughout) and
 * this feature adds none.
 */
async function click(element: HTMLElement) {
  await act(async () => {
    fireEvent.click(element);
  });
}

/** A fresh client with retries off, so an error state renders at once. */
function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <DeepDivePanel jobId={1} />
    </QueryClientProvider>,
  );
}

/** Builds the ApiError shape the backend's {detail, code} body produces. */
function structuredError(status: number, detail: string, code: string) {
  return new ApiError(`POST failed with ${status}`, status, JSON.stringify({ detail, code }));
}

describe("DeepDivePanel", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("starts idle with a run button and makes no network call on mount", () => {
    const start = vi.spyOn(api, "startDeepDive");
    const get = vi.spyOn(api, "getDeepDive");

    renderPanel();

    expect(screen.getByRole("button", { name: /run deep-dive/i })).toBeInTheDocument();
    // Mounting must never dispatch - POST also re-runs a failed row, so
    // an automatic call on page load would silently spend Modal budget.
    expect(start).not.toHaveBeenCalled();
    expect(get).not.toHaveBeenCalled();
  });

  it("dispatches on click and shows the running state", async () => {
    vi.spyOn(api, "startDeepDive").mockResolvedValue(makeRecord());
    vi.spyOn(api, "getDeepDive").mockResolvedValue(makeRecord());

    renderPanel();
    await click(screen.getByRole("button", { name: /run deep-dive/i }));

    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(/analyzing your resume/i);
    });
    expect(api.startDeepDive).toHaveBeenCalledWith(1);
  });

  it("polls while running, then stops polling once the status is done", async () => {
    vi.spyOn(api, "startDeepDive").mockResolvedValue(makeRecord());
    const get = vi
      .spyOn(api, "getDeepDive")
      .mockResolvedValueOnce(makeRecord({ status: "running" }))
      .mockResolvedValue(makeRecord({ status: "done", result: RESULT }));

    renderPanel();
    await click(screen.getByRole("button", { name: /run deep-dive/i }));

    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));

    // Second poll resolves the run.
    await vi.advanceTimersByTimeAsync(3_100);
    await waitFor(() => expect(screen.getByText(/strong fit/i)).toBeInTheDocument());

    const callsAtCompletion = get.mock.calls.length;
    // A terminal status must stop the interval dead - advance well past
    // several more poll windows and confirm nothing further is fetched.
    await vi.advanceTimersByTimeAsync(60_000);
    expect(get.mock.calls.length).toBe(callsAtCompletion);
  });

  it("stops polling once the status is failed", async () => {
    vi.spyOn(api, "startDeepDive").mockResolvedValue(makeRecord());
    const get = vi
      .spyOn(api, "getDeepDive")
      .mockResolvedValueOnce(makeRecord({ status: "running" }))
      .mockResolvedValue(
        makeRecord({ status: "failed", error: "The analysis did not complete (RuntimeError)." }),
      );

    renderPanel();
    await click(screen.getByRole("button", { name: /run deep-dive/i }));

    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(3_100);

    await waitFor(() => expect(screen.getByText(/didn't finish/i)).toBeInTheDocument());

    const callsAtFailure = get.mock.calls.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(get.mock.calls.length).toBe(callsAtFailure);
  });

  it("renders every section of a finished result", async () => {
    vi.spyOn(api, "startDeepDive").mockResolvedValue(makeRecord({ status: "done", result: RESULT }));
    vi.spyOn(api, "getDeepDive").mockResolvedValue(makeRecord({ status: "done", result: RESULT }));

    renderPanel();
    await click(screen.getByRole("button", { name: /run deep-dive/i }));

    await waitFor(() => expect(screen.getByText(RESULT.summary as string)).toBeInTheDocument());
    expect(screen.getByText("Strengths")).toBeInTheDocument();
    expect(screen.getByText("Python depth")).toBeInTheDocument();
    expect(screen.getByText("Four years of Django at Acme.")).toBeInTheDocument();
    expect(screen.getByText("Gaps")).toBeInTheDocument();
    expect(screen.getByText("Kubernetes")).toBeInTheDocument();
    expect(screen.getByText("Resume edits")).toBeInTheDocument();
    expect(screen.getByText("Interview focus")).toBeInTheDocument();
    expect(screen.getByText("Caveats")).toBeInTheDocument();
  });

  it("renders a result that is missing every optional section", async () => {
    // A row written by an older prompt version, or a genuinely sparse
    // result - must render, not crash.
    const sparse = { summary: "Just a summary.", fit_rating: "moderate" } as DeepDiveResult;
    vi.spyOn(api, "startDeepDive").mockResolvedValue(makeRecord({ status: "done", result: sparse }));
    vi.spyOn(api, "getDeepDive").mockResolvedValue(makeRecord({ status: "done", result: sparse }));

    renderPanel();
    await click(screen.getByRole("button", { name: /run deep-dive/i }));

    await waitFor(() => expect(screen.getByText("Just a summary.")).toBeInTheDocument());
    expect(screen.getByText(/moderate fit/i)).toBeInTheDocument();
    expect(screen.queryByText("Gaps")).not.toBeInTheDocument();
  });

  it("renders the no-active-resume state", async () => {
    vi.spyOn(api, "startDeepDive").mockRejectedValue(
      structuredError(400, "No active resume is on file.", "no_active_resume"),
    );

    renderPanel();
    await click(screen.getByRole("button", { name: /run deep-dive/i }));

    await waitFor(() => expect(screen.getByText(/no active resume/i)).toBeInTheDocument());
    expect(screen.getByText(/upload a resume on the resume page/i)).toBeInTheDocument();
  });

  it("renders distinct plain copy for budget_exhausted, with no retry button", async () => {
    vi.spyOn(api, "startDeepDive").mockRejectedValue(
      structuredError(503, "This month's analysis budget has been used up.", "budget_exhausted"),
    );

    renderPanel();
    await click(screen.getByRole("button", { name: /run deep-dive/i }));

    await waitFor(() => expect(screen.getByText(/paused for this month/i)).toBeInTheDocument());
    expect(screen.getByText(/available again at the start of next month/i)).toBeInTheDocument();
    // Retrying cannot help until the month rolls over, so no button.
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
  });

  it("renders the modal_unavailable state with the server's own message", async () => {
    vi.spyOn(api, "startDeepDive").mockRejectedValue(
      structuredError(503, "The Deep-Dive analysis service is not available.", "modal_unavailable"),
    );

    renderPanel();
    await click(screen.getByRole("button", { name: /run deep-dive/i }));

    await waitFor(() =>
      expect(screen.getByText("The Deep-Dive analysis service is not available.")).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });

  it("renders the rate-limited state", async () => {
    vi.spyOn(api, "startDeepDive").mockRejectedValue(
      structuredError(429, "Too many Deep-Dive runs started from this client.", "rate_limited"),
    );

    renderPanel();
    await click(screen.getByRole("button", { name: /run deep-dive/i }));

    await waitFor(() => expect(screen.getByText(/too many runs just now/i)).toBeInTheDocument());
  });

  it("renders a generic error for an unstructured failure", async () => {
    // No parseable {detail, code} body - a network failure, a proxy's
    // own error page, a bare 500.
    vi.spyOn(api, "startDeepDive").mockRejectedValue(new ApiError("Could not reach the server.", null));

    renderPanel();
    await click(screen.getByRole("button", { name: /run deep-dive/i }));

    await waitFor(() =>
      expect(screen.getByText(/could not start the deep-dive/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/something went wrong reaching the server/i)).toBeInTheDocument();
  });

  it("retry after a failed run dispatches again and can then succeed", async () => {
    const start = vi
      .spyOn(api, "startDeepDive")
      .mockResolvedValueOnce(makeRecord({ id: 7, status: "failed", error: "it broke" }))
      .mockResolvedValueOnce(makeRecord({ id: 8, status: "done", result: RESULT }));
    vi.spyOn(api, "getDeepDive").mockImplementation(async (id: number) =>
      id === 8
        ? makeRecord({ id: 8, status: "done", result: RESULT })
        : makeRecord({ id: 7, status: "failed", error: "it broke" }),
    );

    renderPanel();
    await click(screen.getByRole("button", { name: /run deep-dive/i }));
    await waitFor(() => expect(screen.getByText(/didn't finish/i)).toBeInTheDocument());

    await click(screen.getByRole("button", { name: /try again/i }));

    await waitFor(() => expect(screen.getByText(RESULT.summary as string)).toBeInTheDocument());
    expect(start).toHaveBeenCalledTimes(2);
  });

  it("retry from a finished result starts a new run", async () => {
    const start = vi
      .spyOn(api, "startDeepDive")
      .mockResolvedValue(makeRecord({ status: "done", result: RESULT }));
    vi.spyOn(api, "getDeepDive").mockResolvedValue(makeRecord({ status: "done", result: RESULT }));

    renderPanel();
    await click(screen.getByRole("button", { name: /run deep-dive/i }));
    await waitFor(() => expect(screen.getByText(RESULT.summary as string)).toBeInTheDocument());

    await click(screen.getByRole("button", { name: /run again/i }));

    await waitFor(() => expect(start).toHaveBeenCalledTimes(2));
  });

  it("gives up with a clear message after the maximum polling window", async () => {
    vi.spyOn(api, "startDeepDive").mockResolvedValue(makeRecord());
    const get = vi.spyOn(api, "getDeepDive").mockResolvedValue(makeRecord({ status: "running" }));

    renderPanel();
    await click(screen.getByRole("button", { name: /run deep-dive/i }));
    await waitFor(() => expect(get).toHaveBeenCalled());

    // Past the 11-minute ceiling.
    await vi.advanceTimersByTimeAsync(11 * 60 * 1_000 + 5_000);

    await waitFor(() =>
      expect(screen.getByText(/taking longer than expected/i)).toBeInTheDocument(),
    );
    const callsAtGiveUp = get.mock.calls.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(get.mock.calls.length).toBe(callsAtGiveUp);
    expect(screen.getByRole("button", { name: /start a new run/i })).toBeInTheDocument();
  });

  /**
   * The report layout itself. These cover the parts that are real logic
   * rather than markup: which sections appear at all, the order gaps are
   * presented in, the fold, the copy affordance, and the honest elapsed
   * counter on the running state.
   */
  describe("report layout", () => {
    /** Renders a finished result and waits for it to be on screen. */
    async function renderResult(result: DeepDiveResult) {
      vi.spyOn(api, "startDeepDive").mockResolvedValue(makeRecord({ status: "done", result }));
      vi.spyOn(api, "getDeepDive").mockResolvedValue(makeRecord({ status: "done", result }));
      renderPanel();
      await click(screen.getByRole("button", { name: /run deep-dive/i }));
      await waitFor(() => expect(screen.getByRole("button", { name: /run again/i })).toBeInTheDocument());
    }

    function gap(skill: string, importance: string): DeepDiveGap {
      return {
        skill,
        importance,
        why_it_matters: `${skill} matters here.`,
        how_to_close: `Practise ${skill}.`,
      } as DeepDiveGap;
    }

    /** The skill headings, in the order they are rendered. */
    function renderedGapSkills(): string[] {
      return screen
        .getAllByRole("heading", { level: 4 })
        .map((heading) => heading.textContent ?? "");
    }

    it("hides the interview-focus and caveats sections, headings included, when their lists are empty", async () => {
      await renderResult({ ...RESULT, interview_focus: [], caveats: [] });

      // The sections that do have content are still there...
      expect(screen.getByText("Strengths")).toBeInTheDocument();
      expect(screen.getByText("Gaps")).toBeInTheDocument();
      // ...and the empty ones are gone entirely - heading and all, not
      // an empty section with a lonely title.
      expect(screen.queryByText("Interview focus")).not.toBeInTheDocument();
      expect(screen.queryByText("Caveats")).not.toBeInTheDocument();
    });

    it("sorts gaps high, then medium, then low regardless of the order they arrive in", async () => {
      await renderResult({
        ...RESULT,
        gaps: [gap("Terraform", "low"), gap("Kafka", "high"), gap("Go", "medium")],
      });

      expect(renderedGapSkills()).toEqual(["Kafka", "Go", "Terraform"]);
    });

    it("keeps the model's own order within one importance level", async () => {
      await renderResult({
        ...RESULT,
        gaps: [gap("Kubernetes", "high"), gap("Kafka", "high"), gap("Go", "medium")],
      });

      expect(renderedGapSkills()).toEqual(["Kubernetes", "Kafka", "Go"]);
    });

    it("folds gaps past the third behind a 'Show N more' control that reveals the rest", async () => {
      await renderResult({
        ...RESULT,
        gaps: [
          gap("Kubernetes", "high"),
          gap("Kafka", "high"),
          gap("Go", "medium"),
          gap("Terraform", "medium"),
          gap("gRPC", "low"),
        ],
      });

      expect(renderedGapSkills()).toEqual(["Kubernetes", "Kafka", "Go"]);
      // The count in the heading still reports every gap, not just the
      // visible ones.
      const showMore = screen.getByRole("button", { name: "Show 2 more" });

      await click(showMore);

      expect(renderedGapSkills()).toEqual(["Kubernetes", "Kafka", "Go", "Terraform", "gRPC"]);
      expect(screen.getByRole("button", { name: /show fewer/i })).toBeInTheDocument();
    });

    it("shows no fold control when there are three gaps or fewer", async () => {
      await renderResult({ ...RESULT, gaps: [gap("Kafka", "high"), gap("Go", "medium")] });

      expect(screen.queryByRole("button", { name: /show \d+ more/i })).not.toBeInTheDocument();
    });

    it("copies the matching suggestion and confirms, then returns to its resting label", async () => {
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, "clipboard", {
        value: { writeText },
        configurable: true,
      });

      await renderResult({
        ...RESULT,
        resume_edits: [
          { section: "Summary", suggestion: "Lead with payments infrastructure." },
          { section: "Skills", suggestion: "Drop jQuery; add Docker." },
        ],
      });

      const copyButtons = screen.getAllByRole("button", { name: /copy suggestion/i });
      expect(copyButtons).toHaveLength(2);

      // The second button must copy the SECOND suggestion, not the first.
      await click(copyButtons[1]);

      expect(writeText).toHaveBeenCalledTimes(1);
      expect(writeText).toHaveBeenCalledWith("Drop jQuery; add Docker.");
      await waitFor(() => expect(copyButtons[1]).toHaveTextContent("Copied"));
      // The other button is untouched.
      expect(copyButtons[0]).toHaveTextContent("Copy");

      // The confirmation is temporary.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_500);
      });
      expect(copyButtons[1]).toHaveTextContent("Copy");
    });

    it.each([
      ["strong", "Strong fit"],
      ["moderate", "Moderate fit"],
      ["stretch", "Stretch"],
    ])("spells the %s fit rating out in the stamp", async (rating, label) => {
      await renderResult({ ...RESULT, fit_rating: rating });

      // The word itself carries the verdict - colour is only a second,
      // redundant signal.
      expect(screen.getByText(label)).toBeInTheDocument();
    });

    it("renders an unrecognised fit rating as its own text rather than dropping it", async () => {
      await renderResult({ ...RESULT, fit_rating: "excellent" });

      expect(screen.getByText("excellent")).toBeInTheDocument();
    });

    it("shows a ticking elapsed counter while a run is in progress", async () => {
      vi.spyOn(api, "startDeepDive").mockResolvedValue(makeRecord({ status: "running" }));
      vi.spyOn(api, "getDeepDive").mockResolvedValue(makeRecord({ status: "running" }));

      renderPanel();
      await click(screen.getByRole("button", { name: /run deep-dive/i }));

      await waitFor(() => expect(screen.getByText("0s")).toBeInTheDocument());
      expect(screen.getByText(/usually takes 10 to 20 seconds/i)).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });

      // Real elapsed time, not a faked stage sequence.
      await waitFor(() => expect(screen.getByText(/^[45]s$/)).toBeInTheDocument());
    });
  });
});

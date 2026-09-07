import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ErrorState } from "./ErrorState";
import { ApiError } from "@/lib/api";

/**
 * Covers the real branching: a 404 reads as "not found" and offers no
 * retry, any other failure reads as a server problem with a working
 * "Try again" button, and the raw response body is never rendered.
 */
describe("ErrorState", () => {
  it("shows a not-found message and no retry button for a 404", () => {
    const onRetry = vi.fn();
    render(
      <ErrorState
        error={new ApiError("GET /jobs/9 failed with 404", 404, '{"detail":"No job posting with id=9"}')}
        onRetry={onRetry}
        resourceLabel="this job"
      />,
    );

    expect(screen.getByText("Not found")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/No job posting with id=9/)).not.toBeInTheDocument();
  });

  it("shows a server-problem message with a working retry for a non-404 error", () => {
    const onRetry = vi.fn();
    render(
      <ErrorState error={new ApiError("Could not reach the server.", null)} onRetry={onRetry} resourceLabel="jobs" />,
    );

    expect(screen.getByText("Couldn't load jobs")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("handles a plain Error (non-ApiError) as a generic server problem", () => {
    render(<ErrorState error={new Error("boom")} resourceLabel="the dashboard" />);
    expect(screen.getByText("Couldn't load the dashboard")).toBeInTheDocument();
    expect(screen.queryByText(/boom/)).not.toBeInTheDocument();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JobFilters, type JobFiltersValue } from "./JobFilters";
import * as api from "@/lib/api";
import { UNSPECIFIED_DEPARTMENT } from "@/types/api";

/**
 * Covers the filter component's real logic: the min-score slider's
 * fraction<->percent conversion (0-1 stored, 0-95% displayed/edited), the
 * "Clear filters" button only appearing once a filter is actually set, and
 * the department sentinel value - not the department <select>'s options
 * themselves, which are just a query result rendered as <option>s.
 */
function renderFilters(value: JobFiltersValue, onChange = vi.fn()) {
  const queryClient = new QueryClient();
  vi.spyOn(api, "getDepartments").mockResolvedValue(["Engineering", "Sales"]);
  render(
    <QueryClientProvider client={queryClient}>
      <JobFilters value={value} onChange={onChange} />
    </QueryClientProvider>,
  );
  return onChange;
}

const emptyValue: JobFiltersValue = { company: "", department: "", minScore: "", sort: "-score" };

describe("JobFilters", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does not show a clear-filters button when nothing is set", () => {
    renderFilters(emptyValue);
    expect(screen.queryByText(/Clear filters/)).not.toBeInTheDocument();
  });

  it("shows a clear-filters button once a filter is set, and clearing preserves sort", () => {
    const onChange = renderFilters({ ...emptyValue, company: "checkr" });

    const clearButton = screen.getByText(/Clear filters/);
    fireEvent.click(clearButton);

    expect(onChange).toHaveBeenCalledWith({ company: "", department: "", minScore: "", sort: "-score" });
  });

  it("converts the min-score slider's percent value back to a 0-1 fraction", () => {
    const onChange = renderFilters(emptyValue);

    const slider = screen.getByRole("slider");
    fireEvent.change(slider, { target: { value: "40" } });

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ minScore: "0.4" }));
  });

  it("displays the stored min-score fraction as its rounded percent", () => {
    renderFilters({ ...emptyValue, minScore: "0.65" });

    expect(screen.getByText("65%")).toBeInTheDocument();
  });

  it("sends the UNSPECIFIED_DEPARTMENT sentinel when 'Not specified' is selected", () => {
    const onChange = renderFilters(emptyValue);

    const select = screen.getByDisplayValue("All departments");
    fireEvent.change(select, { target: { value: UNSPECIFIED_DEPARTMENT } });

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ department: UNSPECIFIED_DEPARTMENT }));
  });
});

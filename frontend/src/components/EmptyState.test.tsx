import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("renders the title, optional description and optional action", () => {
    render(
      <EmptyState
        title="No postings match your filters"
        description="Try widening your search."
        action={<button type="button">Clear all</button>}
      />,
    );

    expect(screen.getByText("No postings match your filters")).toBeInTheDocument();
    expect(screen.getByText("Try widening your search.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear all" })).toBeInTheDocument();
  });

  it("renders without a description or action", () => {
    render(<EmptyState title="Nothing here" />);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
  });
});

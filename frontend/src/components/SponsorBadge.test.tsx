import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SponsorBadge } from "./SponsorBadge";

describe("SponsorBadge", () => {
  it("shows the sponsor wording when the employer has LCA history", () => {
    render(<SponsorBadge hasSponsorHistory />);
    expect(screen.getByText("H-1B on file")).toBeInTheDocument();
  });

  it("shows the no-data wording when the employer has no LCA history", () => {
    render(<SponsorBadge hasSponsorHistory={false} />);
    expect(screen.getByText("No LCA record")).toBeInTheDocument();
  });
});

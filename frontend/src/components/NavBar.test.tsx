import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NavBar } from "./NavBar";

vi.mock("next/navigation", () => ({
  usePathname: () => "/jobs",
}));

describe("NavBar", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("shows the drafting settings gear outside demo mode", () => {
    render(<NavBar />);
    expect(screen.getByRole("button", { name: /drafting settings/i })).toBeInTheDocument();
  });

  describe("in demo mode", () => {
    beforeEach(() => {
      vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "true");
    });

    it("hides the drafting settings gear", () => {
      render(<NavBar />);
      expect(screen.queryByRole("button", { name: /drafting settings/i })).not.toBeInTheDocument();
    });
  });
});

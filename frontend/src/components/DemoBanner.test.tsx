import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DemoBanner } from "./DemoBanner";

function renderBanner() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DemoBanner />
    </QueryClientProvider>,
  );
}

describe("DemoBanner", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("renders nothing outside demo mode", () => {
    renderBanner();
    expect(screen.queryByTestId("demo-banner")).not.toBeInTheDocument();
  });

  describe("in demo mode", () => {
    beforeEach(() => {
      vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "true");
    });

    it("shows the banner with the snapshot date and a link to the repo", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ snapshot_date: "2026-10-01", message: "x" }),
          text: () => Promise.resolve(""),
        }),
      );

      renderBanner();

      expect(await screen.findByTestId("demo-banner")).toBeInTheDocument();
      const banner = await screen.findByText(/2026/);
      expect(banner.textContent).toMatch(/live demo/i);
      expect(screen.getByRole("link")).toHaveAttribute("href", expect.stringContaining("github.com"));
    });

    it("still shows the banner before the snapshot date has loaded", () => {
      vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));
      renderBanner();
      expect(screen.getByTestId("demo-banner")).toBeInTheDocument();
    });
  });
});

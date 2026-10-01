import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DemoWakeUpGate } from "./DemoWakeUpGate";
import * as api from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getHealth: vi.fn(),
}));

describe("DemoWakeUpGate", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("renders children immediately outside demo mode, never calling getHealth", () => {
    render(
      <DemoWakeUpGate>
        <div>page content</div>
      </DemoWakeUpGate>,
    );
    expect(screen.getByText("page content")).toBeInTheDocument();
    expect(api.getHealth).not.toHaveBeenCalled();
  });

  describe("in demo mode", () => {
    beforeEach(() => {
      vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "true");
    });

    it("renders children once the backend answers quickly", async () => {
      vi.mocked(api.getHealth).mockResolvedValue({ status: "ok" });

      render(
        <DemoWakeUpGate>
          <div>page content</div>
        </DemoWakeUpGate>,
      );

      expect(await screen.findByText("page content")).toBeInTheDocument();
    });

    it("shows a wake up message once the backend takes longer than a few seconds", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      // Never resolves within this test - simulates a sleeping backend.
      vi.mocked(api.getHealth).mockReturnValue(new Promise(() => {}));

      render(
        <DemoWakeUpGate>
          <div>page content</div>
        </DemoWakeUpGate>,
      );

      expect(screen.queryByTestId("demo-wakeup-message")).not.toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(3_100);
      });

      expect(screen.getByTestId("demo-wakeup-message")).toBeInTheDocument();
      expect(screen.getByText(/waking up the demo server/i)).toBeInTheDocument();
      expect(screen.queryByText("page content")).not.toBeInTheDocument();
    });
  });
});

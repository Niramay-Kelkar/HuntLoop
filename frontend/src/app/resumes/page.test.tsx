import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "@/lib/api";
import { ToastProvider } from "@/components/Toast";
import ResumesPage from "./page";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getResumes: vi.fn(),
}));

function renderPage() {
  vi.mocked(api.getResumes).mockResolvedValue([
    {
      id: 1,
      version_number: 1,
      uploaded_at: "2026-10-01T00:00:00Z",
      is_active: true,
      text_preview: "Jordan Alvarez, Backend Software Engineer…",
    },
  ]);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ToastProvider>
        <ResumesPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("ResumesPage in demo mode", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv("NEXT_PUBLIC_DEMO_MODE", "true");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("shows an upload-disabled message instead of the dropzone", async () => {
    renderPage();
    expect(await screen.findByTestId("resume-upload-disabled")).toBeInTheDocument();
    expect(screen.getByText(/upload disabled in this demo/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /choose file/i })).not.toBeInTheDocument();
  });

  it("never shows a Make active button", async () => {
    renderPage();
    await screen.findByTestId("resume-upload-disabled");
    expect(screen.queryByRole("button", { name: /make active/i })).not.toBeInTheDocument();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NotesEditor } from "./NotesEditor";
import { ToastProvider } from "./Toast";
import * as api from "@/lib/api";

function renderEditor(notes: string | null) {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <NotesEditor jobId={7} notes={notes} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("NotesEditor", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("saves the note on blur when the text changed", async () => {
    const patchSpy = vi.spyOn(api, "updateApplicationStatus").mockResolvedValue({
      job_posting_id: 7,
      status: "applied",
      applied_at: null,
      status_updated_at: "now",
      notes: "Phone screen booked",
    });
    renderEditor(null);

    const textarea = screen.getByLabelText("Application note");
    fireEvent.change(textarea, { target: { value: "Phone screen booked" } });
    fireEvent.blur(textarea);

    await waitFor(() => expect(patchSpy).toHaveBeenCalledWith(7, { notes: "Phone screen booked" }));
  });

  it("does not fire a request on blur when the text is unchanged", () => {
    const patchSpy = vi.spyOn(api, "updateApplicationStatus");
    renderEditor("Existing note");

    const textarea = screen.getByLabelText("Application note");
    fireEvent.focus(textarea);
    fireEvent.blur(textarea);

    expect(patchSpy).not.toHaveBeenCalled();
  });
});

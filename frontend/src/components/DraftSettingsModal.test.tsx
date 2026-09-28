import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { readDraftSettings } from "@/lib/draftSettings";
import { DraftSettingsModal } from "./DraftSettingsModal";

describe("DraftSettingsModal", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });
  afterEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("is closed until the gear icon is clicked", () => {
    render(<DraftSettingsModal />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /drafting settings/i }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("saves the chosen provider (localStorage) and key (sessionStorage)", () => {
    render(<DraftSettingsModal />);
    fireEvent.click(screen.getByRole("button", { name: /drafting settings/i }));

    fireEvent.change(screen.getByLabelText(/provider/i), { target: { value: "gemini" } });
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: "my-real-looking-key" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    expect(readDraftSettings()).toEqual({ provider: "gemini", apiKey: "my-real-looking-key" });
    expect(window.localStorage.getItem("huntloop.draftAnswer.preferredProvider")).toBe("gemini");
    expect(window.sessionStorage.getItem("huntloop.draftAnswer.apiKey.gemini")).toBe(
      "my-real-looking-key",
    );
    expect(window.localStorage.getItem("huntloop.draftAnswer.apiKey.gemini")).toBeNull();
    expect(screen.getByText(/saved/i)).toBeInTheDocument();
  });

  it("closes on the ✕ button without discarding what was already saved", () => {
    render(<DraftSettingsModal />);
    fireEvent.click(screen.getByRole("button", { name: /drafting settings/i }));
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: "groq-key" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    fireEvent.click(screen.getByRole("button", { name: /close/i }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(readDraftSettings()).toEqual({ provider: "groq", apiKey: "groq-key" });
  });
});

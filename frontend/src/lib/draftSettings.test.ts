import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DRAFT_SETTINGS_CHANGED_EVENT,
  readApiKey,
  readDraftSettings,
  readPreferredProvider,
  writeDraftSettings,
} from "./draftSettings";

describe("draftSettings", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });
  afterEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("defaults to groq with no saved key when nothing is stored", () => {
    expect(readPreferredProvider()).toBe("groq");
    expect(readDraftSettings()).toEqual({ provider: "groq", apiKey: "" });
  });

  it("persists a saved provider + key so it can be read back", () => {
    writeDraftSettings({ provider: "gemini", apiKey: "my-gemini-key" });
    expect(readPreferredProvider()).toBe("gemini");
    expect(readDraftSettings()).toEqual({ provider: "gemini", apiKey: "my-gemini-key" });
  });

  it("keeps each provider's key separate - switching preferred provider doesn't lose the other one's key", () => {
    writeDraftSettings({ provider: "groq", apiKey: "groq-key" });
    writeDraftSettings({ provider: "gemini", apiKey: "gemini-key" });

    expect(readApiKey("groq")).toBe("groq-key");
    expect(readApiKey("gemini")).toBe("gemini-key");
    // The last write also changed the preferred provider.
    expect(readDraftSettings()).toEqual({ provider: "gemini", apiKey: "gemini-key" });
  });

  it("removes a provider's stored key when saved with an empty key", () => {
    writeDraftSettings({ provider: "groq", apiKey: "groq-key" });
    writeDraftSettings({ provider: "groq", apiKey: "" });
    expect(readApiKey("groq")).toBe("");
  });

  it("dispatches a change event on save so an already-mounted form can pick it up", () => {
    const handler = vi.fn();
    window.addEventListener(DRAFT_SETTINGS_CHANGED_EVENT, handler);
    writeDraftSettings({ provider: "groq", apiKey: "x" });
    expect(handler).toHaveBeenCalledTimes(1);
    window.removeEventListener(DRAFT_SETTINGS_CHANGED_EVENT, handler);
  });

  it("saves the API key to sessionStorage (not localStorage) while the preferred provider stays in localStorage", () => {
    writeDraftSettings({ provider: "gemini", apiKey: "my-gemini-key" });

    expect(window.sessionStorage.getItem("huntloop.draftAnswer.apiKey.gemini")).toBe(
      "my-gemini-key",
    );
    expect(window.localStorage.getItem("huntloop.draftAnswer.apiKey.gemini")).toBeNull();

    expect(window.localStorage.getItem("huntloop.draftAnswer.preferredProvider")).toBe("gemini");
    expect(window.sessionStorage.getItem("huntloop.draftAnswer.preferredProvider")).toBeNull();
  });

  it("removes a legacy API key left in localStorage on module load, without copying it into sessionStorage", async () => {
    window.localStorage.setItem("huntloop.draftAnswer.apiKey.groq", "old-leftover-key");
    window.localStorage.setItem("huntloop.draftAnswer.apiKey.gemini", "another-old-key");

    vi.resetModules();
    const fresh = await import("./draftSettings");

    expect(window.localStorage.getItem("huntloop.draftAnswer.apiKey.groq")).toBeNull();
    expect(window.localStorage.getItem("huntloop.draftAnswer.apiKey.gemini")).toBeNull();
    expect(window.sessionStorage.getItem("huntloop.draftAnswer.apiKey.groq")).toBeNull();
    expect(window.sessionStorage.getItem("huntloop.draftAnswer.apiKey.gemini")).toBeNull();
    expect(fresh.readApiKey("groq")).toBe("");
    expect(fresh.readApiKey("gemini")).toBe("");
  });
});

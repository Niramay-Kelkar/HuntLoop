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
  });
  afterEach(() => {
    window.localStorage.clear();
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
});

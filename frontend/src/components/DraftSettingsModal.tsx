"use client";

import { useEffect, useState } from "react";

import { readApiKey, readDraftSettings, writeDraftSettings } from "@/lib/draftSettings";
import type { DraftAnswerProvider } from "@/types/api";

/**
 * App-wide, gear-icon-triggered settings drawer for the BYOK drafting
 * feature (see JobAssistantPanel.tsx's DraftingSection and
 * lib/draftSettings.ts). Lets the user set a preferred provider + API
 * key ONCE instead of retyping it on every job detail page - saved to
 * this browser's localStorage only (see lib/draftSettings.ts's
 * docstring for why: no server-side/account-backed version of this
 * exists or is planned until real user auth exists as its own separate
 * project).
 *
 * Rendered from NavBar.tsx so it's reachable from every page, not just
 * a job detail page.
 */
const PROVIDER_OPTIONS: { value: DraftAnswerProvider; label: string }[] = [
  { value: "groq", label: "Groq" },
  { value: "gemini", label: "Gemini" },
];

export function DraftSettingsModal() {
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState<DraftAnswerProvider>("groq");
  const [apiKey, setApiKey] = useState("");
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    if (!open) return;
    const settings = readDraftSettings();
    setProvider(settings.provider);
    setApiKey(settings.apiKey);
    setSavedAt(null);
  }, [open]);

  function handleProviderChange(next: DraftAnswerProvider) {
    setProvider(next);
    // readApiKey(next) - not readDraftSettings() - since that provider
    // might not be the currently-preferred one; this just shows
    // whatever key (if any) is already saved for the provider the
    // dropdown was just switched to.
    setApiKey(readApiKey(next));
  }

  function handleSave() {
    writeDraftSettings({ provider, apiKey });
    setSavedAt(Date.now());
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Drafting settings"
        title="Drafting settings (provider + API key)"
        className="grid h-8 w-8 place-items-center border border-transparent text-text-subtle hover:border-border hover:text-text"
      >
        <span aria-hidden className="text-base leading-none">
          ⚙
        </span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Drafting settings"
          className="fixed inset-0 z-40 flex items-start justify-end bg-black/30 p-4"
          onClick={() => setOpen(false)}
        >
          <div
            className="mt-14 flex w-full max-w-sm flex-col gap-3 border border-border-strong bg-surface p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold text-text">Drafting settings</h2>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close"
                className="text-text-subtle hover:text-text"
              >
                ✕
              </button>
            </div>

            <p className="text-xs leading-relaxed text-text-subtle">
              Set your preferred provider and API key once - job pages will pre-fill this
              automatically. Stored only in this browser (localStorage), never on our servers.
              You can still override the key for a single request on any job page.
            </p>

            <label className="flex flex-col gap-1 text-sm text-text">
              Provider
              <select
                value={provider}
                onChange={(e) => handleProviderChange(e.target.value as DraftAnswerProvider)}
                className="border border-border bg-surface px-2 py-1.5 text-sm text-text"
              >
                {PROVIDER_OPTIONS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex flex-col gap-1 text-sm text-text">
              API key
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={`Your ${provider === "groq" ? "Groq" : "Gemini"} API key`}
                className="border border-border bg-surface px-2 py-1.5 text-sm text-text"
              />
            </label>

            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={handleSave}
                className="border border-border px-3 py-1.5 text-sm text-text hover:border-accent hover:text-accent"
              >
                Save
              </button>
              {savedAt !== null && <span className="text-xs text-good">Saved.</span>}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

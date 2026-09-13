/**
 * Shared, browser-localStorage-only settings for the BYOK drafting
 * feature (POST /jobs/{id}/draft-answer - see JobAssistantPanel.tsx and
 * DraftSettingsModal.tsx). Lets the user set a preferred provider + API
 * key ONCE (via DraftSettingsModal, opened from NavBar's gear icon)
 * instead of retyping the key on every job detail page.
 *
 * Client-side only, by design, matching the per-request BYOK key this
 * was built on top of: nothing here is ever sent to any backend for
 * storage - it's read purely to pre-fill the per-request form fields
 * that already send the key straight to the chosen provider. There is
 * deliberately no server-side/account-backed version of this - that's
 * explicitly deferred until real user authentication exists as its own
 * separate future project, not something to build here.
 *
 * The API key is stored PER PROVIDER (so switching the preferred
 * provider doesn't lose the other provider's key), while the preferred
 * provider itself is a single shared value. `readDraftSettings()`
 * combines both into the one shape callers actually want: "the key for
 * whichever provider is currently preferred."
 */
import type { DraftAnswerProvider } from "@/types/api";

const PREFERRED_PROVIDER_KEY = "huntloop.draftAnswer.preferredProvider";
const DEFAULT_PROVIDER: DraftAnswerProvider = "groq";

// Same event name both DraftSettingsModal (writer) and any open
// JobAssistantPanel (reader) listen for, so saving a new default in the
// settings modal while a job detail page is already mounted updates
// that page's drafting form immediately, not just on the next
// navigation. The plain `storage` event does NOT fire in the same tab
// that made the write (only other tabs/windows) - this custom event is
// what covers the same-tab case.
export const DRAFT_SETTINGS_CHANGED_EVENT = "huntloop:draft-settings-changed";

export interface DraftSettings {
  provider: DraftAnswerProvider;
  apiKey: string;
}

function apiKeyStorageKey(provider: DraftAnswerProvider): string {
  return `huntloop.draftAnswer.apiKey.${provider}`;
}

function safeGetItem(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    // Private-browsing / blocked storage - behave as if nothing is saved.
    return null;
  }
}

function safeSetItem(key: string, value: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Ignore storage failures - the caller's in-memory state still
    // works for this session, it just won't persist.
  }
}

function safeRemoveItem(key: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Ignore.
  }
}

export function readPreferredProvider(): DraftAnswerProvider {
  const stored = safeGetItem(PREFERRED_PROVIDER_KEY);
  return stored === "groq" || stored === "gemini" ? stored : DEFAULT_PROVIDER;
}

export function readApiKey(provider: DraftAnswerProvider): string {
  return safeGetItem(apiKeyStorageKey(provider)) ?? "";
}

/** The one shape callers actually want: the preferred provider, plus
 * whatever key is saved for it. */
export function readDraftSettings(): DraftSettings {
  const provider = readPreferredProvider();
  return { provider, apiKey: readApiKey(provider) };
}

function notifyChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(DRAFT_SETTINGS_CHANGED_EVENT));
}

/** Persists the shared default provider + API key - called only from
 * DraftSettingsModal's Save action. The per-job drafting form reads
 * these as its initial defaults but does NOT write back here itself
 * when the user overrides the key just for one request - see
 * JobAssistantPanel.tsx. */
export function writeDraftSettings(settings: DraftSettings): void {
  safeSetItem(PREFERRED_PROVIDER_KEY, settings.provider);
  if (settings.apiKey) {
    safeSetItem(apiKeyStorageKey(settings.provider), settings.apiKey);
  } else {
    safeRemoveItem(apiKeyStorageKey(settings.provider));
  }
  notifyChanged();
}

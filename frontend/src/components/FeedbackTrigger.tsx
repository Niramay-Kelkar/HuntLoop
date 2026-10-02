"use client";

import { useState, type FormEvent } from "react";
import { usePathname } from "next/navigation";

import { submitFeedback } from "@/lib/api";
import { useToast } from "@/components/Toast";
import type { FeedbackCategory } from "@/types/api";

/**
 * App-wide feedback entry point - a corner trigger button + small panel,
 * reachable from anywhere (mounted once in layout.tsx), in the same
 * spirit as JobAssistantPanel's chat panel but for submitting a report
 * rather than asking a question. Posts to POST /feedback
 * (huntloop.api.routers.feedback) and shows a simple "thanks, got it"
 * toast on success - no result is shown inline beyond that; the
 * submitted report is never shown back to the user here (there's no
 * submission history view - see the /status page for what's public).
 *
 * This is NOT a BYOK feature and has nothing to do with
 * DraftSettingsModal/JobAssistantPanel - no API key, no third-party
 * provider call happens on this request path (triage happens
 * out-of-band - see scripts/triage_feedback.py).
 */
const CATEGORY_OPTIONS: { value: FeedbackCategory; label: string }[] = [
  { value: "bug", label: "Bug" },
  { value: "feature", label: "Feature request" },
  { value: "question", label: "Question" },
  { value: "other", label: "Other" },
];

const MAX_DESCRIPTION_LENGTH = 2000;

export function FeedbackTrigger() {
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState<FeedbackCategory>("bug");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pathname = usePathname();
  const { showToast } = useToast();

  function reset() {
    setCategory("bug");
    setDescription("");
    setError(null);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = description.trim();
    if (!trimmed || submitting) return;

    setSubmitting(true);
    setError(null);
    try {
      await submitFeedback({
        category,
        description: trimmed,
        page_path: pathname ?? undefined,
      });
      setOpen(false);
      reset();
      showToast("Thanks, got it.", "success");
    } catch {
      setError("Could not submit feedback - please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed bottom-4 left-4 z-30 border border-border-strong bg-surface px-3 py-2 text-xs font-medium text-text-subtle shadow-[0_2px_10px_rgba(20,30,40,.12)] hover:border-accent hover:text-accent"
      >
        Feedback
      </button>

      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Send feedback"
          className="fixed inset-0 z-40 flex items-end justify-start bg-black/30 p-4"
          onClick={() => setOpen(false)}
        >
          <form
            onSubmit={handleSubmit}
            onClick={(e) => e.stopPropagation()}
            className="flex w-full max-w-sm flex-col gap-3 border border-border-strong bg-surface p-5"
          >
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold text-text">Send feedback</h2>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close"
                className="text-text-subtle hover:text-text"
              >
                ✕
              </button>
            </div>

            <label className="flex flex-col gap-1 text-sm text-text">
              Category
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value as FeedbackCategory)}
                className="border border-border bg-surface px-2 py-1.5 text-sm text-text"
              >
                {CATEGORY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex flex-col gap-1 text-sm text-text">
              What's going on?
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value.slice(0, MAX_DESCRIPTION_LENGTH))}
                rows={4}
                placeholder="Describe the bug, idea, or question..."
                className="border border-border bg-surface px-2 py-1.5 text-sm text-text"
              />
              <span className="text-right text-xs text-text-subtle">
                {description.length}/{MAX_DESCRIPTION_LENGTH}
              </span>
            </label>

            {error && <p className="text-xs text-bad">{error}</p>}

            <button
              type="submit"
              disabled={!description.trim() || submitting}
              className="border border-border px-3 py-1.5 text-sm text-text hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? "Sending..." : "Send"}
            </button>
          </form>
        </div>
      )}
    </>
  );
}

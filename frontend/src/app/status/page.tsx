"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getPublicFeedback } from "@/lib/api";
import { ErrorState } from "@/components/ErrorState";
import type { FeedbackStatus, PublicFeedbackItem } from "@/types/api";

/**
 * Public, read-only status page - GET /feedback/public
 * (huntloop.api.routers.feedback). No submission form here; the
 * submission entry point is FeedbackTrigger (see layout.tsx), reachable
 * from any page. Only rows a human has explicitly marked
 * is_public=true (via scripts/review_feedback.py) ever show up here,
 * and only category/llm_summary/status/created_at - never the
 * submitter's raw text (see that endpoint's docstring for why).
 */
const STATUS_ORDER: FeedbackStatus[] = ["open", "in_progress", "resolved", "wont_fix"];

const STATUS_LABEL: Record<FeedbackStatus, string> = {
  open: "Open",
  in_progress: "In progress",
  resolved: "Resolved",
  wont_fix: "Won't fix",
};

const STATUS_DOT: Record<FeedbackStatus, string> = {
  open: "#b0432f",
  in_progress: "#1b4965",
  resolved: "#2f7d4f",
  wont_fix: "#78818b",
};

function groupByStatus(items: PublicFeedbackItem[]): Record<FeedbackStatus, PublicFeedbackItem[]> {
  const groups: Record<FeedbackStatus, PublicFeedbackItem[]> = {
    open: [],
    in_progress: [],
    resolved: [],
    wont_fix: [],
  };
  for (const item of items) {
    groups[item.status]?.push(item);
  }
  return groups;
}

export default function StatusPage() {
  const [sortNewestFirst, setSortNewestFirst] = useState(true);

  const feedback = useQuery({
    queryKey: ["public-feedback"],
    queryFn: getPublicFeedback,
  });

  const grouped = useMemo(() => {
    if (!feedback.data) return null;
    const sorted = [...feedback.data].sort((a, b) => {
      const diff = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
      return sortNewestFirst ? -diff : diff;
    });
    return groupByStatus(sorted);
  }, [feedback.data, sortNewestFirst]);

  return (
    <main className="flex flex-1 flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-text">Feedback status</h1>
          <p className="mt-1 text-sm text-text-subtle">
            Publicly shared reports from the HuntLoop feedback form, grouped by status.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setSortNewestFirst((prev) => !prev)}
          className="border border-border px-2.5 py-1.5 font-mono text-xs text-text-subtle hover:border-accent hover:text-accent"
        >
          {sortNewestFirst ? "Newest first" : "Oldest first"}
        </button>
      </div>

      {feedback.isPending && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-[72px] animate-pulse border border-border bg-surface-alt" />
          ))}
        </div>
      )}

      {feedback.isError && (
        <ErrorState error={feedback.error} onRetry={() => feedback.refetch()} resourceLabel="feedback status" />
      )}

      {grouped && (
        <div className="flex flex-col gap-6">
          {STATUS_ORDER.map((status) => {
            const items = grouped[status];
            if (items.length === 0) return null;
            return (
              <section key={status} className="flex flex-col gap-2">
                <h2 className="flex items-center gap-2 font-mono text-xs font-semibold uppercase tracking-[0.08em] text-text-faint">
                  <span className="h-2 w-2" style={{ backgroundColor: STATUS_DOT[status] }} />
                  {STATUS_LABEL[status]} ({items.length})
                </h2>
                <div className="flex flex-col divide-y divide-border border border-border bg-surface">
                  {items.map((item, idx) => (
                    <div key={idx} className="flex flex-wrap items-start justify-between gap-2 p-3">
                      <div className="flex flex-col gap-0.5">
                        <span className="font-mono text-[11px] uppercase tracking-[0.06em] text-text-faint">
                          {item.category}
                        </span>
                        <p className="text-sm text-text">{item.llm_summary ?? "(summary pending)"}</p>
                      </div>
                      <span className="whitespace-nowrap font-mono text-xs text-text-subtle">
                        {new Date(item.created_at).toLocaleDateString(undefined, {
                          year: "numeric",
                          month: "short",
                          day: "numeric",
                        })}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            );
          })}

          {Object.values(grouped).every((items) => items.length === 0) && (
            <p className="text-sm text-text-subtle">No publicly shared feedback yet.</p>
          )}
        </div>
      )}
    </main>
  );
}

"use client";

import { useState } from "react";
import { useAuth, UserButton } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ErrorState } from "@/components/ErrorState";
import { useToast } from "@/components/Toast";
import { ApiError } from "@/lib/api";
import { getAdminFeedback, updateAdminFeedback } from "@/lib/adminApi";
import type { AdminFeedbackItem, FeedbackStatus } from "@/types/api";

/**
 * Admin-only feedback review - GET/PATCH /admin/feedback*
 * (huntloop.api.routers.admin_feedback). Replaces the terminal-only
 * scripts/review_feedback.py workflow with a real web UI.
 *
 * Reachability has two independent layers, and this page only owns
 * the first half of each:
 *   1. src/proxy.ts already redirects a signed-out visitor to Clerk's
 *      sign-in flow before this component ever mounts - by the time
 *      this renders, there's always a Clerk session.
 *   2. The backend separately checks that session's email against
 *      ADMIN_ALLOWED_EMAILS (huntloop.api.admin_auth) - a valid Clerk
 *      login that isn't on the allowlist gets a real 403 from the API,
 *      which this page has to handle explicitly (a signed-in-but-not-
 *      authorized state ErrorState's generic "server problem" copy
 *      would mischaracterize).
 *
 * Unlike /status (the public read-only page), every row is shown
 * regardless of is_public, and raw_text is shown in full - this page
 * must never be reachable without both checks above passing.
 */
const STATUS_OPTIONS: FeedbackStatus[] = ["open", "in_progress", "resolved", "wont_fix"];

const STATUS_LABEL: Record<FeedbackStatus, string> = {
  open: "Open",
  in_progress: "In progress",
  resolved: "Resolved",
  wont_fix: "Won't fix",
};

export default function AdminFeedbackPage() {
  const { getToken } = useAuth();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const feedback = useQuery({
    queryKey: ["admin-feedback"],
    queryFn: async () => {
      const token = await getToken();
      if (!token) {
        throw new ApiError("Not signed in.", 401);
      }
      return getAdminFeedback(token);
    },
  });

  const updateMutation = useMutation({
    mutationFn: async ({
      id,
      status,
      is_public,
    }: {
      id: number;
      status?: FeedbackStatus;
      is_public?: boolean;
    }) => {
      const token = await getToken();
      if (!token) {
        throw new ApiError("Not signed in.", 401);
      }
      return updateAdminFeedback(token, id, { status, is_public });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-feedback"] });
      showToast("Updated.", "success");
    },
    onError: () => {
      showToast("Couldn't save that change. Try again.", "error");
    },
  });

  const forbidden = feedback.isError && feedback.error instanceof ApiError && feedback.error.status === 403;

  return (
    <main className="flex flex-1 flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-text">Feedback (admin)</h1>
          <p className="mt-1 text-sm text-text-subtle">
            Every submitted report, including raw text. Only reachable by an allow-listed Clerk account.
          </p>
        </div>
        <UserButton />
      </div>

      {forbidden && (
        <div
          role="alert"
          className="flex flex-col items-center gap-2 border border-border-strong bg-surface-alt px-6 py-12 text-center"
        >
          <span aria-hidden className="text-lg text-accent">
            &#9888;
          </span>
          <p className="text-sm font-semibold text-text">Not authorized</p>
          <p className="max-w-sm text-sm leading-relaxed text-text-subtle">
            You&apos;re signed in, but this Clerk account isn&apos;t on the admin allowlist for this app.
          </p>
        </div>
      )}

      {feedback.isError && !forbidden && (
        <ErrorState error={feedback.error} onRetry={() => feedback.refetch()} resourceLabel="feedback" />
      )}

      {feedback.isPending && (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-[52px] animate-pulse border border-border bg-surface-alt" />
          ))}
        </div>
      )}

      {feedback.data && (
        <div className="overflow-x-auto border border-border">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead>
              <tr className="border-b border-border bg-surface-alt font-mono text-[11px] uppercase tracking-[0.06em] text-text-faint">
                <th className="px-3 py-2">Created</th>
                <th className="px-3 py-2">Category</th>
                <th className="px-3 py-2">Report</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Public</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {feedback.data.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-6 text-center text-text-subtle">
                    No feedback submitted yet.
                  </td>
                </tr>
              )}
              {feedback.data.map((row) => (
                <AdminFeedbackRow
                  key={row.id}
                  row={row}
                  expanded={expandedId === row.id}
                  onToggleExpand={() => setExpandedId((prev) => (prev === row.id ? null : row.id))}
                  onChangeStatus={(status) => updateMutation.mutate({ id: row.id, status })}
                  onTogglePublic={() => updateMutation.mutate({ id: row.id, is_public: !row.is_public })}
                  saving={updateMutation.isPending && updateMutation.variables?.id === row.id}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}

function AdminFeedbackRow({
  row,
  expanded,
  onToggleExpand,
  onChangeStatus,
  onTogglePublic,
  saving,
}: {
  row: AdminFeedbackItem;
  expanded: boolean;
  onToggleExpand: () => void;
  onChangeStatus: (status: FeedbackStatus) => void;
  onTogglePublic: () => void;
  saving: boolean;
}) {
  return (
    <>
      <tr className="align-top">
        <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-text-subtle">
          {new Date(row.created_at).toLocaleString(undefined, {
            year: "numeric",
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          })}
        </td>
        <td className="px-3 py-2 font-mono text-xs uppercase tracking-[0.04em] text-text-faint">{row.category}</td>
        <td className="max-w-md px-3 py-2">
          <button
            type="button"
            onClick={onToggleExpand}
            className="text-left text-sm text-text hover:underline"
          >
            {expanded ? row.raw_text : truncate(row.raw_text, 120)}
          </button>
          {row.llm_summary && (
            <p className="mt-1 text-xs text-text-faint">Summary: {row.llm_summary}</p>
          )}
        </td>
        <td className="px-3 py-2">
          <select
            value={row.status}
            disabled={saving}
            onChange={(e) => onChangeStatus(e.target.value as FeedbackStatus)}
            className="border border-border bg-surface px-2 py-1 font-mono text-xs text-text disabled:opacity-50"
          >
            {STATUS_OPTIONS.map((status) => (
              <option key={status} value={status}>
                {STATUS_LABEL[status]}
              </option>
            ))}
          </select>
        </td>
        <td className="px-3 py-2">
          <button
            type="button"
            disabled={saving}
            onClick={onTogglePublic}
            className={`border px-2 py-1 font-mono text-xs disabled:opacity-50 ${
              row.is_public
                ? "border-accent bg-accent text-white"
                : "border-border text-text-subtle hover:border-accent hover:text-accent"
            }`}
          >
            {row.is_public ? "Public" : "Private"}
          </button>
        </td>
      </tr>
    </>
  );
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max)}…`;
}

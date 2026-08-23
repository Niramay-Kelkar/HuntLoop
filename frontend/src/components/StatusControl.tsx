"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { updateApplicationStatus } from "@/lib/api";
import type { ApplicationStatus, JobListResponse } from "@/types/api";
import { useToast } from "./Toast";
import { STATUS_LABELS, STATUS_STYLES } from "./StatusBadge";

const STATUS_OPTIONS: ApplicationStatus[] = [
  "not_applied",
  "applied",
  "interviewing",
  "rejected",
  "offer",
];

/**
 * Interactive replacement for StatusBadge on JobCard - a styled
 * `<select>` (looks like the badge, but is a real control) wired to the
 * real PATCH /jobs/{id}/application endpoint via a TanStack Query
 * mutation with optimistic updates: the displayed status changes
 * immediately on selection, before the request resolves, and rolls back
 * to whatever it actually was if the request fails - never left showing
 * a stale "succeeded" state. Every list query currently in the cache
 * (any company/min_score/sort/page combination) is updated, not just
 * the one the user is currently viewing, so the change is consistent if
 * they page back.
 */
export function StatusControl({ jobId, status }: { jobId: number; status: ApplicationStatus }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const mutation = useMutation({
    mutationFn: (newStatus: ApplicationStatus) => updateApplicationStatus(jobId, { status: newStatus }),

    onMutate: async (newStatus) => {
      await queryClient.cancelQueries({ queryKey: ["jobs"] });

      const previous = queryClient.getQueriesData<JobListResponse>({ queryKey: ["jobs"] });

      queryClient.setQueriesData<JobListResponse>({ queryKey: ["jobs"] }, (old) => {
        if (!old) return old;
        return {
          ...old,
          items: old.items.map((item) =>
            item.id === jobId ? { ...item, application_status: newStatus } : item,
          ),
        };
      });

      return { previous };
    },

    onError: (error, _newStatus, context) => {
      // Roll back every query snapshot taken in onMutate - the optimistic
      // update never actually happened as far as the server's concerned.
      if (context?.previous) {
        for (const [queryKey, data] of context.previous) {
          queryClient.setQueryData(queryKey, data);
        }
      }
      showToast(
        `Failed to update status${error instanceof Error ? `: ${error.message}` : ""}`,
        "error",
      );
    },

    onSuccess: (_data, newStatus) => {
      showToast(`Status updated to "${STATUS_LABELS[newStatus]}"`, "success");
    },

    onSettled: () => {
      // Reconcile with the server's actual state either way - confirms
      // the optimistic update on success, and confirms the rollback
      // (or picks up whatever's really there) on failure.
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  return (
    <select
      value={status}
      disabled={mutation.isPending}
      onChange={(e) => mutation.mutate(e.target.value as ApplicationStatus)}
      aria-label="Application status"
      className={`rounded-full border-0 px-2.5 py-1 text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-60 ${STATUS_STYLES[status]}`}
    >
      {STATUS_OPTIONS.map((option) => (
        <option key={option} value={option}>
          {STATUS_LABELS[option]}
        </option>
      ))}
    </select>
  );
}

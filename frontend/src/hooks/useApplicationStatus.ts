"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { updateApplicationStatus } from "@/lib/api";
import type { ApplicationStatus, JobListResponse } from "@/types/api";
import { useToast } from "@/components/Toast";
import { STATUS_META } from "@/lib/theme";

interface Variables {
  jobId: number;
  status: ApplicationStatus;
}

/**
 * Shared optimistic-update mutation for PATCH /jobs/{id}/application - one
 * mutation instance handles any job id (passed per-call via `variables`,
 * not bound at hook-creation time), so a single instance created once at
 * the top of a list/board component can back every row/card's status
 * change (JobCard, job detail, the applications kanban board's
 * drag-and-drop drop handler, and its list view's per-row select) with the
 * same rollback-on-failure behavior instead of duplicating it per widget.
 */
export function useApplicationStatusMutation() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation({
    mutationFn: ({ jobId, status }: Variables) => updateApplicationStatus(jobId, { status }),

    onMutate: async ({ jobId, status }) => {
      await queryClient.cancelQueries({ queryKey: ["jobs"] });

      const previous = queryClient.getQueriesData<JobListResponse>({ queryKey: ["jobs"] });

      queryClient.setQueriesData<JobListResponse>({ queryKey: ["jobs"] }, (old) => {
        if (!old) return old;
        return {
          ...old,
          items: old.items.map((item) => (item.id === jobId ? { ...item, application_status: status } : item)),
        };
      });

      return { previous };
    },

    onError: (error, _variables, context) => {
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

    onSuccess: (_data, { status }) => {
      showToast(`Status updated to "${STATUS_META[status].label}"`, "success");
    },

    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

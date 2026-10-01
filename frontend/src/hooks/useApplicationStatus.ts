"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { updateApplicationStatus } from "@/lib/api";
import type { ApplicationStatus, JobDetail, JobListResponse } from "@/types/api";
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

    onMutate: async ({ jobId, status }: Variables) => {
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

    onSuccess: (data, { status }) => {
      if (data.demo) {
        showToast("Demo only, changes are not saved", "success");
        return;
      }
      showToast(`Status updated to "${STATUS_META[status].label}"`, "success");
    },

    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["job"] });
    },
  });
}

interface NotesVariables {
  jobId: number;
  notes: string;
}

/**
 * Companion to useApplicationStatusMutation for the per-application
 * free-text note (job_applications.notes) - same PATCH endpoint, same
 * optimistic-update / rollback-on-failure / toast pattern, but it only
 * sends `notes` so it never disturbs the status. The endpoint upserts an
 * application row if one doesn't exist yet.
 */
export function useApplicationNotesMutation() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation({
    mutationFn: ({ jobId, notes }: NotesVariables) => updateApplicationStatus(jobId, { notes }),

    onMutate: async ({ jobId, notes }: NotesVariables) => {
      await queryClient.cancelQueries({ queryKey: ["jobs"] });
      await queryClient.cancelQueries({ queryKey: ["job"] });
      const nextNotes = notes || null;

      const previousLists = queryClient.getQueriesData<JobListResponse>({ queryKey: ["jobs"] });
      queryClient.setQueriesData<JobListResponse>({ queryKey: ["jobs"] }, (old) => {
        if (!old) return old;
        return {
          ...old,
          items: old.items.map((item) =>
            item.id === jobId ? { ...item, application_notes: nextNotes } : item,
          ),
        };
      });

      const previousDetails = queryClient.getQueriesData<JobDetail>({ queryKey: ["job"] });
      queryClient.setQueriesData<JobDetail>({ queryKey: ["job"] }, (old) =>
        old && old.id === jobId ? { ...old, application_notes: nextNotes } : old,
      );

      return { previous: [...previousLists, ...previousDetails] };
    },

    onError: (error, _variables, context) => {
      if (context?.previous) {
        for (const [queryKey, data] of context.previous) {
          queryClient.setQueryData(queryKey, data);
        }
      }
      showToast(
        `Failed to save note${error instanceof Error ? `: ${error.message}` : ""}`,
        "error",
      );
    },

    onSuccess: (data) => {
      if (data.demo) {
        showToast("Demo only, changes are not saved", "success");
        return;
      }
      showToast("Note saved", "success");
    },

    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["job"] });
    },
  });
}

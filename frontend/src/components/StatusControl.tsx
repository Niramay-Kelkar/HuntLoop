"use client";

import { useApplicationStatusMutation } from "@/hooks/useApplicationStatus";
import type { ApplicationStatus } from "@/types/api";
import { STATUS_META, STATUS_ORDER } from "@/lib/theme";

/**
 * Interactive status control wired to the real PATCH /jobs/{id}/application
 * endpoint (mutation logic lives in useApplicationStatus, shared with the
 * kanban board and applications list) with optimistic updates and
 * rollback-on-failure. Behavior is unchanged; only the styling follows
 * the "Register" identity - a square, hairline-ruled mono select rather
 * than a rounded pill.
 */
export function StatusControl({
  jobId,
  status,
  size = "md",
}: {
  jobId: number;
  status: ApplicationStatus;
  size?: "sm" | "md";
}) {
  const mutation = useApplicationStatusMutation();
  const meta = STATUS_META[status];

  return (
    <select
      value={status}
      disabled={mutation.isPending}
      onChange={(e) => mutation.mutate({ jobId, status: e.target.value as ApplicationStatus })}
      aria-label="Application status"
      className={`appearance-none border font-mono font-medium uppercase tracking-[0.04em] focus:outline-none focus:ring-1 focus:ring-accent disabled:opacity-60 ${
        size === "sm" ? "px-1.5 py-0.5 text-2xs" : "px-2 py-1 text-xs"
      }`}
      style={{ backgroundColor: meta.bg, color: meta.color, borderColor: `${meta.color}55` }}
    >
      {STATUS_ORDER.map((option) => (
        <option key={option} value={option}>
          {STATUS_META[option].label}
        </option>
      ))}
    </select>
  );
}

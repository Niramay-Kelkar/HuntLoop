"use client";

import { useApplicationStatusMutation } from "@/hooks/useApplicationStatus";
import type { ApplicationStatus } from "@/types/api";
import { STATUS_META, STATUS_ORDER } from "@/lib/theme";

/**
 * Interactive status control wired to the real PATCH /jobs/{id}/application
 * endpoint (mutation logic lives in useApplicationStatus, shared with the
 * kanban board and applications list) with optimistic updates and
 * rollback-on-failure - unchanged behavior from before the reskin, only the
 * visual styling changed to the mockup's pill-select ("rowSelectStyle" in
 * design/HuntLoop.dc.html - the mockup only shows an editable status select
 * on its applications tracker; the job list/table there is read-only. This
 * app keeps status editable everywhere it was editable before the reskin,
 * so JobCard/JobTable use this same pill styling instead of a truly
 * read-only badge).
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
      className={`appearance-none rounded-full border font-semibold focus:outline-none focus:ring-2 focus:ring-accent disabled:opacity-60 ${
        size === "sm" ? "px-2.5 py-1 text-[11px]" : "px-3 py-1.5 text-xs"
      }`}
      style={{ backgroundColor: meta.bg, color: meta.color, borderColor: `${meta.color}44` }}
    >
      {STATUS_ORDER.map((option) => (
        <option key={option} value={option}>
          {STATUS_META[option].label}
        </option>
      ))}
    </select>
  );
}

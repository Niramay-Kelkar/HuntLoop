"use client";

import { useState } from "react";
import Link from "next/link";

import type { JobSummary } from "@/types/api";
import { useApplicationStatusMutation } from "@/hooks/useApplicationStatus";
import { STATUS_META, STATUS_ORDER, avatarColors, initials, scoreTier, timeAgo } from "@/lib/theme";
import type { ApplicationStatus } from "@/types/api";

/**
 * Drag-and-drop status board - uses native HTML5 drag events, matching
 * design/HuntLoop.dc.html's kanbanColumns exactly (draggable card,
 * onDragStart/onDragOver/onDrop), wired to the same
 * PATCH /jobs/{id}/application mutation (with optimistic update +
 * rollback-on-failure) as everywhere else status can be changed.
 */
export function KanbanBoard({ jobs }: { jobs: JobSummary[] }) {
  const mutation = useApplicationStatusMutation();
  const [dragOverCol, setDragOverCol] = useState<ApplicationStatus | null>(null);

  function handleDrop(e: React.DragEvent, status: ApplicationStatus) {
    e.preventDefault();
    setDragOverCol(null);
    const id = Number(e.dataTransfer.getData("text/plain"));
    if (id) mutation.mutate({ jobId: id, status });
  }

  return (
    <div className="flex items-start gap-3.5 overflow-x-auto pb-2">
      {STATUS_ORDER.map((status) => {
        const meta = STATUS_META[status];
        const colJobs = jobs.filter((j) => j.application_status === status);
        return (
          <div
            key={status}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOverCol(status);
            }}
            onDragLeave={() => setDragOverCol((cur) => (cur === status ? null : cur))}
            onDrop={(e) => handleDrop(e, status)}
            className={`flex min-w-[236px] flex-1 flex-col gap-2.5 rounded-xl bg-kanban-bg p-2.5 ${
              dragOverCol === status ? "ring-2 ring-accent" : ""
            }`}
          >
            <div className="flex items-center gap-2 px-1.5 py-1">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: meta.dot }} />
              <span className="text-[13px] font-semibold text-text">{meta.label}</span>
              <span className="ml-auto font-mono text-xs text-text-faintest">{colJobs.length}</span>
            </div>

            {colJobs.map((job) => {
              const avatar = avatarColors(job.id);
              const tier = job.match_score !== null ? scoreTier(job.match_score) : null;
              const updatedLabel = timeAgo(job.status_updated_at);
              return (
                <Link
                  key={job.id}
                  href={`/jobs/${job.id}`}
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData("text/plain", String(job.id));
                    e.dataTransfer.effectAllowed = "move";
                  }}
                  className="cursor-pointer rounded-lg border border-border bg-surface p-3 hover:shadow-[0_4px_14px_rgba(0,0,0,.08)]"
                >
                  <div className="mb-2 flex items-center gap-2">
                    <div
                      className="grid h-[30px] w-[30px] flex-none place-items-center rounded-lg font-mono text-[11px] font-bold"
                      style={{ backgroundColor: avatar.bg, color: avatar.color }}
                    >
                      {initials(job.company_name)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-semibold text-text">{job.job_title}</div>
                      <div className="text-[11px] text-text-subtle">{job.company_name}</div>
                    </div>
                  </div>
                  <div className="flex items-center justify-between">
                    {job.match_score !== null && tier ? (
                      <span
                        className="rounded-md px-2 py-0.5 font-mono text-xs font-bold"
                        style={{ backgroundColor: tier.bg, color: tier.color }}
                      >
                        {Math.round(job.match_score * 100)}%
                      </span>
                    ) : (
                      <span className="font-mono text-xs text-text-faintest">—</span>
                    )}
                    <span className="flex items-center gap-1 font-mono text-[11px] text-text-faintest">
                      {job.application_notes ? (
                        <span title={job.application_notes} aria-label="Has a note">
                          ✎
                        </span>
                      ) : null}
                      {updatedLabel ?? "—"}
                    </span>
                  </div>
                </Link>
              );
            })}

            {colJobs.length === 0 && (
              <div className="py-3.5 text-center font-mono text-[11px] text-[#b8b1a7]">Drop here</div>
            )}
          </div>
        );
      })}
    </div>
  );
}

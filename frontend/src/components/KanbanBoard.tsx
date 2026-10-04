"use client";

import { useState } from "react";
import Link from "next/link";

import type { JobSummary } from "@/types/api";
import { useApplicationStatusMutation } from "@/hooks/useApplicationStatus";
import { STATUS_META, STATUS_ORDER, avatarColors, companyLabel, initials, scoreTier, timeAgo } from "@/lib/theme";
import type { ApplicationStatus } from "@/types/api";

/**
 * Drag-and-drop status board - native HTML5 drag events (draggable
 * card, onDragStart/onDragOver/onDrop), wired to the same
 * PATCH /jobs/{id}/application mutation (optimistic + rollback) as
 * everywhere else status can be changed. Behavior unchanged; the
 * "Register" identity squares the columns and cards and drops the
 * card shadows for hairline rules.
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
    <div className="flex items-start gap-3 overflow-x-auto pb-2">
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
            className={`flex min-w-[236px] flex-1 flex-col gap-2 border border-border bg-kanban-bg p-2 ${
              dragOverCol === status ? "outline outline-1 outline-accent" : ""
            }`}
          >
            <div className="flex items-center gap-2 border-b border-border px-1 py-1.5">
              <span className="h-2 w-2 flex-none" style={{ backgroundColor: meta.dot }} />
              <span className="text-sm font-semibold text-text">{meta.label}</span>
              <span className="ml-auto font-mono text-xs text-text-faintest">{colJobs.length}</span>
            </div>

            {colJobs.map((job) => {
              const avatar = avatarColors(job.id);
              const company = companyLabel(job);
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
                  className="cursor-pointer border border-border bg-surface p-2.5 hover:border-border-strong"
                >
                  <div className="mb-2 flex items-center gap-2">
                    <div
                      className="grid h-[28px] w-[28px] flex-none place-items-center rounded-sm font-mono text-2xs font-semibold"
                      style={{ backgroundColor: avatar.bg, color: avatar.color }}
                    >
                      {initials(company)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-semibold text-text">{job.job_title}</div>
                      <div className="text-xs text-text-subtle">{company}</div>
                    </div>
                  </div>
                  <div className="flex items-center justify-between">
                    {job.match_score !== null && tier ? (
                      <span className="font-mono text-xs font-semibold" style={{ color: tier.color }}>
                        {Math.round(job.match_score * 100)}
                      </span>
                    ) : (
                      <span className="font-mono text-xs text-text-faintest">--</span>
                    )}
                    <span className="flex items-center gap-1 font-mono text-2xs text-text-faintest">
                      {job.application_notes ? (
                        <span title={job.application_notes} aria-label="Has a note">
                          ✎
                        </span>
                      ) : null}
                      {updatedLabel ?? "--"}
                    </span>
                  </div>
                </Link>
              );
            })}

            {colJobs.length === 0 && (
              <div className="py-3 text-center font-mono text-2xs uppercase tracking-[0.08em] text-text-faintest">
                Drop here
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

import Link from "next/link";

import type { JobSummary } from "@/types/api";
import { EmptyState } from "./EmptyState";
import { NotesEditor } from "./NotesEditor";
import { StatusControl } from "./StatusControl";
import { STATUS_ORDER, avatarColors, formatDate, initials, scoreTier, timeAgo } from "@/lib/theme";

/** List view of the applications tracker - every job with a status other
 * than "not applied", ordered the same way as design/HuntLoop.dc.html's
 * trackerList (by STATUS_ORDER, not by date). Unlike the board, this view
 * also shows each application's free-text note and when its status last
 * changed. */
export function ApplicationsList({ jobs }: { jobs: JobSummary[] }) {
  const tracked = jobs
    .filter((j) => j.application_status !== "not_applied")
    .slice()
    .sort((a, b) => STATUS_ORDER.indexOf(a.application_status) - STATUS_ORDER.indexOf(b.application_status));

  if (tracked.length === 0) {
    return (
      <EmptyState
        icon="✦"
        title="No applications tracked yet"
        description="Set a status on a job from the Jobs list or a job's detail page and it shows up here."
      />
    );
  }

  return (
    <div className="flex flex-col gap-2.5">
      {tracked.map((job) => {
        const avatar = avatarColors(job.id);
        const tier = job.match_score !== null ? scoreTier(job.match_score) : null;
        const updated = timeAgo(job.status_updated_at);
        return (
          <div key={job.id} className="rounded-xl border border-border bg-surface p-3.5">
            <div className="flex flex-wrap items-start gap-2.5">
              <Link href={`/jobs/${job.id}`} className="flex min-w-0 flex-1 items-center gap-2.5">
                <div
                  className="grid h-[30px] w-[30px] flex-none place-items-center rounded-lg font-mono text-[11px] font-bold"
                  style={{ backgroundColor: avatar.bg, color: avatar.color }}
                >
                  {initials(job.company_name)}
                </div>
                <div className="min-w-0">
                  <div className="truncate text-[13px] font-semibold text-text">{job.job_title}</div>
                  <div className="truncate text-xs text-text-subtle">
                    {job.company_name}
                    {job.locations.length > 0 ? ` · ${job.locations[0]}` : ""}
                  </div>
                </div>
              </Link>
              <div className="flex flex-none items-center gap-2">
                {job.match_score !== null && tier ? (
                  <span
                    className="rounded-md px-2 py-0.5 font-mono text-xs font-bold"
                    style={{ backgroundColor: tier.bg, color: tier.color }}
                  >
                    {Math.round(job.match_score * 100)}%
                  </span>
                ) : null}
                <StatusControl jobId={job.id} status={job.application_status} size="sm" />
              </div>
            </div>

            <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[11px] text-text-faintest">
              {formatDate(job.date_posted) && <span>Posted {formatDate(job.date_posted)}</span>}
              {updated && <span>Status changed {updated}</span>}
            </div>

            <div className="mt-2.5">
              <NotesEditor jobId={job.id} notes={job.application_notes} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

import Link from "next/link";

import type { JobSummary } from "@/types/api";
import { StatusControl } from "./StatusControl";
import { STATUS_ORDER, avatarColors, formatDate, initials, scoreTier } from "@/lib/theme";

/** List view of the applications tracker - every job with a status other
 * than "not applied", ordered the same way as design/HuntLoop.dc.html's
 * trackerList (by STATUS_ORDER, not by date). */
export function ApplicationsList({ jobs }: { jobs: JobSummary[] }) {
  const tracked = jobs
    .filter((j) => j.application_status !== "not_applied")
    .slice()
    .sort((a, b) => STATUS_ORDER.indexOf(a.application_status) - STATUS_ORDER.indexOf(b.application_status));

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-surface">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[680px] border-collapse">
          <thead>
            <tr className="bg-surface-alt">
              <Th>Role</Th>
              <Th>Match</Th>
              <Th>Posted</Th>
              <Th>Status</Th>
            </tr>
          </thead>
          <tbody>
            {tracked.map((job) => {
              const avatar = avatarColors(job.id);
              const tier = job.match_score !== null ? scoreTier(job.match_score) : null;
              return (
                <tr key={job.id} className="border-t border-divider hover:bg-surface-alt">
                  <td className="px-4 py-2.5">
                    <Link href={`/jobs/${job.id}`} className="flex items-center gap-2.5">
                      <div
                        className="grid h-[30px] w-[30px] flex-none place-items-center rounded-lg font-mono text-[11px] font-bold"
                        style={{ backgroundColor: avatar.bg, color: avatar.color }}
                      >
                        {initials(job.company_name)}
                      </div>
                      <div className="min-w-0">
                        <div className="truncate text-[13px] font-semibold text-text">{job.job_title}</div>
                        <div className="text-xs text-text-subtle">
                          {job.company_name}
                          {job.locations.length > 0 ? ` · ${job.locations[0]}` : ""}
                        </div>
                      </div>
                    </Link>
                  </td>
                  <td className="px-2 py-2.5">
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
                  </td>
                  <td className="px-2 py-2.5 font-mono text-xs text-text-muted">
                    {formatDate(job.date_posted) ?? "—"}
                  </td>
                  <td className="px-4 py-2.5">
                    <StatusControl jobId={job.id} status={job.application_status} size="sm" />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {tracked.length === 0 && (
        <div className="px-5 py-12 text-center text-sm text-text-faintest">
          No applications tracked yet — status changes made from the job list or job detail page show up here.
        </div>
      )}
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="px-2 py-[11px] text-left font-mono text-[10px] font-semibold uppercase tracking-wide text-text-faintest first:pl-4 last:pr-4">
      {children}
    </th>
  );
}

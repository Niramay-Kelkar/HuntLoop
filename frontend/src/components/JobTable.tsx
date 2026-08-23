import Link from "next/link";

import type { JobSummary } from "@/types/api";
import { StatusControl } from "./StatusControl";
import { avatarColors, initials, scoreTier } from "@/lib/theme";

function ScorePill({ score }: { score: number | null }) {
  if (score === null) {
    return <span className="rounded-md bg-surface-alt px-2 py-0.5 font-mono text-xs text-text-faintest">—</span>;
  }
  const { color, bg } = scoreTier(score);
  return (
    <span className="rounded-md px-2 py-0.5 font-mono text-xs font-bold" style={{ backgroundColor: bg, color }}>
      {Math.round(score * 100)}%
    </span>
  );
}

export function JobTable({ jobs }: { jobs: JobSummary[] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-surface">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse">
          <thead>
            <tr className="bg-surface-alt">
              <Th className="w-[70px]">Match</Th>
              <Th>Role</Th>
              <Th>Location</Th>
              <Th>Sponsor</Th>
              <Th>Status</Th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => {
              const avatar = avatarColors(job.id);
              return (
                <tr key={job.id} className="border-t border-divider hover:bg-surface-alt">
                  <td className="px-4 py-2.5">
                    <ScorePill score={job.match_score} />
                  </td>
                  <td className="px-2 py-2.5">
                    <Link href={`/jobs/${job.id}`} className="flex items-center gap-2.5">
                      <div
                        className="grid h-[30px] w-[30px] flex-none place-items-center rounded-lg font-mono text-[11px] font-bold"
                        style={{ backgroundColor: avatar.bg, color: avatar.color }}
                      >
                        {initials(job.company_name)}
                      </div>
                      <div className="min-w-0">
                        <div className="truncate text-[13px] font-semibold text-text">{job.job_title}</div>
                        <div className="text-xs text-text-subtle">{job.company_name}</div>
                      </div>
                    </Link>
                  </td>
                  <td className="px-2 py-2.5 text-[13px] text-text-muted">
                    {job.locations.length > 0 ? job.locations.join(" · ") : "—"}
                  </td>
                  <td className="px-2 py-2.5 text-xs">
                    <span
                      className="inline-flex items-center gap-1"
                      style={{ color: job.has_sponsor_history ? "#1f8f4e" : "#a89f95" }}
                    >
                      <span
                        className="h-1.5 w-1.5 rounded-full"
                        style={{ backgroundColor: job.has_sponsor_history ? "#1f9d55" : "#c9c2b8" }}
                      />
                      {job.has_sponsor_history ? "Sponsors H-1B" : "No H-1B data"}
                    </span>
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
      {jobs.length === 0 && (
        <div className="px-5 py-12 text-center text-sm text-text-faintest">No jobs match these filters.</div>
      )}
    </div>
  );
}

function Th({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <th
      className={`px-2 py-[11px] text-left font-mono text-[10px] font-semibold uppercase tracking-wide text-text-faintest first:pl-4 last:pr-4 ${className}`}
    >
      {children}
    </th>
  );
}

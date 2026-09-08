import Link from "next/link";

import type { JobSummary } from "@/types/api";
import { ProvisionalScoreNote, ScoreIndicator } from "./ScoreIndicator";
import { SponsorBadge } from "./SponsorBadge";
import { StatusControl } from "./StatusControl";
import { avatarColors, formatWage, initials } from "@/lib/theme";

export function JobTable({ jobs }: { jobs: JobSummary[] }) {
  return (
    <div className="border border-border bg-surface">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[820px] border-collapse">
          <thead>
            <tr className="border-b border-border bg-surface-alt">
              <Th className="w-[128px]">Match</Th>
              <Th>Role</Th>
              <Th>Location</Th>
              <Th>Est. salary</Th>
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
                    <ScoreIndicator score={job.match_score} size="sm" provisional={job.score_basis === "partial"} />
                  </td>
                  <td className="px-2 py-2.5">
                    <Link href={`/jobs/${job.id}`} className="flex items-center gap-2.5">
                      <div
                        className="grid h-[28px] w-[28px] flex-none place-items-center rounded-sm font-mono text-2xs font-semibold"
                        style={{ backgroundColor: avatar.bg, color: avatar.color }}
                      >
                        {initials(job.company_name)}
                      </div>
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold text-text">{job.job_title}</div>
                        <div className="flex items-center gap-1.5 text-xs text-text-subtle">
                          <span className="truncate">{job.company_name}</span>
                          {job.employment_type && (
                            <span className="flex-none border border-border px-1 py-px font-mono text-2xs uppercase tracking-[0.04em] text-text-muted">
                              {job.employment_type}
                            </span>
                          )}
                        </div>
                      </div>
                    </Link>
                  </td>
                  <td className="px-2 py-2.5 text-sm text-text-muted">
                    {job.locations.length > 0 ? job.locations.join(" / ") : "--"}
                  </td>
                  <td className="px-2 py-2.5 font-mono text-xs text-text-muted">
                    {job.salary_estimate ? (
                      <span title={job.salary_estimate.basis}>~{formatWage(job.salary_estimate.amount)}</span>
                    ) : (
                      "--"
                    )}
                  </td>
                  <td className="px-2 py-2.5">
                    <SponsorBadge hasSponsorHistory={job.has_sponsor_history} />
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
      {jobs.length === 0 ? (
        <div className="px-5 py-12 text-center text-sm text-text-faintest">No jobs match these filters.</div>
      ) : (
        <div className="flex flex-col gap-1 border-t border-divider px-4 py-2 font-mono text-2xs text-text-faintest">
          <span>Est. salary is an employer-level estimate from DOL filings, not a posted salary</span>
          <ProvisionalScoreNote jobs={jobs} />
        </div>
      )}
    </div>
  );
}

function Th({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <th
      className={`px-2 py-[11px] text-left font-mono text-2xs font-semibold uppercase tracking-[0.08em] text-text-faint first:pl-4 last:pr-4 ${className}`}
    >
      {children}
    </th>
  );
}

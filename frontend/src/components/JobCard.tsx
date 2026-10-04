import Link from "next/link";

import type { JobSummary } from "@/types/api";
import { ScoreIndicator } from "./ScoreIndicator";
import { SkillChipsPreview } from "./SkillChips";
import { SponsorBadge } from "./SponsorBadge";
import { StatusControl } from "./StatusControl";
import { avatarColors, companyLabel, formatDate, formatWage, initials } from "@/lib/theme";

export function JobCard({ job }: { job: JobSummary }) {
  const postedDate = formatDate(job.date_posted);
  const avatar = avatarColors(job.id);
  const company = companyLabel(job);

  return (
    <article className="relative flex flex-col gap-3 border border-border border-t-2 border-t-accent bg-surface p-4 transition-colors hover:border-border-strong hover:border-t-accent">
      <Link href={`/jobs/${job.id}`} className="absolute inset-0 z-0" aria-label={job.job_title} />

      <div className="pointer-events-none relative z-[1] flex items-start gap-3">
        <div
          className="grid h-[34px] w-[34px] flex-none place-items-center rounded-sm font-mono text-sm font-semibold"
          style={{ backgroundColor: avatar.bg, color: avatar.color }}
        >
          {initials(company)}
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-base font-bold leading-tight tracking-tight text-text">
            {job.job_title}
          </p>
          <p className="mt-0.5 text-sm text-text-subtle">{company}</p>
        </div>
        <ScoreIndicator score={job.match_score} size="sm" provisional={job.score_basis === "partial"} />
      </div>

      <div className="pointer-events-none relative z-[1]">
        <SkillChipsPreview matchedSkills={job.matched_skills} />
      </div>

      <div className="pointer-events-none relative z-[1] flex flex-col gap-1.5 text-sm text-text-subtle">
        <span className="truncate">
          {job.locations.length > 0 ? job.locations.join(" / ") : "Location not listed"}
        </span>
        <div className="flex flex-wrap items-center gap-1.5">
          {job.employment_type && (
            <span className="border border-border px-1.5 py-0.5 font-mono text-2xs uppercase tracking-[0.04em] text-text-muted">
              {job.employment_type}
            </span>
          )}
          <SponsorBadge hasSponsorHistory={job.has_sponsor_history} />
          {job.salary_estimate && (
            <span
              className="border border-border px-1.5 py-0.5 font-mono text-2xs text-text-faint"
              title={job.salary_estimate.basis}
            >
              Est. ~{formatWage(job.salary_estimate.amount)}
            </span>
          )}
        </div>
      </div>

      <div className="relative z-[1] flex items-center justify-between border-t border-divider pt-2.5">
        <StatusControl jobId={job.id} status={job.application_status} size="sm" />
        <span className="font-mono text-2xs text-text-faintest">
          {postedDate ? `Posted ${postedDate}` : "--"}
        </span>
      </div>
    </article>
  );
}

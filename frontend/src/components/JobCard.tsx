import Link from "next/link";

import type { JobSummary } from "@/types/api";
import { ScoreIndicator } from "./ScoreIndicator";
import { SkillChipsPreview } from "./SkillChips";
import { StatusControl } from "./StatusControl";
import { avatarColors, formatDate, initials } from "@/lib/theme";

export function JobCard({ job }: { job: JobSummary }) {
  const postedDate = formatDate(job.date_posted);
  const avatar = avatarColors(job.id);

  return (
    <article className="relative flex flex-col gap-3 rounded-xl border border-border bg-surface p-4 transition-all hover:-translate-y-px hover:border-border-strong hover:shadow-[0_6px_20px_rgba(0,0,0,.06)]">
      <Link href={`/jobs/${job.id}`} className="absolute inset-0 z-0" aria-label={job.job_title} />

      <div className="pointer-events-none relative z-[1] flex items-start gap-3">
        <div
          className="grid h-[38px] w-[38px] flex-none place-items-center rounded-lg font-mono text-[13px] font-bold"
          style={{ backgroundColor: avatar.bg, color: avatar.color }}
        >
          {initials(job.company_name)}
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[15px] font-semibold leading-tight tracking-tight text-text">
            {job.job_title}
          </p>
          <p className="mt-0.5 text-[13px] text-text-subtle">{job.company_name}</p>
        </div>
        <ScoreIndicator score={job.match_score} />
      </div>

      <div className="pointer-events-none relative z-[1]">
        <SkillChipsPreview matchedSkills={job.matched_skills} />
      </div>

      <div className="pointer-events-none relative z-[1] flex items-center gap-2 text-xs text-text-subtle">
        <span>{job.locations.length > 0 ? job.locations.join(" · ") : "Location not listed"}</span>
        <span className="text-divider">·</span>
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
      </div>

      <div className="relative z-[1] flex items-center justify-between border-t border-divider pt-2.5">
        <StatusControl jobId={job.id} status={job.application_status} size="sm" />
        <span className="font-mono text-[11px] text-text-faintest">
          {postedDate ? `Posted ${postedDate}` : "—"}
        </span>
      </div>
    </article>
  );
}

import type { JobSummary } from "@/types/api";
import { ScoreIndicator } from "./ScoreIndicator";
import { SkillChips } from "./SkillChips";
import { StatusControl } from "./StatusControl";

function formatDate(dateString: string | null): string | null {
  if (!dateString) return null;
  return new Date(dateString).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function JobCard({ job }: { job: JobSummary }) {
  const postedDate = formatDate(job.date_posted);

  return (
    <article className="flex flex-col gap-3 rounded-xl border border-neutral-200 bg-white p-5 shadow-sm transition-shadow hover:shadow-md dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
            {job.company_name}
          </p>
          <a
            href={job.job_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-lg font-semibold text-neutral-900 hover:text-blue-600 hover:underline dark:text-neutral-50 dark:hover:text-blue-400"
          >
            {job.job_title}
          </a>
          <p className="mt-0.5 text-sm text-neutral-500 dark:text-neutral-400">
            {job.locations.length > 0 ? job.locations.join(" · ") : "Location not listed"}
            {postedDate && <span className="text-neutral-300 dark:text-neutral-600"> · Posted {postedDate}</span>}
          </p>
        </div>

        <div className="flex flex-shrink-0 flex-col items-end gap-2">
          <ScoreIndicator score={job.match_score} />
          <StatusControl jobId={job.id} status={job.application_status} />
        </div>
      </div>

      <SkillChips matchedSkills={job.matched_skills} missingSkills={job.missing_skills} />
    </article>
  );
}

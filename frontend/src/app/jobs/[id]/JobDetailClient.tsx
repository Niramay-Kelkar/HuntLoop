"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { getJob } from "@/lib/api";
import { ScoreIndicator } from "@/components/ScoreIndicator";
import { StatusControl } from "@/components/StatusControl";
import { avatarColors, formatDate, initials, stripHtml } from "@/lib/theme";

export function JobDetailClient({ jobId }: { jobId: number }) {
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId),
  });

  if (job.isPending) {
    return (
      <div className="flex flex-col gap-4">
        <div className="h-40 animate-pulse rounded-xl border border-border bg-surface-alt" />
        <div className="h-56 animate-pulse rounded-xl border border-border bg-surface-alt" />
      </div>
    );
  }

  if (job.isError) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        Failed to load job: {job.error.message}
      </div>
    );
  }

  const detail = job.data;
  const avatar = avatarColors(detail.id);
  const postedDate = formatDate(detail.date_posted);
  const matched = detail.matched_skills ?? [];
  const missing = detail.missing_skills ?? [];
  const description = detail.job_description ? stripHtml(detail.job_description) : null;

  return (
    <div className="flex flex-col gap-4">
      <Link href="/jobs" className="mb-1 inline-block font-mono text-xs text-text-subtle hover:text-text">
        ← Back to jobs
      </Link>

      <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        {/* main column */}
        <div className="flex flex-col gap-4">
          <div className="rounded-2xl border border-border bg-surface p-6">
            <div className="flex items-start gap-4">
              <div
                className="grid h-14 w-14 flex-none place-items-center rounded-xl font-mono text-lg font-bold"
                style={{ backgroundColor: avatar.bg, color: avatar.color }}
              >
                {initials(detail.company_name)}
              </div>
              <div className="min-w-0 flex-1">
                <h1 className="text-[23px] font-semibold leading-tight tracking-tight text-text">
                  {detail.job_title}
                </h1>
                <p className="mt-1 text-[15px] text-text-muted">
                  {detail.company_name}
                  {detail.department ? ` · ${detail.department}` : ""}
                </p>
                <div className="mt-3 flex flex-wrap gap-3.5 text-[13px] text-text-subtle">
                  <span>◎ {detail.locations.length > 0 ? detail.locations.join(" · ") : "Location not listed"}</span>
                  {postedDate && <span>◷ Posted {postedDate}</span>}
                </div>
              </div>
              <ScoreIndicator score={detail.match_score} size="lg" />
            </div>

            <div className="mt-5 flex flex-wrap gap-2.5">
              <a
                href={detail.job_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 rounded-lg bg-accent px-5 py-2.5 text-sm font-semibold text-white shadow-[0_2px_8px_rgba(224,83,61,.3)] hover:bg-accent-hover"
              >
                Apply ↗
              </a>
              <StatusControl jobId={detail.id} status={detail.application_status} />
            </div>
          </div>

          {/* matched skills first, missing second */}
          <div className="rounded-2xl border border-border bg-surface p-6">
            <div className="mb-1 flex items-center gap-2">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: "#1f9d55" }} />
              <h2 className="text-base font-semibold text-text">Your matching skills</h2>
              <span className="rounded-md bg-[#e9f4ee] px-2 py-0.5 font-mono text-xs text-[#1f8f4e]">
                {matched.length} matched
              </span>
            </div>
            <p className="mb-3.5 text-[13px] text-text-subtle">
              Pulled from your active resume — skills this role asks for that you already demonstrate.
            </p>
            {matched.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {matched.map((sk, i) => (
                  <span
                    key={`${sk}-${i}`}
                    className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 font-mono text-[13px] font-medium"
                    style={{ backgroundColor: "#e9f4ee", color: "#1f7a45", borderColor: "#cfe8d8" }}
                  >
                    <span style={{ color: "#1f9d55" }}>✓</span>
                    {sk}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-[13px] text-text-faintest">Not analyzed yet.</p>
            )}

            <div className="mt-5 border-t border-divider pt-4">
              <div className="mb-1 flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-[#c9c2b8]" />
                <h3 className="text-sm font-semibold text-text-muted">Skills to develop</h3>
                <span className="font-mono text-xs text-text-faintest">{missing.length} gap</span>
              </div>
              <p className="mb-3 text-[13px] text-text-faintest">
                Mentioned in the posting but not evident on your resume.
              </p>
              {missing.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                  {missing.map((sk, i) => (
                    <span
                      key={`${sk}-${i}`}
                      className="rounded-lg border border-dashed px-2.5 py-1.5 font-mono text-xs"
                      style={{ backgroundColor: "#faf9f7", color: "#8a837a", borderColor: "#d9d3cb" }}
                    >
                      {sk}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-[13px] text-text-faintest">No gaps identified.</p>
              )}
            </div>
          </div>

          {description && (
            <div className="rounded-2xl border border-border bg-surface p-6">
              <h2 className="mb-3 text-base font-semibold text-text">About the role</h2>
              <p className="whitespace-pre-line text-sm leading-relaxed text-text-secondary">{description}</p>
            </div>
          )}
        </div>

        {/* sidebar */}
        <div className="flex flex-col gap-4 lg:sticky lg:top-[78px]">
          <div className="rounded-2xl border border-border bg-surface p-5">
            <h3 className="mb-3.5 font-mono text-xs font-semibold uppercase tracking-wide text-text-faintest">
              Company
            </h3>
            <div className="flex flex-col gap-2.5 text-[13px]">
              <div className="flex justify-between">
                <span className="text-text-subtle">Name</span>
                <span className="font-semibold text-text">{detail.company_name}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-subtle">Department</span>
                <span className="font-medium text-text">{detail.department ?? "—"}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

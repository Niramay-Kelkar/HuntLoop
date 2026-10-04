"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { getJob } from "@/lib/api";
import type { CompanyResearch } from "@/types/api";
import { ErrorState } from "@/components/ErrorState";
import { JobAssistantPanel } from "@/components/JobAssistantPanel";
import { NotesEditor } from "@/components/NotesEditor";
import { ScoreIndicator } from "@/components/ScoreIndicator";
import { StatusControl } from "@/components/StatusControl";
import { avatarColors, companyLabel, formatDate, formatWage, initials, timeAgo } from "@/lib/theme";
import { sanitizeJobDescription } from "@/lib/sanitizeHtml";
import { isDemoMode } from "@/lib/demoMode";

export function JobDetailClient({ jobId }: { jobId: number }) {
  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId),
  });

  if (job.isPending) {
    return (
      <div className="flex flex-col gap-4">
        <div className="h-40 animate-pulse border border-border bg-surface-alt" />
        <div className="h-56 animate-pulse border border-border bg-surface-alt" />
      </div>
    );
  }

  if (job.isError) {
    return (
      <div className="flex flex-col gap-4">
        <Link href="/jobs" className="inline-block font-mono text-xs text-text-subtle hover:text-text">
          Back to jobs
        </Link>
        <ErrorState error={job.error} onRetry={() => job.refetch()} resourceLabel="this job" />
      </div>
    );
  }

  const detail = job.data;
  const company = companyLabel(detail);
  const avatar = avatarColors(detail.id);
  const postedDate = formatDate(detail.date_posted);
  const matched = detail.matched_skills ?? [];
  const missing = detail.missing_skills ?? [];
  const description = sanitizeJobDescription(detail.job_description);
  const sponsor = detail.sponsor;
  const statusChanged =
    detail.application_status !== "not_applied" ? timeAgo(detail.status_updated_at) : null;

  return (
    <div className="flex flex-col gap-4">
      <Link href="/jobs" className="mb-1 inline-block font-mono text-xs text-text-subtle hover:text-text">
        Back to jobs
      </Link>

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        {/* main column */}
        <div className="flex flex-col gap-4">
          <div className="border border-border border-t-2 border-t-accent bg-surface p-6">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
              <div className="flex min-w-0 flex-1 items-start gap-4">
                <div
                  className="grid h-12 w-12 flex-none place-items-center rounded-sm font-mono text-base font-semibold"
                  style={{ backgroundColor: avatar.bg, color: avatar.color }}
                >
                  {initials(company)}
                </div>
                <div className="min-w-0 flex-1">
                  <h1 className="text-xl font-bold leading-tight tracking-tight text-text">
                    {detail.job_title}
                  </h1>
                  <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-text-muted">
                    <span>{company}</span>
                    {detail.department && (
                      <span className="border border-border px-1.5 py-px font-mono text-2xs uppercase tracking-[0.04em] text-text-muted">
                        {detail.department}
                      </span>
                    )}
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-text-subtle">
                    <span>{detail.locations.length > 0 ? detail.locations.join(" / ") : "Location not listed"}</span>
                    {detail.employment_type && (
                      <span className="border border-border px-1.5 py-px font-mono text-2xs uppercase tracking-[0.04em] text-text-muted">
                        {detail.employment_type}
                      </span>
                    )}
                    {postedDate && <span>Posted {postedDate}</span>}
                  </div>
                </div>
              </div>
              <div className="flex-none self-start sm:self-auto">
                <ScoreIndicator score={detail.match_score} size="lg" provisional={detail.score_basis === "partial"} />
              </div>
            </div>

            <div className="mt-5 flex flex-wrap items-center gap-2.5">
              <a
                href={detail.job_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 bg-accent px-5 py-2.5 text-sm font-semibold text-white hover:bg-accent-hover"
              >
                Apply
              </a>
              <StatusControl jobId={detail.id} status={detail.application_status} />
              {statusChanged && (
                <span className="font-mono text-2xs text-text-faintest">changed {statusChanged}</span>
              )}
            </div>

            <div className="mt-4 border-t border-divider pt-4">
              <h3 className="mb-2 text-sm font-semibold text-text">Notes</h3>
              <NotesEditor jobId={detail.id} notes={detail.application_notes} rows={3} />
            </div>
          </div>

          {/* matched skills first, missing second */}
          <div className="border border-border bg-surface p-6">
            <div className="mb-1 flex items-center gap-2">
              <span className="h-2 w-2" style={{ backgroundColor: "var(--color-good)" }} />
              <h2 className="text-base font-semibold text-text">Your matching skills</h2>
              <span
                className="border px-1.5 py-0.5 font-mono text-2xs"
                style={{
                  backgroundColor: "var(--color-good-bg)",
                  color: "var(--color-good)",
                  borderColor: "var(--color-good-border)",
                }}
              >
                {matched.length} matched
              </span>
            </div>
            <p className="mb-3.5 text-sm text-text-subtle">
              Pulled from your active resume — skills this role asks for that you already demonstrate.
            </p>
            {matched.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {matched.map((sk, i) => (
                  <span
                    key={`${sk}-${i}`}
                    className="inline-flex items-center gap-1.5 border px-2 py-1 font-mono text-sm"
                    style={{
                      backgroundColor: "var(--color-good-bg)",
                      color: "var(--color-good)",
                      borderColor: "var(--color-good-border)",
                    }}
                  >
                    {sk}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-sm text-text-faintest">Not analyzed yet.</p>
            )}

            <div className="mt-5 border-t border-divider pt-4">
              <div className="mb-1 flex items-center gap-2">
                <span className="h-2 w-2 bg-border-strong" />
                <h3 className="text-sm font-semibold text-text-muted">Skills to develop</h3>
                <span className="font-mono text-xs text-text-faintest">{missing.length} gap</span>
              </div>
              <p className="mb-3 text-sm text-text-faintest">
                Mentioned in the posting but not evident on your resume.
              </p>
              {missing.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {missing.map((sk, i) => (
                    <span
                      key={`${sk}-${i}`}
                      className="border border-dashed border-border-strong bg-surface-alt px-2 py-1 font-mono text-xs text-text-faint"
                    >
                      {sk}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-text-faintest">No gaps identified.</p>
              )}
            </div>
          </div>

          {!isDemoMode() && <JobAssistantPanel detail={detail} />}

          {description && (
            <div className="border border-border bg-surface p-6">
              <h2 className="mb-3 text-base font-semibold text-text">About the role</h2>
              <div
                className="rich-text text-sm leading-relaxed text-text-secondary"
                dangerouslySetInnerHTML={{ __html: description }}
              />
            </div>
          )}
        </div>

        {/* sidebar */}
        <div className="flex flex-col gap-4 lg:sticky lg:top-[70px]">
          <div className="border border-border bg-surface p-5">
            <h3 className="mb-3 text-sm font-semibold text-text">Company</h3>
            <div className="flex flex-col text-sm">
              <Row label="Name">
                <span className="font-semibold text-text">{company}</span>
              </Row>
              <Row label="Department">
                <span className="flex flex-col items-end">
                  <span className="font-medium text-text">{detail.department_category ?? "--"}</span>
                  {detail.department && detail.department !== detail.department_category && (
                    <span className="text-xs text-text-faint">{detail.department}</span>
                  )}
                </span>
              </Row>
              <Row label="Salary est.">
                <span className="font-mono font-semibold text-text">
                  {detail.salary_estimate ? formatWage(detail.salary_estimate.amount) : "--"}
                </span>
              </Row>
              <Row label="ATS">
                <span className="font-mono text-xs text-text">{detail.ats_platform ?? "--"}</span>
              </Row>
            </div>
            {detail.salary_estimate && (
              <p className="mt-3 border-t border-divider pt-3 text-xs leading-relaxed text-text-faintest">
                {detail.salary_estimate.basis}
              </p>
            )}
          </div>

          {detail.company_research && <CompanyResearchCard research={detail.company_research} />}

          <div
            className="border border-l-2 p-5"
            style={
              sponsor
                ? { backgroundColor: "var(--color-good-bg)", borderColor: "var(--color-good-border)", borderLeftColor: "var(--color-good)" }
                : { backgroundColor: "var(--color-surface-alt)", borderColor: "var(--color-border)", borderLeftColor: "var(--color-border-strong)" }
            }
          >
            <div className="mb-3 flex items-center gap-2">
              <span
                className="h-2 w-2"
                style={{ backgroundColor: sponsor ? "var(--color-good)" : "var(--color-text-faintest)" }}
              />
              <h3 className="text-sm font-semibold text-text">H-1B sponsorship</h3>
            </div>
            {sponsor ? (
              <>
                <div className="flex flex-col text-sm">
                  <Row label={`LCAs filed (${sponsor.most_recent_fiscal_year})`} tone="good">
                    <span className="font-mono font-bold text-text">
                      {sponsor.total_lcas_most_recent_fiscal_year}
                    </span>
                  </Row>
                  <Row label="Median wage" tone="good">
                    <span className="font-mono font-semibold text-text">
                      {sponsor.median_wage !== null ? formatWage(sponsor.median_wage) : "--"}
                    </span>
                  </Row>
                  <Row label="Top title" tone="good">
                    <span className="text-right font-medium text-text">
                      {sponsor.most_frequent_job_title ?? "--"}
                    </span>
                  </Row>
                  <Row label="Latest status" tone="good">
                    <span className="font-mono text-xs" style={{ color: "var(--color-good)" }}>
                      {sponsor.latest_case_status ?? "--"}
                    </span>
                  </Row>
                </div>
                <p className="mt-3 font-mono text-2xs leading-relaxed text-text-faint">
                  Source: DOL LCA disclosure data.
                </p>
              </>
            ) : (
              <p className="text-sm leading-relaxed text-text-subtle">
                No H-1B LCA disclosures found for this employer in recent DOL data. Sponsorship isn&apos;t
                guaranteed either way — confirm with the recruiter.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function CompanyResearchCard({ research }: { research: CompanyResearch }) {
  return (
    <div className="border border-border bg-surface p-5">
      <h3 className="mb-3 text-sm font-semibold text-text">Company research</h3>
      {research.summary && (
        <p className="mb-3 text-sm leading-relaxed text-text-secondary">{research.summary}</p>
      )}
      {research.funding_signal && (
        <div className="mb-2.5 border-t border-divider pt-2.5">
          <p className="mb-1 font-mono text-2xs uppercase tracking-[0.04em] text-text-faint">Funding</p>
          <p className="text-xs leading-relaxed text-text-muted">{research.funding_signal}</p>
        </div>
      )}
      {research.hiring_signal && (
        <div className="mb-2.5 border-t border-divider pt-2.5">
          <p className="mb-1 font-mono text-2xs uppercase tracking-[0.04em] text-text-faint">Hiring</p>
          <p className="text-xs leading-relaxed text-text-muted">{research.hiring_signal}</p>
        </div>
      )}
      {research.recent_news.length > 0 && (
        <div className="border-t border-divider pt-2.5">
          <p className="mb-1.5 font-mono text-2xs uppercase tracking-[0.04em] text-text-faint">Recent news</p>
          <ul className="flex flex-col gap-1.5">
            {research.recent_news.slice(0, 3).map((item) => (
              <li key={item.url}>
                <a
                  href={item.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-accent hover:underline"
                >
                  {item.title}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="mt-3 font-mono text-2xs leading-relaxed text-text-faintest">Source: Tavily search, not verified.</p>
    </div>
  );
}

function Row({
  label,
  children,
  tone,
}: {
  label: string;
  children: React.ReactNode;
  tone?: "good";
}) {
  return (
    <div className="flex justify-between gap-3 border-b border-divider py-1.5 last:border-b-0">
      <span className={tone === "good" ? "text-text-muted" : "text-text-subtle"}>{label}</span>
      {children}
    </div>
  );
}

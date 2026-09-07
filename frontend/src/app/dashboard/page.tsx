"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { getDashboardStats } from "@/lib/api";
import { ErrorState } from "@/components/ErrorState";
import { STATUS_META, STATUS_ORDER } from "@/lib/theme";

const STAT_CARDS = [
  { key: "total_jobs" as const, label: "Jobs tracked", sub: "total postings scraped", dot: "#e0533d" },
  { key: "total_companies" as const, label: "Companies", sub: "with tracked postings", dot: "#3f7fca" },
  { key: "new_jobs_last_7_days" as const, label: "New this week", sub: "scraped in the last 7 days", dot: "#1f9d55" },
];

export default function DashboardPage() {
  const stats = useQuery({
    queryKey: ["dashboard-stats"],
    queryFn: getDashboardStats,
  });

  const today = new Date().toLocaleDateString(undefined, {
    weekday: "long",
    month: "short",
    day: "numeric",
  });

  return (
    <main className="flex flex-1 flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="mb-1 font-mono text-[11px] uppercase tracking-[1.5px] text-text-faintest">Overview</div>
          <h1 className="text-2xl font-semibold tracking-tight text-text sm:text-[26px]">Dashboard</h1>
        </div>
        <div className="font-mono text-xs text-text-subtle">{today}</div>
      </div>

      {stats.isPending && (
        <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-[92px] animate-pulse rounded-xl border border-border bg-surface-alt" />
          ))}
        </div>
      )}

      {stats.isError && (
        <ErrorState
          error={stats.error}
          onRetry={() => stats.refetch()}
          resourceLabel="the dashboard"
        />
      )}

      {stats.isSuccess && (
        <>
          <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-3">
            {STAT_CARDS.map((card) => (
              <div key={card.key} className="rounded-xl border border-border bg-surface p-4">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[11px] uppercase tracking-wide text-text-faintest">
                    {card.label}
                  </span>
                  <span className="h-2 w-2 rounded-full" style={{ backgroundColor: card.dot }} />
                </div>
                <div className="mt-2 font-mono text-[34px] font-bold leading-none text-text">
                  {stats.data[card.key]}
                </div>
                <div className="mt-1 text-xs text-text-subtle">{card.sub}</div>
              </div>
            ))}
          </div>

          <div className="rounded-xl border border-border bg-surface p-5">
            <div className="mb-4 flex items-baseline justify-between">
              <h2 className="text-[15px] font-semibold text-text">Applications by status</h2>
              <Link href="/applications" className="font-mono text-[11px] text-accent hover:text-accent-hover">
                Open tracker →
              </Link>
            </div>

            {stats.data.total_jobs === 0 ? (
              <p className="text-[13px] text-text-faintest">No jobs tracked yet.</p>
            ) : (
              <>
                <div className="mb-4 flex h-3 overflow-hidden rounded-md bg-divider">
                  {STATUS_ORDER.map((status) => {
                    const count = stats.data.applications_by_status[status];
                    const pct = (count / stats.data.total_jobs) * 100;
                    if (pct === 0) return null;
                    return (
                      <div
                        key={status}
                        style={{ width: `${pct}%`, backgroundColor: STATUS_META[status].dot }}
                      />
                    );
                  })}
                </div>
                <div className="flex flex-col gap-0.5">
                  {STATUS_ORDER.map((status) => (
                    <div key={status} className="flex items-center gap-2.5 border-b border-divider py-1.5 last:border-b-0">
                      <span className="h-[9px] w-[9px] flex-none rounded-full" style={{ backgroundColor: STATUS_META[status].dot }} />
                      <span className="flex-1 text-[13px] text-text">{STATUS_META[status].label}</span>
                      <span className="font-mono text-sm font-semibold text-text">
                        {stats.data.applications_by_status[status]}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </>
      )}
    </main>
  );
}

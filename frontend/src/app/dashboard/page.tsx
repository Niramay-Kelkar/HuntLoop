"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { getDashboardStats } from "@/lib/api";
import { ErrorState } from "@/components/ErrorState";
import { STATUS_META, STATUS_ORDER } from "@/lib/theme";

const STAT_CARDS = [
  { key: "total_jobs" as const, label: "Jobs tracked", sub: "total postings scraped", dot: "#1b4965" },
  { key: "total_companies" as const, label: "Companies", sub: "with tracked postings", dot: "#5b6570" },
  { key: "new_jobs_last_7_days" as const, label: "New this week", sub: "scraped in the last 7 days", dot: "#2f7d4f" },
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
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-border pb-4">
        <h1 className="text-xl font-bold tracking-tight text-text">Dashboard</h1>
        <div className="font-mono text-xs text-text-subtle">{today}</div>
      </div>

      {stats.isPending && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-[92px] animate-pulse border border-border bg-surface-alt" />
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
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {STAT_CARDS.map((card) => (
              <div key={card.key} className="border border-border border-t-2 border-t-accent bg-surface p-4">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs uppercase tracking-[0.08em] text-text-faint">
                    {card.label}
                  </span>
                  <span className="h-2 w-2" style={{ backgroundColor: card.dot }} />
                </div>
                <div className="mt-2 font-mono text-3xl font-medium leading-none text-text">
                  {stats.data[card.key].toLocaleString()}
                </div>
                <div className="mt-1.5 text-sm text-text-subtle">{card.sub}</div>
              </div>
            ))}
          </div>

          <div className="border border-border bg-surface p-5">
            <div className="mb-4 flex items-baseline justify-between border-b border-divider pb-3">
              <h2 className="text-base font-semibold text-text">Applications by status</h2>
              <Link
                href="/applications"
                className="font-mono text-xs uppercase tracking-[0.04em] text-accent hover:text-accent-hover"
              >
                View tracker
              </Link>
            </div>

            {stats.data.total_jobs === 0 ? (
              <p className="text-sm text-text-faintest">No jobs tracked yet.</p>
            ) : (
              <>
                <div className="mb-4 flex h-2.5 overflow-hidden border border-border bg-surface-alt">
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
                <div className="flex flex-col">
                  {STATUS_ORDER.map((status) => (
                    <div key={status} className="flex items-center gap-2.5 border-b border-divider py-2 last:border-b-0">
                      <span className="h-2 w-2 flex-none" style={{ backgroundColor: STATUS_META[status].dot }} />
                      <span className="flex-1 text-sm text-text">{STATUS_META[status].label}</span>
                      <span className="font-mono text-sm font-semibold text-text">
                        {stats.data.applications_by_status[status].toLocaleString()}
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

"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { getJobs } from "@/lib/api";
import type { JobListResponse } from "@/types/api";
import { ApplicationsList } from "@/components/ApplicationsList";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { KanbanBoard } from "@/components/KanbanBoard";
import { SegmentedToggle } from "@/components/SegmentedToggle";

// The tracker only needs postings the user has actually tracked, which
// `GET /jobs?tracked=true` returns directly (a small set) - it no longer
// pages through the entire jobs table client-side. A defensive loop still
// handles the unlikely case of more than one page of tracked
// applications. Returns the same JobListResponse shape as a single-page
// fetch so useApplicationStatusMutation's `old.items.map(...)` cache
// updater works on this query's cache entry too.
const PAGE_LIMIT = 100;
const NO_ITEMS: JobListResponse["items"] = [];

async function getTrackedJobs(): Promise<JobListResponse> {
  const first = await getJobs({ tracked: true, limit: PAGE_LIMIT, offset: 0, sort: "-score" });
  const items = [...first.items];
  let offset = PAGE_LIMIT;
  while (offset < first.total) {
    const page = await getJobs({ tracked: true, limit: PAGE_LIMIT, offset, sort: "-score" });
    items.push(...page.items);
    offset += PAGE_LIMIT;
  }
  return { items, total: first.total, limit: items.length, offset: 0 };
}

export default function ApplicationsPage() {
  const [mode, setMode] = useState<"kanban" | "list">("kanban");
  const [search, setSearch] = useState("");

  const jobs = useQuery({
    queryKey: ["jobs", { tracker: true }],
    queryFn: getTrackedJobs,
  });

  const allItems = jobs.data?.items ?? NO_ITEMS;
  const query = search.trim().toLowerCase();
  const filtered = useMemo(
    () =>
      query === ""
        ? allItems
        : allItems.filter(
            (j) =>
              j.job_title.toLowerCase().includes(query) ||
              j.company_name.toLowerCase().includes(query),
          ),
    [allItems, query],
  );

  return (
    <main className="flex flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-text">Applications</h1>
          <p className="mt-1 text-sm text-text-subtle">
            Every job you&apos;ve set a status on. Drag cards between columns to update status.
          </p>
        </div>
        <SegmentedToggle
          value={mode}
          onChange={setMode}
          options={[
            { value: "kanban", label: "Board" },
            { value: "list", label: "List" },
          ]}
        />
      </div>

      {jobs.isPending && (
        <div className="h-64 animate-pulse border border-border bg-surface-alt" />
      )}

      {jobs.isError && (
        <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} resourceLabel="your applications" />
      )}

      {jobs.isSuccess && allItems.length === 0 && (
        <EmptyState
          icon="✦"
          title="No applications tracked yet"
          description="Set a status on any job from the Jobs list or a job's detail page and it shows up here."
        />
      )}

      {jobs.isSuccess && allItems.length > 0 && (
        <>
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search your applications by role or company…"
            aria-label="Search applications"
            className="w-full max-w-sm border border-border-strong bg-surface px-3 py-1.5 text-sm text-text placeholder:text-text-faintest focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          />
          {filtered.length === 0 ? (
            <EmptyState
              icon="⌕"
              title="No applications match your search"
              description="Try a different role or company name, or clear the search box."
              compact
            />
          ) : mode === "kanban" ? (
            <KanbanBoard jobs={filtered} />
          ) : (
            <ApplicationsList jobs={filtered} />
          )}
        </>
      )}
    </main>
  );
}

"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getJobs } from "@/lib/api";
import type { JobListResponse } from "@/types/api";
import { ApplicationsList } from "@/components/ApplicationsList";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { KanbanBoard } from "@/components/KanbanBoard";
import { SegmentedToggle } from "@/components/SegmentedToggle";

// The real API has no server-side status filter (see CLAUDE.md's `GET
// /jobs` params: company/min_score/sort/limit/offset only) and caps
// `limit` at 100 - the tracker pages through every job with that cap and
// groups/filters by status client-side, same as design/HuntLoop.dc.html's
// own `all`/`trackerList`/`kanbanColumns` derivation. Returns the same
// JobListResponse shape as a single-page fetch (not a bare array) so
// useApplicationStatusMutation's generic `old.items.map(...)` cache
// updater works on this query's cache entry too.
const PAGE_LIMIT = 100;

async function getAllJobs(): Promise<JobListResponse> {
  const first = await getJobs({ limit: PAGE_LIMIT, offset: 0 });
  const items = [...first.items];
  let offset = PAGE_LIMIT;
  while (offset < first.total) {
    const page = await getJobs({ limit: PAGE_LIMIT, offset });
    items.push(...page.items);
    offset += PAGE_LIMIT;
  }
  return { items, total: first.total, limit: items.length, offset: 0 };
}

export default function ApplicationsPage() {
  const [mode, setMode] = useState<"kanban" | "list">("kanban");

  const jobs = useQuery({
    queryKey: ["jobs", { tracker: true }],
    queryFn: getAllJobs,
  });

  return (
    <main className="flex flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-text">Applications</h1>
          <p className="mt-0.5 text-[13px] text-text-subtle">
            Track everything you&apos;ve engaged with. Drag cards between columns to update status.
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
        <div className="h-64 animate-pulse rounded-xl border border-border bg-surface-alt" />
      )}

      {jobs.isError && (
        <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} resourceLabel="your applications" />
      )}

      {jobs.isSuccess && jobs.data.items.length === 0 && (
        <EmptyState
          icon="✦"
          title="Nothing to track yet"
          description="Once postings are scraped they show up here. Set a status on any job from the Jobs list and it moves onto this board."
        />
      )}

      {jobs.isSuccess &&
        jobs.data.items.length > 0 &&
        (mode === "kanban" ? <KanbanBoard jobs={jobs.data.items} /> : <ApplicationsList jobs={jobs.data.items} />)}
    </main>
  );
}

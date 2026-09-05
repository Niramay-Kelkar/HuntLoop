"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getJobs } from "@/lib/api";
import { JobCard } from "@/components/JobCard";
import { JobFilters, type JobFiltersValue } from "@/components/JobFilters";
import { JobTable } from "@/components/JobTable";
import { Pagination } from "@/components/Pagination";
import { SegmentedToggle } from "@/components/SegmentedToggle";

const PAGE_SIZE = 12;

export default function JobsPage() {
  const [filters, setFilters] = useState<JobFiltersValue>({
    company: "",
    department: "",
    employmentType: "",
    minScore: "",
    sort: "-score",
  });
  const [offset, setOffset] = useState(0);
  const [viewMode, setViewMode] = useState<"cards" | "table">("cards");

  const parsedMinScore = filters.minScore === "" ? undefined : Number(filters.minScore);

  const jobs = useQuery({
    queryKey: [
      "jobs",
      {
        company: filters.company,
        department: filters.department,
        employmentType: filters.employmentType,
        minScore: parsedMinScore,
        sort: filters.sort,
        offset,
      },
    ],
    queryFn: () =>
      getJobs({
        company: filters.company || undefined,
        department: filters.department || undefined,
        employment_type: filters.employmentType || undefined,
        min_score: parsedMinScore,
        sort: filters.sort,
        limit: PAGE_SIZE,
        offset,
      }),
    placeholderData: keepPreviousData,
  });

  function handleFiltersChange(next: JobFiltersValue) {
    setFilters(next);
    setOffset(0); // any filter/sort change resets to page 1
  }

  return (
    <main className="flex flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-text">Jobs</h1>
          <p className="mt-0.5 text-[13px] text-text-subtle">
            {jobs.isSuccess ? (
              <>
                <span className="font-mono font-semibold text-text">{jobs.data.total}</span> postings · scored
                against your active resume
              </>
            ) : (
              "Scored against your active resume"
            )}
          </p>
        </div>
        <SegmentedToggle
          value={viewMode}
          onChange={setViewMode}
          options={[
            { value: "cards", label: "Cards" },
            { value: "table", label: "Table" },
          ]}
        />
      </div>

      <JobFilters value={filters} onChange={handleFiltersChange} />

      {jobs.isPending && (
        <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-40 animate-pulse rounded-xl border border-border bg-surface-alt" />
          ))}
        </div>
      )}

      {jobs.isError && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          Failed to load jobs: {jobs.error.message}
        </div>
      )}

      {jobs.isSuccess && jobs.data.items.length === 0 && (
        <div className="rounded-xl border border-dashed border-border-strong p-10 text-center text-sm text-text-faintest">
          No jobs match these filters. Try lowering the minimum score or clearing the company filter.
        </div>
      )}

      {jobs.isSuccess && jobs.data.items.length > 0 && (
        <>
          {viewMode === "cards" ? (
            <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
              {jobs.data.items.map((job) => (
                <JobCard key={job.id} job={job} />
              ))}
            </div>
          ) : (
            <JobTable jobs={jobs.data.items} />
          )}
          <Pagination total={jobs.data.total} limit={jobs.data.limit} offset={jobs.data.offset} onOffsetChange={setOffset} />
        </>
      )}
    </main>
  );
}

"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getJobs } from "@/lib/api";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
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
    location: "",
    minScore: "",
    salaryMin: "",
    salaryMax: "",
    salaryUnspecified: false,
    sort: "-score",
  });
  const [offset, setOffset] = useState(0);
  const [viewMode, setViewMode] = useState<"cards" | "table">("cards");

  const parsedMinScore = filters.minScore === "" ? undefined : Number(filters.minScore);
  const parsedSalaryMin =
    filters.salaryUnspecified || filters.salaryMin === "" ? undefined : Number(filters.salaryMin);
  const parsedSalaryMax =
    filters.salaryUnspecified || filters.salaryMax === "" ? undefined : Number(filters.salaryMax);

  const jobs = useQuery({
    queryKey: [
      "jobs",
      {
        company: filters.company,
        department: filters.department,
        employmentType: filters.employmentType,
        location: filters.location,
        minScore: parsedMinScore,
        salaryMin: parsedSalaryMin,
        salaryMax: parsedSalaryMax,
        salaryUnspecified: filters.salaryUnspecified,
        sort: filters.sort,
        offset,
      },
    ],
    queryFn: () =>
      getJobs({
        company: filters.company || undefined,
        department: filters.department || undefined,
        employment_type: filters.employmentType || undefined,
        location: filters.location || undefined,
        min_score: parsedMinScore,
        salary_min: parsedSalaryMin,
        salary_max: parsedSalaryMax,
        salary_unspecified: filters.salaryUnspecified || undefined,
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

      <JobFilters
        value={filters}
        onChange={handleFiltersChange}
        resultCount={jobs.data?.total}
        isLoading={jobs.isPending}
      />

      {jobs.isPending && (
        <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-40 animate-pulse rounded-xl border border-border bg-surface-alt" />
          ))}
        </div>
      )}

      {jobs.isError && (
        <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} resourceLabel="jobs" />
      )}

      {jobs.isSuccess && jobs.data.items.length === 0 && (
        <EmptyState
          icon="⌕"
          title="No postings match your filters"
          description="Try lowering the minimum match score, clearing the company search, or widening the location and salary filters."
        />
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

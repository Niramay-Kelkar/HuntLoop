"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { getJobs } from "@/lib/api";
import { JobCard } from "@/components/JobCard";
import { JobFilters, type JobFiltersValue } from "@/components/JobFilters";
import { Pagination } from "@/components/Pagination";

const PAGE_SIZE = 12;

export default function Home() {
  const [filters, setFilters] = useState<JobFiltersValue>({
    company: "",
    minScore: "",
    sort: "-score",
  });
  const [offset, setOffset] = useState(0);

  const parsedMinScore = filters.minScore === "" ? undefined : Number(filters.minScore);

  const jobs = useQuery({
    queryKey: ["jobs", { company: filters.company, minScore: parsedMinScore, sort: filters.sort, offset }],
    queryFn: () =>
      getJobs({
        company: filters.company || undefined,
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
    <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 p-6 sm:p-8">
      <header>
        <h1 className="text-2xl font-bold text-neutral-900 dark:text-neutral-50">HuntLoop</h1>
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          Scraped job postings, ranked against your active resume.
        </p>
      </header>

      <JobFilters value={filters} onChange={handleFiltersChange} />

      {jobs.isPending && (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="h-32 animate-pulse rounded-xl border border-neutral-200 bg-neutral-100 dark:border-neutral-800 dark:bg-neutral-900"
            />
          ))}
        </div>
      )}

      {jobs.isError && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          Failed to load jobs: {jobs.error.message}
        </div>
      )}

      {jobs.isSuccess && jobs.data.items.length === 0 && (
        <div className="rounded-xl border border-dashed border-neutral-300 p-10 text-center text-sm text-neutral-500 dark:border-neutral-700 dark:text-neutral-400">
          No jobs match these filters. Try lowering the minimum score or clearing the company filter.
        </div>
      )}

      {jobs.isSuccess && jobs.data.items.length > 0 && (
        <>
          <div className="flex flex-col gap-3">
            {jobs.data.items.map((job) => (
              <JobCard key={job.id} job={job} />
            ))}
          </div>
          <Pagination total={jobs.data.total} limit={jobs.data.limit} offset={jobs.data.offset} onOffsetChange={setOffset} />
        </>
      )}
    </main>
  );
}

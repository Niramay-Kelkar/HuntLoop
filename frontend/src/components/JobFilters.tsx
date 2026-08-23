"use client";

import type { ListJobsParams } from "@/lib/api";

export interface JobFiltersValue {
  company: string;
  minScore: string; // kept as a raw string while editing; parsed by the caller
  sort: NonNullable<ListJobsParams["sort"]>;
}

export function JobFilters({
  value,
  onChange,
}: {
  value: JobFiltersValue;
  onChange: (value: JobFiltersValue) => void;
}) {
  return (
    <div className="flex flex-wrap items-end gap-4 rounded-xl border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium text-neutral-500 dark:text-neutral-400">Company</span>
        <input
          type="text"
          placeholder="e.g. palantir"
          value={value.company}
          onChange={(e) => onChange({ ...value, company: e.target.value })}
          className="w-40 rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm text-neutral-900 placeholder:text-neutral-400 focus:border-blue-500 focus:outline-none dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-100"
        />
      </label>

      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium text-neutral-500 dark:text-neutral-400">Min. match score</span>
        <input
          type="number"
          min={0}
          max={1}
          step={0.05}
          placeholder="0.0 - 1.0"
          value={value.minScore}
          onChange={(e) => onChange({ ...value, minScore: e.target.value })}
          className="w-32 rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm text-neutral-900 placeholder:text-neutral-400 focus:border-blue-500 focus:outline-none dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-100"
        />
      </label>

      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium text-neutral-500 dark:text-neutral-400">Sort by score</span>
        <select
          value={value.sort}
          onChange={(e) => onChange({ ...value, sort: e.target.value as JobFiltersValue["sort"] })}
          className="rounded-lg border border-neutral-300 bg-white px-3 py-1.5 text-sm text-neutral-900 focus:border-blue-500 focus:outline-none dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-100"
        >
          <option value="-score">Best match first</option>
          <option value="score">Worst match first</option>
        </select>
      </label>

      {(value.company || value.minScore) && (
        <button
          type="button"
          onClick={() => onChange({ company: "", minScore: "", sort: value.sort })}
          className="rounded-lg px-3 py-1.5 text-sm font-medium text-neutral-500 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100"
        >
          Clear filters
        </button>
      )}
    </div>
  );
}

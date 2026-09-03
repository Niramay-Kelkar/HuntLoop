"use client";

import { useQuery } from "@tanstack/react-query";

import { getDepartments, type ListJobsParams } from "@/lib/api";
import { UNSPECIFIED_DEPARTMENT } from "@/types/api";

export interface JobFiltersValue {
  company: string;
  department: string; // "" = unset (no filter); UNSPECIFIED_DEPARTMENT = "no department set"; otherwise a real value
  minScore: string; // kept as a raw string while editing; parsed by the caller
  sort: NonNullable<ListJobsParams["sort"]>;
}

/**
 * Only the filters the real API supports (company text match, department,
 * min score, sort) get built here - the mockup also shows a
 * location/radius select, which is blocked pending backend work (see
 * CLAUDE.md), so it's deliberately left out rather than added as
 * non-functional UI.
 *
 * Department options are populated from GET /jobs/departments (the real
 * distinct values in the data), not a hardcoded list - department is
 * free text from each ATS source and varies in casing/wording. Leaving
 * the department filter unset returns postings regardless of department,
 * including ones with none set (NULL) - filtering is additive/optional,
 * never silently exclusionary. Selecting "Not specified" explicitly
 * filters down to only the NULL-department postings (see CLAUDE.md for
 * the full reasoning).
 */
export function JobFilters({
  value,
  onChange,
}: {
  value: JobFiltersValue;
  onChange: (value: JobFiltersValue) => void;
}) {
  const minScorePercent = value.minScore === "" ? 0 : Math.round(Number(value.minScore) * 100);
  const departments = useQuery({ queryKey: ["departments"], queryFn: getDepartments });

  return (
    <div className="rounded-xl border border-border bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2.5">
        <div className="relative min-w-[200px] flex-1">
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-faintest">⌕</span>
          <input
            type="text"
            placeholder="Search by company…"
            value={value.company}
            onChange={(e) => onChange({ ...value, company: e.target.value })}
            className="w-full rounded-lg border border-border-strong bg-surface-alt py-2.5 pl-8 pr-3 text-[13px] text-text focus:border-accent focus:outline-none"
          />
        </div>
        <div className="relative">
          <select
            value={value.department}
            onChange={(e) => onChange({ ...value, department: e.target.value })}
            className="appearance-none rounded-lg border border-border-strong bg-surface py-2 pl-3 pr-7 text-[13px] text-text"
          >
            <option value="">All departments</option>
            {departments.data?.map((department) => (
              <option key={department} value={department}>
                {department}
              </option>
            ))}
            <option value={UNSPECIFIED_DEPARTMENT}>Not specified</option>
          </select>
          <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-text-faintest">
            ▼
          </span>
        </div>
        <div className="relative">
          <select
            value={value.sort}
            onChange={(e) => onChange({ ...value, sort: e.target.value as JobFiltersValue["sort"] })}
            className="appearance-none rounded-lg border border-border-strong bg-surface py-2 pl-3 pr-7 text-[13px] text-text"
          >
            <option value="-score">Sort: Best match</option>
            <option value="score">Sort: Worst match</option>
          </select>
          <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-text-faintest">
            ▼
          </span>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-5 border-t border-divider pt-3">
        <div className="flex min-w-[230px] items-center gap-2.5">
          <span className="whitespace-nowrap font-mono text-[11px] uppercase tracking-wide text-text-faintest">
            Min match
          </span>
          <input
            type="range"
            min={0}
            max={95}
            step={5}
            value={minScorePercent}
            onChange={(e) => onChange({ ...value, minScore: String(Number(e.target.value) / 100) })}
            className="min-w-[110px] flex-1"
          />
          <span className="w-9 text-right font-mono text-[13px] font-semibold">{minScorePercent}%</span>
        </div>
        <div className="flex-1" />
        {(value.company || value.department || value.minScore) && (
          <button
            type="button"
            onClick={() => onChange({ company: "", department: "", minScore: "", sort: value.sort })}
            className="font-mono text-[11px] text-accent hover:text-accent-hover"
          >
            Clear filters ✕
          </button>
        )}
      </div>
    </div>
  );
}

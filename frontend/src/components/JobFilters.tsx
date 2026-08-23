"use client";

import type { ListJobsParams } from "@/lib/api";

export interface JobFiltersValue {
  company: string;
  minScore: string; // kept as a raw string while editing; parsed by the caller
  sort: NonNullable<ListJobsParams["sort"]>;
}

/**
 * Only the filters the real API supports (company text match, min score,
 * sort) get built here - the mockup also shows location/department/radius
 * selects, but those are blocked pending backend work (see CLAUDE.md), so
 * they're deliberately left out rather than added as non-functional UI.
 */
export function JobFilters({
  value,
  onChange,
}: {
  value: JobFiltersValue;
  onChange: (value: JobFiltersValue) => void;
}) {
  const minScorePercent = value.minScore === "" ? 0 : Math.round(Number(value.minScore) * 100);

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
        {(value.company || value.minScore) && (
          <button
            type="button"
            onClick={() => onChange({ company: "", minScore: "", sort: value.sort })}
            className="font-mono text-[11px] text-accent hover:text-accent-hover"
          >
            Clear filters ✕
          </button>
        )}
      </div>
    </div>
  );
}

"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getDepartments, getEmploymentTypes, getLocations, type ListJobsParams } from "@/lib/api";
import { UNSPECIFIED_DEPARTMENT, UNSPECIFIED_EMPLOYMENT_TYPE, UNSPECIFIED_LOCATION } from "@/types/api";
import { LocationMultiSelect } from "@/components/LocationMultiSelect";

export interface JobFiltersValue {
  company: string;
  department: string; // "" = unset (no filter); UNSPECIFIED_DEPARTMENT = "no category"; otherwise a canonical category
  employmentType: string; // "" = unset (no filter); UNSPECIFIED_EMPLOYMENT_TYPE = "none set"; otherwise a real value
  location: string[]; // [] = unset; UNSPECIFIED_LOCATION = "no location scraped"; otherwise canonical location groups (exact match, OR'd, see GET /jobs/locations)
  minScore: string; // kept as a raw string while editing; parsed by the caller
  salaryMin: string; // raw string while editing; parsed by the caller
  salaryMax: string; // raw string while editing; parsed by the caller
  salaryUnspecified: boolean; // true = only postings with no salary estimate (overrides salaryMin/salaryMax)
  sort: NonNullable<ListJobsParams["sort"]>;
}

const EMPTY_FILTERS: JobFiltersValue = {
  company: "",
  department: "",
  employmentType: "",
  location: [],
  minScore: "",
  salaryMin: "",
  salaryMax: "",
  salaryUnspecified: false,
  sort: "-score",
};

/** A shortened, human label for a value that may be a "not specified" sentinel. */
function labelFor(value: string, sentinel: string): string {
  return value === sentinel ? "Not specified" : value;
}

function formatMoney(raw: string): string {
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return raw;
  return n % 1000 === 0 ? `$${n / 1000}k` : `$${n.toLocaleString()}`;
}

/**
 * The active filters, as removable chips. Each chip clears exactly the
 * field(s) it represents and nothing else - sort is never a filter and is
 * never touched here. The salary range is one chip (min+max cleared
 * together); "no estimate" is its own separate chip.
 */
function activeChips(value: JobFiltersValue): { key: string; label: string; next: JobFiltersValue }[] {
  const chips: { key: string; label: string; next: JobFiltersValue }[] = [];
  if (value.company) {
    chips.push({ key: "company", label: `Company: ${value.company}`, next: { ...value, company: "" } });
  }
  if (value.department) {
    chips.push({
      key: "department",
      label: `Dept: ${labelFor(value.department, UNSPECIFIED_DEPARTMENT)}`,
      next: { ...value, department: "" },
    });
  }
  if (value.employmentType) {
    chips.push({
      key: "employmentType",
      label: `Type: ${labelFor(value.employmentType, UNSPECIFIED_EMPLOYMENT_TYPE)}`,
      next: { ...value, employmentType: "" },
    });
  }
  for (const loc of value.location) {
    chips.push({
      key: `location:${loc}`,
      label: `Location: ${labelFor(loc, UNSPECIFIED_LOCATION)}`,
      next: { ...value, location: value.location.filter((l) => l !== loc) },
    });
  }
  if (value.minScore) {
    chips.push({
      key: "minScore",
      label: `Min match: ${Math.round(Number(value.minScore) * 100)}%`,
      next: { ...value, minScore: "" },
    });
  }
  if (value.salaryUnspecified) {
    chips.push({
      key: "salaryUnspecified",
      label: "Salary: no estimate",
      next: { ...value, salaryUnspecified: false },
    });
  } else if (value.salaryMin || value.salaryMax) {
    const range =
      value.salaryMin && value.salaryMax
        ? `${formatMoney(value.salaryMin)}–${formatMoney(value.salaryMax)}`
        : value.salaryMin
          ? `≥ ${formatMoney(value.salaryMin)}`
          : `≤ ${formatMoney(value.salaryMax)}`;
    chips.push({
      key: "salary",
      label: `Est. salary: ${range}`,
      next: { ...value, salaryMin: "", salaryMax: "" },
    });
  }
  return chips;
}

/**
 * Only the filters the real API supports get built here: company text
 * match, department, employment type, location (exact match on the
 * canonical location groups, OR'd), min match score, estimated-salary
 * range, and sort. This component is purely a UI/UX layer over
 * `value`/`onChange` - it does not query the jobs list or shape API
 * params itself.
 *
 * Visual identity: Direction A, "Register" - the panel is drawn with
 * hairline rules, square-cornered ruled controls, and mono field
 * labels; the active filters read as square ledger tags.
 */
export function JobFilters({
  value,
  onChange,
  resultCount,
  isLoading = false,
}: {
  value: JobFiltersValue;
  onChange: (value: JobFiltersValue) => void;
  resultCount?: number;
  isLoading?: boolean;
}) {
  const [expanded, setExpanded] = useState(true);
  const minScorePercent = value.minScore === "" ? 0 : Math.round(Number(value.minScore) * 100);
  const departments = useQuery({ queryKey: ["departments"], queryFn: getDepartments });
  const employmentTypes = useQuery({ queryKey: ["employment-types"], queryFn: getEmploymentTypes });
  const locations = useQuery({ queryKey: ["locations"], queryFn: getLocations });

  const chips = activeChips(value);
  const anyFilterSet = chips.length > 0;

  const selectClass =
    "w-full appearance-none border border-border-strong bg-surface py-2 pl-3 pr-7 text-sm text-text focus:border-accent focus:outline-none sm:w-auto";

  return (
    <div className="border border-border bg-surface">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5">
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          aria-expanded={expanded}
          className="flex items-center gap-1.5 font-mono text-xs uppercase tracking-[0.1em] text-text-faint hover:text-text"
        >
          <span className="text-2xs">{expanded ? "▾" : "▸"}</span>
          Filters
          {anyFilterSet && (
            <span className="border border-accent px-1 py-px font-mono text-2xs font-semibold text-accent">
              {chips.length}
            </span>
          )}
        </button>

        <span className="font-mono text-xs text-text-faintest" aria-live="polite">
          {isLoading || resultCount === undefined
            ? "…"
            : `${resultCount.toLocaleString()} ${resultCount === 1 ? "result" : "results"}`}
        </span>

        <div className="flex-1" />

        {anyFilterSet && (
          <button
            type="button"
            onClick={() => onChange({ ...EMPTY_FILTERS, sort: value.sort })}
            className="font-mono text-xs uppercase tracking-[0.04em] text-accent hover:text-accent-hover"
          >
            Clear all
          </button>
        )}
      </div>

      {anyFilterSet && (
        <div className="flex flex-wrap gap-1.5 border-t border-divider px-4 py-2.5">
          {chips.map((chip) => (
            <button
              key={chip.key}
              type="button"
              onClick={() => onChange(chip.next)}
              aria-label={`Remove filter ${chip.label}`}
              className="flex items-center gap-1.5 border border-border-strong bg-surface-alt py-1 pl-2 pr-1.5 font-mono text-xs text-text-subtle hover:border-accent hover:text-text"
            >
              {chip.label}
              <span className="text-text-faintest">✕</span>
            </button>
          ))}
        </div>
      )}

      {expanded && (
        <div className="border-t border-divider p-4">
          <div className="flex flex-wrap items-center gap-2.5">
            <div className="relative min-w-[200px] flex-1">
              <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-faintest">⌕</span>
              <input
                type="text"
                placeholder="Search by company…"
                value={value.company}
                onChange={(e) => onChange({ ...value, company: e.target.value })}
                className="w-full border border-border-strong bg-surface-alt py-2.5 pl-8 pr-3 text-sm text-text focus:border-accent focus:outline-none"
              />
            </div>
            <div className="relative w-full sm:w-auto">
              <select
                value={value.department}
                onChange={(e) => onChange({ ...value, department: e.target.value })}
                className={selectClass}
              >
                <option value="">All departments</option>
                {departments.data?.map((department) => (
                  <option key={department} value={department}>
                    {department}
                  </option>
                ))}
                <option value={UNSPECIFIED_DEPARTMENT}>Not specified</option>
              </select>
              <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-2xs text-text-faintest">
                ▾
              </span>
            </div>
            <div className="relative w-full sm:w-auto">
              <select
                value={value.employmentType}
                onChange={(e) => onChange({ ...value, employmentType: e.target.value })}
                className={selectClass}
              >
                <option value="">All employment types</option>
                {employmentTypes.data?.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
                <option value={UNSPECIFIED_EMPLOYMENT_TYPE}>Not specified</option>
              </select>
              <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-2xs text-text-faintest">
                ▾
              </span>
            </div>
            <LocationMultiSelect
              groups={locations.data}
              selected={value.location}
              onChange={(next) => onChange({ ...value, location: next })}
            />
            <div className="relative w-full sm:w-auto">
              <select
                value={value.sort}
                onChange={(e) => onChange({ ...value, sort: e.target.value as JobFiltersValue["sort"] })}
                className={selectClass}
              >
                <option value="-score">Sort: Best match</option>
                <option value="-date">Sort: Most recent</option>
                <option value="-salary">Sort: Highest salary</option>
              </select>
              <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-2xs text-text-faintest">
                ▾
              </span>
            </div>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-3 border-t border-divider pt-3">
            <div className="flex min-w-[230px] items-center gap-2.5">
              <span className="whitespace-nowrap font-mono text-xs uppercase tracking-[0.08em] text-text-faint">
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
              <span className="w-9 text-right font-mono text-sm font-semibold">{minScorePercent}%</span>
            </div>
            <div className="flex flex-wrap items-center gap-2.5">
              <span className="whitespace-nowrap font-mono text-xs uppercase tracking-[0.08em] text-text-faint">
                Est. salary
              </span>
              <input
                type="number"
                min={0}
                step={10000}
                placeholder="min"
                value={value.salaryMin}
                disabled={value.salaryUnspecified}
                onChange={(e) => onChange({ ...value, salaryMin: e.target.value })}
                className="w-24 border border-border-strong bg-surface-alt py-1.5 px-2 text-sm text-text focus:border-accent focus:outline-none disabled:opacity-40"
              />
              <span className="text-text-faintest">–</span>
              <input
                type="number"
                min={0}
                step={10000}
                placeholder="max"
                value={value.salaryMax}
                disabled={value.salaryUnspecified}
                onChange={(e) => onChange({ ...value, salaryMax: e.target.value })}
                className="w-24 border border-border-strong bg-surface-alt py-1.5 px-2 text-sm text-text focus:border-accent focus:outline-none disabled:opacity-40"
              />
              <label className="flex items-center gap-1.5 text-sm text-text-subtle">
                <input
                  type="checkbox"
                  checked={value.salaryUnspecified}
                  onChange={(e) => onChange({ ...value, salaryUnspecified: e.target.checked })}
                />
                No estimate
              </label>
            </div>
            <span className="font-mono text-2xs text-text-faintest">
              Salary is an employer-level estimate from DOL filings, not a posted salary
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { UNSPECIFIED_LOCATION } from "@/types/api";
import type { LocationGroup } from "@/types/api";

/**
 * A checkbox dropdown for the job list's location filter. Replaces the
 * old single <select>: the backend's `location` param is multi-value
 * (repeated params, OR'd), and the option list is grouped by country
 * (see GET /jobs/locations). No new dependency - a plain button + a
 * positioned panel with a type-to-filter box and country section
 * headings, closed on outside click / Escape.
 *
 * `selected` / `onChange` deal in the flat list of canonical labels (a
 * "Not specified" pick is the UNSPECIFIED_LOCATION sentinel in that
 * same list). Rendering the chips per selected location is the parent's
 * job (JobFilters), same as every other filter.
 */
export function LocationMultiSelect({
  groups,
  selected,
  onChange,
}: {
  groups: LocationGroup[] | undefined;
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [filterText, setFilterText] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const selectedSet = useMemo(() => new Set(selected), [selected]);

  const filteredGroups = useMemo(() => {
    const needle = filterText.trim().toLowerCase();
    const source = groups ?? [];
    if (!needle) return source;
    return source
      .map((group) => ({
        country: group.country,
        locations: group.locations.filter(
          (loc) =>
            loc.toLowerCase().includes(needle) ||
            group.country.toLowerCase().includes(needle),
        ),
      }))
      .filter((group) => group.locations.length > 0);
  }, [groups, filterText]);

  function toggle(value: string) {
    if (selectedSet.has(value)) {
      onChange(selected.filter((v) => v !== value));
    } else {
      onChange([...selected, value]);
    }
  }

  const label =
    selected.length === 0
      ? "All locations"
      : selected.length === 1
        ? selected[0] === UNSPECIFIED_LOCATION
          ? "Not specified"
          : selected[0]
        : `${selected.length} locations`;

  return (
    <div ref={rootRef} className="relative w-full sm:w-auto">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label="Filter by location"
        className="flex w-full items-center justify-between gap-2 border border-border-strong bg-surface py-2 pl-3 pr-2.5 text-sm text-text focus:border-accent focus:outline-none sm:w-[220px]"
      >
        <span className="truncate">{label}</span>
        <span className="text-2xs text-text-faintest">▾</span>
      </button>

      {open && (
        <div
          role="listbox"
          aria-multiselectable="true"
          className="absolute left-0 z-20 mt-1 max-h-[min(60vh,340px)] w-[min(320px,calc(100vw-2rem))] overflow-y-auto border border-border-strong bg-surface shadow-[0_6px_16px_rgba(20,30,40,.12)]"
        >
          <div className="sticky top-0 flex items-center gap-2 border-b border-divider bg-surface px-2.5 py-2">
            <input
              type="text"
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              placeholder="Filter locations…"
              className="w-full border border-border-strong bg-surface-alt px-2 py-1.5 text-xs text-text focus:border-accent focus:outline-none"
            />
            {selected.length > 0 && (
              <button
                type="button"
                onClick={() => onChange([])}
                className="whitespace-nowrap font-mono text-xs text-accent hover:text-accent-hover"
              >
                Clear
              </button>
            )}
          </div>

          <ul className="py-1">
            <li>
              <label className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs text-text-subtle hover:bg-surface-alt">
                <input
                  type="checkbox"
                  checked={selectedSet.has(UNSPECIFIED_LOCATION)}
                  onChange={() => toggle(UNSPECIFIED_LOCATION)}
                />
                Not specified
              </label>
            </li>
            {filteredGroups.map((group) => (
              <li key={group.country}>
                <p className="px-3 pb-1 pt-2 font-mono text-2xs uppercase tracking-[0.08em] text-text-faintest">
                  {group.country}
                </p>
                {group.locations.map((loc) => (
                  <label
                    key={loc}
                    className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm text-text hover:bg-surface-alt"
                  >
                    <input
                      type="checkbox"
                      checked={selectedSet.has(loc)}
                      onChange={() => toggle(loc)}
                    />
                    {loc}
                  </label>
                ))}
              </li>
            ))}
            {groups !== undefined && filteredGroups.length === 0 && (
              <li className="px-3 py-2 text-xs text-text-faintest">No matching locations</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}

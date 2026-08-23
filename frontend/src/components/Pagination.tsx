"use client";

export function Pagination({
  total,
  limit,
  offset,
  onOffsetChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onOffsetChange: (offset: number) => void;
}) {
  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + limit, total);

  return (
    <div className="flex items-center justify-between gap-4 text-sm">
      <p className="font-mono text-xs text-text-faintest">
        {total === 0 ? "0 results" : `${rangeStart}-${rangeEnd} of ${total}`}
      </p>

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={currentPage <= 1}
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
          className="rounded-lg border border-border-strong px-3 py-1.5 font-medium text-text-muted disabled:cursor-not-allowed disabled:opacity-40 enabled:hover:bg-surface-alt"
        >
          Previous
        </button>
        <span className="font-mono text-xs text-text-faintest">
          Page {currentPage} of {totalPages}
        </span>
        <button
          type="button"
          disabled={currentPage >= totalPages}
          onClick={() => onOffsetChange(offset + limit)}
          className="rounded-lg border border-border-strong px-3 py-1.5 font-medium text-text-muted disabled:cursor-not-allowed disabled:opacity-40 enabled:hover:bg-surface-alt"
        >
          Next
        </button>
      </div>
    </div>
  );
}

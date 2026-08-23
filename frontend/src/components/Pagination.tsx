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
      <p className="text-neutral-500 dark:text-neutral-400">
        {total === 0 ? "0 results" : `${rangeStart}-${rangeEnd} of ${total}`}
      </p>

      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={currentPage <= 1}
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
          className="rounded-lg border border-neutral-300 px-3 py-1.5 font-medium text-neutral-700 disabled:cursor-not-allowed disabled:opacity-40 enabled:hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-200 dark:enabled:hover:bg-neutral-800"
        >
          Previous
        </button>
        <span className="tabular-nums text-neutral-500 dark:text-neutral-400">
          Page {currentPage} of {totalPages}
        </span>
        <button
          type="button"
          disabled={currentPage >= totalPages}
          onClick={() => onOffsetChange(offset + limit)}
          className="rounded-lg border border-neutral-300 px-3 py-1.5 font-medium text-neutral-700 disabled:cursor-not-allowed disabled:opacity-40 enabled:hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-200 dark:enabled:hover:bg-neutral-800"
        >
          Next
        </button>
      </div>
    </div>
  );
}

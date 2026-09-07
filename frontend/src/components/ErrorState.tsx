import { ApiError } from "@/lib/api";

/**
 * One shared "the data failed to load" treatment. Every page that
 * fetches uses this instead of hand-rolling a red box with the raw
 * error string in it - a 404 reads as "not found", anything else reads
 * as "server problem", and a "Try again" button re-runs the query
 * (TanStack Query's refetch) when the caller passes one.
 */
export function ErrorState({
  error,
  onRetry,
  resourceLabel = "this",
  compact = false,
}: {
  error: unknown;
  onRetry?: () => void;
  resourceLabel?: string;
  compact?: boolean;
}) {
  const status = error instanceof ApiError ? error.status : undefined;
  const notFound = status === 404;

  const title = notFound ? "Not found" : "Couldn't load " + resourceLabel;
  const description = notFound
    ? "This may have been removed, or the link is out of date."
    : "The server is unreachable or returning an error. This is usually temporary.";

  return (
    <div
      role="alert"
      className={`flex flex-col items-center rounded-xl border border-border-strong bg-surface-alt text-center ${
        compact ? "gap-1.5 px-5 py-8" : "gap-2 px-6 py-12"
      }`}
    >
      <span aria-hidden className="text-xl text-accent">
        {notFound ? "◎" : "⚠"}
      </span>
      <p className="text-sm font-semibold text-text">{title}</p>
      <p className="max-w-sm text-[13px] leading-relaxed text-text-subtle">{description}</p>
      {onRetry && !notFound && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-1 rounded-lg bg-accent px-4 py-1.5 text-[13px] font-semibold text-white hover:bg-accent-hover"
        >
          Try again
        </button>
      )}
    </div>
  );
}

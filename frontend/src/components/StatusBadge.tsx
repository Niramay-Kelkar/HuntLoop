import type { ApplicationStatus } from "@/types/api";

/**
 * Read-only application-status badge, plus the shared color/label maps
 * StatusControl (the interactive version, JobCard) reuses - single
 * source of truth for what each status looks like.
 */
export const STATUS_STYLES: Record<ApplicationStatus, string> = {
  not_applied:
    "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400",
  applied: "bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  interviewing:
    "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  rejected: "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300",
  offer: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
};

export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  not_applied: "Not applied",
  applied: "Applied",
  interviewing: "Interviewing",
  rejected: "Rejected",
  offer: "Offer",
};

export function StatusBadge({ status }: { status: ApplicationStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${STATUS_STYLES[status]}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}

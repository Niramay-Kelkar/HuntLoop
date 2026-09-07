import type { ReactNode } from "react";

/**
 * One shared "there's genuinely nothing here (and that's not an error)"
 * treatment, so every list/section in the app words and styles its empty
 * case the same way instead of hand-rolling a bare `<div>` each time.
 */
export function EmptyState({
  icon = "◦",
  title,
  description,
  action,
  compact = false,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div
      className={`flex flex-col items-center rounded-xl border border-dashed border-border-strong bg-surface-alt text-center ${
        compact ? "gap-1.5 px-5 py-8" : "gap-2 px-6 py-12"
      }`}
    >
      <span aria-hidden className="text-xl text-text-faintest">
        {icon}
      </span>
      <p className="text-sm font-semibold text-text">{title}</p>
      {description && (
        <p className="max-w-sm text-[13px] leading-relaxed text-text-subtle">{description}</p>
      )}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

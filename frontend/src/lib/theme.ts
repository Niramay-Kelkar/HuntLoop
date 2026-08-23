import type { ApplicationStatus } from "@/types/api";

/**
 * Colors/logic ported 1:1 from design/HuntLoop.dc.html's `META`/`PALETTE`/
 * `tier()` (see CLAUDE.md for where that reference file lives) - the
 * mockup computes these as plain JS rather than Tailwind classes, so this
 * module mirrors that instead of forcing them into the Tailwind theme.
 */
export const STATUS_META: Record<
  ApplicationStatus,
  { label: string; color: string; bg: string; dot: string }
> = {
  not_applied: { label: "Not applied", color: "#78736c", bg: "#efece7", dot: "#b0a89d" },
  applied: { label: "Applied", color: "#2f6bb0", bg: "#e9f1fa", dot: "#3f7fca" },
  interviewing: { label: "Interviewing", color: "#b07817", bg: "#fbf1dd", dot: "#d98324" },
  offer: { label: "Offer", color: "#1f8f4e", bg: "#e6f4ea", dot: "#1f9d55" },
  rejected: { label: "Rejected", color: "#b0463a", bg: "#f8e9e6", dot: "#d64c3c" },
};

export const STATUS_ORDER: ApplicationStatus[] = [
  "not_applied",
  "applied",
  "interviewing",
  "offer",
  "rejected",
];

const AVATAR_PALETTE: [string, string][] = [
  ["#fdece7", "#c8442f"],
  ["#e9eef7", "#3560a8"],
  ["#e7f2ec", "#2b8a56"],
  ["#f3ecf7", "#7346a8"],
  ["#fbf0dd", "#a5701a"],
  ["#e7f1f0", "#2b7a72"],
  ["#f5ece2", "#8a6528"],
];

export function avatarColors(id: number): { bg: string; color: string } {
  const [bg, color] = AVATAR_PALETTE[id % AVATAR_PALETTE.length];
  return { bg, color };
}

export function initials(companyName: string): string {
  const trimmed = companyName.trim();
  if (trimmed.length === 0) return "?";
  return (trimmed[0].toUpperCase() + (trimmed[1] ?? "").toLowerCase()).slice(0, 2);
}

/**
 * Highest score seen across real backfills so far (~0.593, Palantir
 * "Software Engineer" roles - see ScoreIndicator's original comment /
 * SESSIONS.md) - a job scoring at or above this renders fully green.
 * Shared here so the ring (ScoreIndicator) and any other score-colored UI
 * (table pills, kanban cards) stay calibrated identically.
 */
export const SCORE_CEILING = 0.6;

/** 0-100 percent, calibrated against SCORE_CEILING - not the raw percent. */
export function calibratedPercent(score: number): number {
  return Math.max(0, Math.min(1, score / SCORE_CEILING)) * 100;
}

export function scoreTier(score: number): { color: string; bg: string } {
  const p = calibratedPercent(score);
  if (p >= 80) return { color: "#1f9d55", bg: "#e6f4ea" };
  if (p >= 60) return { color: "#d98324", bg: "#fbf1dd" };
  return { color: "#d64c3c", bg: "#f8e9e6" };
}

export function formatDate(dateString: string | null): string | null {
  if (!dateString) return null;
  return new Date(dateString).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** Whole-dollar wage/salary amounts as compact "$168k" strings, matching
 * design/HuntLoop.dc.html's mock sponsor/salary figures. */
export function formatWage(amount: number): string {
  return `$${Math.round(amount / 1000)}k`;
}

/** Scraped job descriptions may contain raw or entity-escaped HTML (see
 * CLAUDE.md's text_cleaning note) - strip tags for safe plain-text display
 * rather than rendering untrusted external HTML. */
export function stripHtml(html: string): string {
  return html
    .replace(/<[^>]*>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

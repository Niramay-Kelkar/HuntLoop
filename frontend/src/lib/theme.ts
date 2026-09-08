import type { ApplicationStatus } from "@/types/api";

/**
 * Visual identity: Direction A, "Register" (see the visual-identity
 * proposal + SESSIONS.md). Status, score and avatar colors are cooled
 * to sit beside the single deep form-blue accent instead of the old
 * warm terracotta scheme. The score/sponsor greens stay green - they
 * carry meaning - but shift a notch cooler.
 */
export const STATUS_META: Record<
  ApplicationStatus,
  { label: string; color: string; bg: string; dot: string }
> = {
  not_applied: { label: "Not applied", color: "#5b6570", bg: "#eef0f2", dot: "#9aa1a8" },
  applied: { label: "Applied", color: "#2f6bb0", bg: "#e7eef6", dot: "#3f7fca" },
  interviewing: { label: "Interviewing", color: "#8a5f18", bg: "#f4ede0", dot: "#c08a2a" },
  offer: { label: "Offer", color: "#1f7a45", bg: "#e9f2ec", dot: "#2f9d57" },
  rejected: { label: "Rejected", color: "#a83f2e", bg: "#f3e8e5", dot: "#c0503c" },
};

export const STATUS_ORDER: ApplicationStatus[] = [
  "not_applied",
  "applied",
  "interviewing",
  "offer",
  "rejected",
];

const AVATAR_PALETTE: [string, string][] = [
  ["#e9edf1", "#33475c"],
  ["#eceef1", "#3c434c"],
  ["#e8f0ec", "#2f6a4a"],
  ["#eeedf2", "#4d4560"],
  ["#f0ede7", "#6b5a3a"],
  ["#e7f0ef", "#2f6a63"],
  ["#f1ebe8", "#6b4a3c"],
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
 * `match_score` from the API is the composite match score (see
 * huntloop.match_scoring) - already a calibrated value in [0, 1], so
 * this is just a clamp to a 0-100 percent. Calibration (embedding
 * similarity against its ceiling, skills ratio against its ceiling)
 * moved server-side with composite-match-score-v1; the frontend used to
 * divide the raw similarity by SCORE_CEILING here.
 */
export function calibratedPercent(score: number): number {
  return Math.max(0, Math.min(1, score)) * 100;
}

export function scoreTier(score: number): { color: string; bg: string } {
  const p = calibratedPercent(score);
  if (p >= 80) return { color: "#2f7d4f", bg: "#ecf3ee" };
  if (p >= 60) return { color: "#9a6a1c", bg: "#f4ede0" };
  return { color: "#b0432f", bg: "#f4e9e6" };
}

export function formatDate(dateString: string | null): string | null {
  if (!dateString) return null;
  return new Date(dateString).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** A short relative-time label ("just now", "3d ago", "2mo ago") for a
 * timestamp string, or null if there's no timestamp. Used for an
 * application's single `status_updated_at` - a light "when did this last
 * move" signal, not a full activity history. */
export function timeAgo(dateString: string | null): string | null {
  if (!dateString) return null;
  const then = new Date(dateString).getTime();
  if (Number.isNaN(then)) return null;
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.round(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.round(months / 12)}y ago`;
}

/** Whole-dollar wage/salary amounts as compact "$168k" strings. */
export function formatWage(amount: number): string {
  return `$${Math.round(amount / 1000)}k`;
}

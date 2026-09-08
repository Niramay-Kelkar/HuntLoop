import { calibratedPercent, scoreTier } from "@/lib/theme";

/**
 * Match-score ring (visual identity: Direction A, "Register"). A conic
 * dial - the score percent in mono at its centre, a tier-colored arc
 * around it - on Register's cooled semantic score-tier colors
 * (lib/theme.ts), a hairline border rule for the track, no drop shadow.
 *
 * `score` is the composite match score from the API (huntloop.match_scoring):
 * a calibrated value in [0, 1], so both the printed number and the arc
 * sweep read straight off it. Calibration moved server-side with
 * composite-match-score-v1 - this component no longer rescales.
 *
 * `provisional` (score_basis === "partial") marks a posting whose score
 * is the embedding-only fallback because its skills analysis hasn't run
 * yet. It renders a neutral, non-alarming accent-blue marker - never a
 * warning color, never hidden, never a lower score. See ProvisionalScoreNote
 * for the legend that explains the marker on the list/table views.
 *
 * Kept the name `ScoreIndicator` and the `size` prop so every call site
 * (job cards, table, job detail) switches over unchanged.
 */
const SIZES = {
  sm: { outer: 36, inner: 26, num: "text-2xs" },
  md: { outer: 52, inner: 40, num: "text-sm" },
  lg: { outer: 76, inner: 58, num: "text-xl" },
} as const;

const PROVISIONAL_TITLE = "Score provisional — skills analysis pending";

export function ScoreIndicator({
  score,
  size = "md",
  provisional = false,
}: {
  score: number | null;
  size?: keyof typeof SIZES;
  provisional?: boolean;
}) {
  const dims = SIZES[size];

  if (score === null) {
    if (size === "lg") {
      return (
        <div className="flex flex-none flex-col items-center gap-1">
          <span className="font-mono text-base text-text-faintest">--</span>
          <span className="font-mono text-2xs uppercase tracking-[0.14em] text-text-faintest">
            not scored
          </span>
        </div>
      );
    }
    return <span className="font-mono text-sm text-text-faintest">--</span>;
  }

  const { color } = scoreTier(score);
  const percent = Math.round(calibratedPercent(score));
  const deg = Math.round((calibratedPercent(score) / 100) * 360);
  const title = provisional
    ? `Match score: ${percent}% (provisional) — skills analysis pending`
    : `Match score: ${percent}%`;

  const ring = (
    <div
      className="grid flex-none place-items-center rounded-full"
      style={{
        width: dims.outer,
        height: dims.outer,
        background: `conic-gradient(${color} ${deg}deg, var(--color-border) ${deg}deg)`,
        boxShadow: "inset 0 0 0 1px var(--color-border)",
      }}
    >
      <div
        className="grid place-items-center rounded-full bg-surface"
        style={{ width: dims.inner, height: dims.inner }}
      >
        <span className={`font-mono font-medium tabular-nums ${dims.num}`} style={{ color }}>
          {percent}
        </span>
      </div>
    </div>
  );

  if (size === "lg") {
    return (
      <div className="flex flex-none flex-col items-center gap-1.5" title={title}>
        {ring}
        <span className="font-mono text-2xs uppercase tracking-[0.14em] text-text-faintest">
          match score
        </span>
        {provisional && (
          <span className="max-w-[8rem] text-center font-mono text-2xs leading-tight text-accent">
            score provisional &middot; skills analysis pending
          </span>
        )}
      </div>
    );
  }

  return (
    <span className="relative flex flex-none items-start" title={title}>
      {ring}
      {provisional && (
        <span
          aria-label={PROVISIONAL_TITLE}
          className="ml-0.5 font-mono text-2xs font-semibold leading-none text-accent"
        >
          *
        </span>
      )}
    </span>
  );
}

/**
 * The legend that explains ScoreIndicator's provisional "*" marker on
 * the job list / table (where the marker itself has to stay compact).
 * Renders nothing when no visible posting is on the fallback.
 */
export function ProvisionalScoreNote({ jobs }: { jobs: { score_basis: string | null }[] }) {
  if (!jobs.some((j) => j.score_basis === "partial")) return null;
  return (
    <p className="font-mono text-2xs leading-relaxed text-text-faint">
      <span className="text-accent">*</span> score provisional &mdash; based on semantic
      similarity only; a fuller skills breakdown is queued and will refine it.
    </p>
  );
}

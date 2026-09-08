import { calibratedPercent, scoreTier } from "@/lib/theme";

/**
 * Match-score ring (visual identity: Direction A, "Register"). A conic
 * dial - the raw percent in mono at its centre, a tier-colored arc
 * around it - reskinned onto Register's palette: the cooled semantic
 * score-tier colors (lib/theme.ts) for the arc, a hairline border rule
 * for the track and outer edge, no drop shadow.
 *
 * The arc sweep uses the score's fraction of SCORE_CEILING (real scores
 * cluster ~0.03-0.59, see lib/theme.ts) while the printed number stays
 * the real raw percent, so the dial has usable range instead of every
 * job reading near-empty.
 *
 * Kept the name `ScoreIndicator` and the `size` prop so every call site
 * (job cards, table, job detail) switches over unchanged.
 */
const SIZES = {
  sm: { outer: 36, inner: 26, num: "text-2xs" },
  md: { outer: 52, inner: 40, num: "text-sm" },
  lg: { outer: 76, inner: 58, num: "text-xl" },
} as const;

export function ScoreIndicator({
  score,
  size = "md",
}: {
  score: number | null;
  size?: keyof typeof SIZES;
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
  const percent = Math.round(score * 100);
  const deg = Math.round((calibratedPercent(score) / 100) * 360);

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
      <div className="flex flex-none flex-col items-center gap-1.5" title={`Match score: ${percent}%`}>
        {ring}
        <span className="font-mono text-2xs uppercase tracking-[0.14em] text-text-faintest">
          match score
        </span>
      </div>
    );
  }

  return (
    <span className="flex-none" title={`Match score: ${percent}%`}>
      {ring}
    </span>
  );
}

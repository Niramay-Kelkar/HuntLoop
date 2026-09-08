import { calibratedPercent, scoreTier } from "@/lib/theme";

/**
 * Match-score gauge (visual identity: Direction A, "Register"). A
 * horizontal meter with the raw percent in mono beside it - an
 * instrument readout, not a badge or a ring. The fill uses the score's
 * fraction of SCORE_CEILING (real scores cluster ~0.03-0.59, see
 * lib/theme.ts) while the printed number stays the real raw percent, so
 * the bar has usable range instead of every job reading near-empty.
 *
 * Kept the name `ScoreIndicator` and the `size` prop so every call site
 * (job cards, table, job detail) switches over unchanged.
 */
const SIZES = {
  sm: { track: 40, num: "text-sm", bar: 3 },
  md: { track: 56, num: "text-base", bar: 4 },
  lg: { track: 132, num: "text-3xl", bar: 6 },
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
        <div className="flex flex-none flex-col items-end gap-1">
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
  const fill = Math.round(calibratedPercent(score));

  const track = (
    <span
      className="block flex-none overflow-hidden bg-border"
      style={{ width: dims.track, height: dims.bar }}
    >
      <span className="block h-full" style={{ width: `${fill}%`, background: color }} />
    </span>
  );

  if (size === "lg") {
    return (
      <div className="flex flex-none flex-col items-end gap-1.5" title={`Match score: ${percent}%`}>
        <span className={`font-mono font-medium leading-none ${dims.num}`} style={{ color }}>
          {percent}
        </span>
        {track}
        <span className="font-mono text-2xs uppercase tracking-[0.14em] text-text-faintest">
          match score
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-none items-center gap-2" title={`Match score: ${percent}%`}>
      <span className={`font-mono font-medium tabular-nums ${dims.num}`} style={{ color }}>
        {percent}
      </span>
      {track}
    </div>
  );
}

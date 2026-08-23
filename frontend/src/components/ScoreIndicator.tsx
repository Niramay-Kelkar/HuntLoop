import { calibratedPercent, scoreTier } from "@/lib/theme";

/**
 * Match-score ring (design/HuntLoop.dc.html's conic-gradient ring, not a
 * bar) - calibrated to this app's real observed score range (see
 * lib/theme.ts's SCORE_CEILING comment): real scores cluster roughly
 * between 0.03 and 0.59, so the ring's color/fill uses the score's
 * fraction of SCORE_CEILING while the printed number stays the real raw
 * percent - a generic 0-100 scale would render almost every real job the
 * same dull red.
 */
const SIZES = {
  sm: { outer: 38, inner: 28, num: "text-[11px]" },
  md: { outer: 52, inner: 38, num: "text-sm" },
  lg: { outer: 74, inner: 56, num: "text-xl" },
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
    return (
      <span className="inline-flex items-center rounded-full bg-surface-alt px-2.5 py-1 text-xs font-medium text-text-faintest">
        not scored
      </span>
    );
  }

  const { color } = scoreTier(score);
  const percent = Math.round(score * 100);
  const deg = Math.round(calibratedPercent(score) * 3.6);

  return (
    <div
      className="grid flex-none place-items-center rounded-full"
      style={{
        width: dims.outer,
        height: dims.outer,
        background: `conic-gradient(${color} ${deg}deg, #ece9e3 ${deg}deg)`,
      }}
      title={`Match score: ${percent}%`}
    >
      <div
        className="grid place-items-center rounded-full bg-surface"
        style={{ width: dims.inner, height: dims.inner }}
      >
        <span className={`font-mono font-bold ${dims.num}`} style={{ color }}>
          {percent}
        </span>
      </div>
    </div>
  );
}

/**
 * Match-score visual indicator: a color-graded bar + percentage, not a
 * bare number. Calibrated to this app's real observed score
 * distribution (all-MiniLM-L6-v2 cosine similarity between the active
 * resume and real scraped job descriptions - see SESSIONS.md): real
 * scores cluster roughly between 0.03 and 0.59, with the bulk in
 * 0.3-0.4. A generic 0-1 red/yellow/green scale would make almost every
 * real job look the same dull color and defeat the point of a
 * scannable-at-a-glance indicator, so the gradient's "full green" end
 * is pinned to the real observed ceiling (SCORE_CEILING) instead of 1.0.
 */

// Highest score seen across real backfills so far (~0.593, Palantir
// "Software Engineer" roles) - a job scoring at or above this renders
// fully green. Revisit if the resume or job mix changes enough to shift
// the real distribution meaningfully.
const SCORE_CEILING = 0.6;

function scoreToHue(score: number): number {
  const clamped = Math.max(0, Math.min(1, score / SCORE_CEILING));
  return clamped * 120; // 0 = red, 60 = amber, 120 = green
}

export function ScoreIndicator({ score }: { score: number | null }) {
  if (score === null) {
    return (
      <span className="inline-flex items-center rounded-full bg-neutral-100 px-2.5 py-1 text-xs font-medium text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400">
        not scored
      </span>
    );
  }

  const hue = scoreToHue(score);
  const percent = Math.round(score * 100);
  const fillPercent = Math.round(Math.max(4, Math.min(100, (score / SCORE_CEILING) * 100)));
  const color = `hsl(${hue}, 72%, 42%)`;

  return (
    <div className="flex min-w-[128px] items-center gap-2" title={`Match score: ${percent}%`}>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-700">
        <div
          className="h-full rounded-full transition-[width] duration-300"
          style={{ width: `${fillPercent}%`, backgroundColor: color }}
        />
      </div>
      <span className="w-10 text-right text-xs font-bold tabular-nums" style={{ color }}>
        {percent}%
      </span>
    </div>
  );
}

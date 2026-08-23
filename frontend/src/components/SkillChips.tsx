/**
 * Matched/missing skills as distinct chip styles - green/solid for
 * matched, neutral-outlined for missing, so the two are unmistakable at
 * a glance (not just a differently-colored word in the same pill
 * style). Capped per section: real missing_skills lists run to 20-30+
 * items for a badly-matched job (see SESSIONS.md) - showing all of them
 * would blow out card height, so only the first MAX_VISIBLE render,
 * with a "+N more" chip for the rest.
 */
const MAX_VISIBLE = 5;

function Chip({ label, variant }: { label: string; variant: "matched" | "missing" }) {
  const className =
    variant === "matched"
      ? "bg-emerald-50 text-emerald-700 border border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-800"
      : "bg-transparent text-neutral-500 border border-dashed border-neutral-300 dark:text-neutral-400 dark:border-neutral-700";

  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${className}`}>
      {label}
    </span>
  );
}

function ChipRow({ skills, variant }: { skills: string[]; variant: "matched" | "missing" }) {
  const visible = skills.slice(0, MAX_VISIBLE);
  const overflow = skills.length - visible.length;

  return (
    <div className="flex flex-wrap gap-1.5">
      {visible.map((skill, i) => (
        <Chip key={`${skill}-${i}`} label={skill} variant={variant} />
      ))}
      {overflow > 0 && (
        <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium text-neutral-400 dark:text-neutral-500">
          +{overflow} more
        </span>
      )}
    </div>
  );
}

export function SkillChips({
  matchedSkills,
  missingSkills,
}: {
  matchedSkills: string[] | null;
  missingSkills: string[] | null;
}) {
  const hasMatched = matchedSkills && matchedSkills.length > 0;
  const hasMissing = missingSkills && missingSkills.length > 0;

  if (!hasMatched && !hasMissing) {
    return <p className="text-xs text-neutral-400 dark:text-neutral-500">Skills not yet analyzed</p>;
  }

  return (
    <div className="flex flex-col gap-1.5">
      {hasMatched && <ChipRow skills={matchedSkills} variant="matched" />}
      {hasMissing && <ChipRow skills={missingSkills} variant="missing" />}
    </div>
  );
}

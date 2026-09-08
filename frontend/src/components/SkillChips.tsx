/**
 * Matched-skills preview chips for job cards: top 3 matched skills as
 * small ruled mono tags plus a "+N" overflow marker. Missing skills
 * live on the job detail page's "Skills to develop" section, not here.
 *
 * Visual identity: Direction A, "Register" - square, hairline-ruled
 * green tags rather than soft filled pills.
 */
const PREVIEW_COUNT = 3;

export function SkillChipsPreview({ matchedSkills }: { matchedSkills: string[] | null }) {
  if (!matchedSkills || matchedSkills.length === 0) {
    return <p className="text-sm text-text-faintest">Skills not yet analyzed</p>;
  }

  const visible = matchedSkills.slice(0, PREVIEW_COUNT);
  const overflow = matchedSkills.length - visible.length;

  return (
    <div className="flex flex-wrap gap-1.5">
      {visible.map((skill, i) => (
        <span
          key={`${skill}-${i}`}
          className="border px-1.5 py-0.5 font-mono text-2xs"
          style={{
            backgroundColor: "var(--color-good-bg)",
            color: "var(--color-good)",
            borderColor: "var(--color-good-border)",
          }}
        >
          {skill}
        </span>
      ))}
      {overflow > 0 && (
        <span className="px-0.5 py-0.5 font-mono text-2xs text-text-faintest">+{overflow}</span>
      )}
    </div>
  );
}

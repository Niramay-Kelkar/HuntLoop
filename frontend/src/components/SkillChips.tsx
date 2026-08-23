/**
 * Matched-skills preview chips for job cards - mirrors design/HuntLoop.dc.html's
 * card ("job.matchedTop"): top 3 matched skills as small green mono pills
 * plus a "+N" overflow chip. The mockup's list/table cards show matched
 * skills only (no missing) - missing skills move to the job detail page's
 * dedicated "Skills to develop" section instead, so they're not duplicated
 * here.
 */
const PREVIEW_COUNT = 3;

export function SkillChipsPreview({ matchedSkills }: { matchedSkills: string[] | null }) {
  if (!matchedSkills || matchedSkills.length === 0) {
    return <p className="text-xs text-text-faintest">Skills not yet analyzed</p>;
  }

  const visible = matchedSkills.slice(0, PREVIEW_COUNT);
  const overflow = matchedSkills.length - visible.length;

  return (
    <div className="flex flex-wrap gap-1.5">
      {visible.map((skill, i) => (
        <span
          key={`${skill}-${i}`}
          className="rounded-md px-2 py-0.5 font-mono text-[11px]"
          style={{ backgroundColor: "#e9f4ee", color: "#1f8f4e" }}
        >
          {skill}
        </span>
      ))}
      {overflow > 0 && <span className="px-1 py-0.5 font-mono text-[11px] text-text-faintest">+{overflow}</span>}
    </div>
  );
}

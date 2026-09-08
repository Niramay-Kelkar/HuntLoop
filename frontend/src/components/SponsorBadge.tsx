/**
 * H-1B sponsor indicator for the job list (cards + table). Sponsorship
 * awareness is one of this app's core differentiators, so it gets a
 * real mark rather than a line of muted text.
 *
 * Visual identity: Direction A, "Register" - a ruled "stamp": solid
 * form-blue rule when the employer has a resolved DOL sponsor match,
 * a dashed neutral rule when there's no LCA history on file. The list
 * endpoint only carries the boolean (no per-row filing count, by
 * design), so the stamp reads "on file" / "no record"; the job detail
 * page shows the actual filing figures.
 */
export function SponsorBadge({ hasSponsorHistory }: { hasSponsorHistory: boolean }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 border px-1.5 py-0.5 font-mono text-2xs font-medium uppercase tracking-[0.06em]"
      style={
        hasSponsorHistory
          ? { borderColor: "var(--color-accent)", color: "var(--color-accent)" }
          : { borderColor: "var(--color-border-strong)", color: "var(--color-text-faint)", borderStyle: "dashed" }
      }
      title={
        hasSponsorHistory
          ? "This employer has filed H-1B LCA disclosures in recent DOL data"
          : "No H-1B LCA disclosures found for this employer in recent DOL data"
      }
    >
      {hasSponsorHistory ? "H-1B on file" : "No LCA record"}
    </span>
  );
}

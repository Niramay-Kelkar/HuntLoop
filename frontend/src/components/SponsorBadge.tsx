/**
 * H-1B sponsor indicator for the job list (cards + table). Sponsorship
 * awareness is one of this app's core differentiators versus a generic
 * job board, so it gets a real badge here rather than a line of muted
 * text - green when the company has a resolved DOL sponsor match,
 * neutral when there's no LCA history on file. Mirrors the dot + wording
 * the job detail page's sponsor card already uses.
 */
export function SponsorBadge({ hasSponsorHistory }: { hasSponsorHistory: boolean }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-[10px] font-medium"
      style={
        hasSponsorHistory
          ? { backgroundColor: "#e6f4ea", color: "#1f7a45" }
          : { backgroundColor: "#efece7", color: "#8a837a" }
      }
      title={
        hasSponsorHistory
          ? "This employer has filed H-1B LCA disclosures in recent DOL data"
          : "No H-1B LCA disclosures found for this employer in recent DOL data"
      }
    >
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ backgroundColor: hasSponsorHistory ? "#1f9d55" : "#c9c2b8" }}
      />
      {hasSponsorHistory ? "Sponsors H-1B" : "No H-1B data"}
    </span>
  );
}

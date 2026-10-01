"use client";

import { useQuery } from "@tanstack/react-query";

import { getDemoInfo } from "@/lib/api";
import { isDemoMode } from "@/lib/demoMode";
import { formatDate } from "@/lib/theme";

const REPO_URL = "https://github.com/Niramay-Kelkar/HuntLoop";

/**
 * Shown on every page when NEXT_PUBLIC_DEMO_MODE is on. Tells a visitor
 * this is a fixed sample, not a live scrape, and points them at the
 * repo for the real thing (their own resume, AI drafting, a live
 * tracker) - none of which this demo offers, see huntloop.demo_mode.
 *
 * Renders nothing outside demo mode or before the snapshot date loads,
 * so this never flashes an empty bar.
 */
export function DemoBanner() {
  const demoInfo = useQuery({
    queryKey: ["demo-info"],
    queryFn: getDemoInfo,
    enabled: isDemoMode(),
  });

  if (!isDemoMode()) return null;

  const snapshotLabel = demoInfo.data?.snapshot_date ? formatDate(demoInfo.data.snapshot_date) : null;

  return (
    <div
      data-testid="demo-banner"
      className="border-b border-border bg-accent-soft px-6 py-2 text-center font-mono text-xs text-text"
    >
      Live demo on sample data{snapshotLabel ? ` as of ${snapshotLabel}` : ""}. Run it locally with your own
      resume and AI drafting:{" "}
      <a href={REPO_URL} className="underline hover:text-accent">
        {REPO_URL}
      </a>
    </div>
  );
}

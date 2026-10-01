"use client";

import { useEffect, useState } from "react";

import { getHealth } from "@/lib/api";
import { isDemoMode } from "@/lib/demoMode";

// A free-tier backend host (see CLAUDE.md's DEMO_MODE notes) can sleep
// after inactivity and take a while to answer its first request. If
// GET /health hasn't resolved within this long, assume that's what is
// happening and say so, rather than leaving every page stuck on a bare
// loading skeleton with no explanation.
const SLOW_THRESHOLD_MS = 3_000;
const RETRY_INTERVAL_MS = 4_000;

type WakeState = "checking" | "slow" | "ready";

/**
 * Wraps the page content in demo mode only. Pings GET /health on mount
 * and retries until it succeeds, showing a wake up message once the
 * first attempt has taken more than a few seconds. Outside demo mode
 * this renders children immediately and does nothing else.
 */
export function DemoWakeUpGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<WakeState>(isDemoMode() ? "checking" : "ready");

  useEffect(() => {
    if (!isDemoMode()) return;

    let cancelled = false;
    const slowTimer = setTimeout(() => {
      if (!cancelled) setState("slow");
    }, SLOW_THRESHOLD_MS);

    async function poll() {
      while (!cancelled) {
        try {
          await getHealth();
          if (!cancelled) {
            clearTimeout(slowTimer);
            setState("ready");
          }
          return;
        } catch {
          await new Promise((resolve) => setTimeout(resolve, RETRY_INTERVAL_MS));
        }
      }
    }

    poll();

    return () => {
      cancelled = true;
      clearTimeout(slowTimer);
    };
  }, []);

  if (state === "ready") {
    return <>{children}</>;
  }

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 py-24 text-center">
      <div className="h-9 w-9 animate-spin rounded-full border-2 border-accent-soft border-t-accent" />
      {state === "slow" ? (
        <div data-testid="demo-wakeup-message">
          <p className="text-sm font-semibold text-text">Waking up the demo server</p>
          <p className="mx-auto mt-1 max-w-[320px] text-xs text-text-faintest">
            This can take up to a minute on first load.
          </p>
        </div>
      ) : (
        <p className="text-xs text-text-faintest">Loading…</p>
      )}
    </div>
  );
}

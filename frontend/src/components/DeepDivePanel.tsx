"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { deepDiveErrorInfo, getDeepDive, startDeepDive } from "@/lib/api";
import type {
  DeepDiveGap,
  DeepDiveResponse,
  DeepDiveResult,
  DeepDiveResumeEdit,
  DeepDiveStrength,
} from "@/types/api";

/**
 * The Resume Deep-Dive panel on the job detail page - a heavier skill-gap
 * analysis of the active resume against this one posting (POST
 * /jobs/{id}/deep-dive, then poll GET /deep-dives/{id} until it
 * finishes). See huntloop.api.routers.deep_dive.
 *
 * This is the first polling UI in the app, so a few deliberate choices:
 *
 *  - NOTHING happens until the user presses the button. The panel does
 *    not POST on mount to discover whether a finished analysis already
 *    exists, even though POST would return one: POST also re-dispatches
 *    a previously FAILED run, so mounting the page would silently spend
 *    Modal budget. Idle-until-asked is the only safe default.
 *  - Polling backs off 3s -> 5s -> 10s as a run gets longer, stops dead
 *    on a terminal status (done/failed), and gives up after
 *    MAX_POLL_MS with a clear message rather than hammering forever.
 *    The server's own Modal function times out at 600s, so the ceiling
 *    here is set just beyond that - a run still "running" by then is
 *    never going to finish.
 *  - Every field of `result` is read defensively (see
 *    types/api.ts's DeepDiveResult). It's LLM-produced content that the
 *    backend caps and normalizes but does not model field-by-field, and
 *    a row written by an older prompt version can legitimately lack a
 *    newer section.
 *
 * Visual identity follows the Register direction (see CLAUDE.md):
 * hairline rules instead of card chrome, no shadows, one form-blue
 * accent, figures in mono, and the shared 24/20/16/13/11 type scale -
 * no one-off text-[Npx].
 */

// Poll cadence: fast at first (most runs finish in well under a
// minute), easing off so a long run isn't hitting the API every 3s.
const POLL_FAST_MS = 3_000;
const POLL_MID_MS = 5_000;
const POLL_SLOW_MS = 10_000;
const FAST_UNTIL_MS = 15_000;
const MID_UNTIL_MS = 45_000;
// Generous ceiling - just past the server-side Modal timeout (600s).
const MAX_POLL_MS = 11 * 60 * 1_000;

const TERMINAL: ReadonlyArray<string> = ["done", "failed"];

function isTerminal(status: string | undefined): boolean {
  return status !== undefined && TERMINAL.includes(status);
}

const FIT_LABELS: Record<string, { label: string; color: string; bg: string }> = {
  strong: { label: "Strong fit", color: "var(--color-good)", bg: "var(--color-good-bg)" },
  moderate: { label: "Moderate fit", color: "var(--color-warn)", bg: "var(--color-warn-bg)" },
  stretch: { label: "Stretch", color: "var(--color-bad)", bg: "var(--color-bad-bg)" },
};

const IMPORTANCE_COLORS: Record<string, string> = {
  high: "var(--color-bad)",
  medium: "var(--color-warn)",
  low: "var(--color-text-faint)",
};

export function DeepDivePanel({ jobId }: { jobId: number }) {
  const [deepDiveId, setDeepDiveId] = useState<number | null>(null);
  // Set when polling gives up at MAX_POLL_MS, so the copy can say so
  // instead of sitting on a spinner forever.
  const [pollTimedOut, setPollTimedOut] = useState(false);
  const startedAtRef = useRef<number | null>(null);

  const start = useMutation({
    mutationFn: () => startDeepDive(jobId),
    onMutate: () => {
      setPollTimedOut(false);
      startedAtRef.current = Date.now();
    },
    onSuccess: (data: DeepDiveResponse) => setDeepDiveId(data.id),
  });

  const poll = useQuery({
    queryKey: ["deep-dive", deepDiveId],
    queryFn: () => getDeepDive(deepDiveId as number),
    // Only polls once a run has actually been started in this session.
    enabled: deepDiveId !== null && !pollTimedOut,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (isTerminal(status)) return false;
      const elapsed = Date.now() - (startedAtRef.current ?? Date.now());
      if (elapsed > MAX_POLL_MS) return false;
      if (elapsed < FAST_UNTIL_MS) return POLL_FAST_MS;
      if (elapsed < MID_UNTIL_MS) return POLL_MID_MS;
      return POLL_SLOW_MS;
    },
    // A finished run never changes again, so don't re-fetch it on focus.
    refetchOnWindowFocus: false,
  });

  // Flip to the timed-out message once the ceiling is passed and the run
  // still hasn't reached a terminal state. Separate from refetchInterval
  // (which can only stop polling, not render anything) so the user gets
  // an explanation rather than a silently frozen panel.
  useEffect(() => {
    if (deepDiveId === null || pollTimedOut) return;
    if (isTerminal(poll.data?.status)) return;
    const startedAt = startedAtRef.current;
    if (startedAt === null) return;
    const remaining = MAX_POLL_MS - (Date.now() - startedAt);
    const timer = setTimeout(() => setPollTimedOut(true), Math.max(0, remaining));
    return () => clearTimeout(timer);
  }, [deepDiveId, pollTimedOut, poll.data?.status]);

  function runAgain() {
    setDeepDiveId(null);
    start.reset();
    start.mutate();
  }

  // The record in hand: whatever the last poll returned, else whatever
  // the POST itself returned (which already carries a full record, and
  // for an already-finished run comes back `done` with its result).
  const record = poll.data ?? start.data ?? null;
  const startErrorInfo = deepDiveErrorInfo(start.error);

  return (
    <div className="border border-border bg-surface p-6">
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold text-text">Resume Deep-Dive</h2>
        {record?.status === "done" && record.result?.fit_rating && (
          <FitBadge rating={String(record.result.fit_rating)} />
        )}
      </div>
      <p className="mb-4 text-sm text-text-subtle">
        A deeper read of your active resume against this specific posting — strengths with
        evidence, gaps worth closing, and concrete resume edits.
      </p>

      <Body
        record={record}
        isStarting={start.isPending}
        startError={start.error}
        startErrorInfo={startErrorInfo}
        pollError={poll.error}
        pollTimedOut={pollTimedOut}
        onRun={() => start.mutate()}
        onRetry={runAgain}
      />
    </div>
  );
}

function Body({
  record,
  isStarting,
  startError,
  startErrorInfo,
  pollError,
  pollTimedOut,
  onRun,
  onRetry,
}: {
  record: DeepDiveResponse | null;
  isStarting: boolean;
  startError: unknown;
  startErrorInfo: { detail: string; code: string } | null;
  pollError: unknown;
  pollTimedOut: boolean;
  onRun: () => void;
  onRetry: () => void;
}) {
  // --- start failed, with a structured reason we can word properly ---
  if (startErrorInfo) {
    if (startErrorInfo.code === "no_active_resume") {
      return (
        <Notice tone="muted" title="No active resume">
          Upload a resume on the Resume page first — a Deep-Dive is written against whichever
          version is active.
        </Notice>
      );
    }
    if (startErrorInfo.code === "budget_exhausted") {
      return (
        <Notice tone="muted" title="Deep-Dives are paused for this month">
          This month&apos;s analysis budget has been used up. Deep-Dives will be available again
          at the start of next month — nothing is wrong on your end.
        </Notice>
      );
    }
    if (startErrorInfo.code === "modal_unavailable") {
      return (
        <Notice tone="muted" title="Deep-Dive isn&apos;t available right now" onRetry={onRetry}>
          {startErrorInfo.detail}
        </Notice>
      );
    }
    if (startErrorInfo.code === "rate_limited") {
      return (
        <Notice tone="muted" title="Too many runs just now" onRetry={onRetry}>
          {startErrorInfo.detail}
        </Notice>
      );
    }
    return (
      <Notice tone="bad" title="Could not start the Deep-Dive" onRetry={onRetry}>
        {startErrorInfo.detail}
      </Notice>
    );
  }

  // --- start failed for an unexpected reason (network, 500, timeout) ---
  if (startError) {
    return (
      <Notice tone="bad" title="Could not start the Deep-Dive" onRetry={onRetry}>
        Something went wrong reaching the server. Please try again.
      </Notice>
    );
  }

  if (isStarting && !record) {
    return <Progress label="Starting the analysis…" />;
  }

  // --- idle: nothing started in this session yet ---
  if (!record) {
    return (
      <button
        type="button"
        onClick={onRun}
        className="inline-flex items-center gap-2 bg-accent px-5 py-2.5 text-sm font-semibold text-white hover:bg-accent-hover"
      >
        Run Deep-Dive
      </button>
    );
  }

  if (record.status === "done" && record.result) {
    return <Result result={record.result} onRetry={onRetry} />;
  }

  if (record.status === "failed") {
    return (
      <Notice tone="bad" title="The analysis didn&apos;t finish" onRetry={onRetry} retryLabel="Try again">
        {record.error ?? "It failed for an unknown reason. Please try again."}
      </Notice>
    );
  }

  if (pollTimedOut) {
    return (
      <Notice
        tone="muted"
        title="This is taking longer than expected"
        onRetry={onRetry}
        retryLabel="Start a new run"
      >
        The analysis has been running for over ten minutes, so we&apos;ve stopped checking. It may
        still finish — reload this page to check again, or start a new run.
      </Notice>
    );
  }

  // A poll that errors mid-run isn't fatal: the run may well still be
  // going, so say so rather than declaring failure.
  if (pollError) {
    return (
      <Notice tone="muted" title="Lost contact while checking progress" onRetry={onRetry}>
        The analysis may still be running. Reload this page to check again, or start a new run.
      </Notice>
    );
  }

  return <Progress label="Analyzing your resume against this role…" />;
}

function Progress({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 border border-border bg-surface-alt px-4 py-3">
      <span
        className="h-2 w-2 animate-pulse"
        style={{ backgroundColor: "var(--color-accent)" }}
        aria-hidden="true"
      />
      <div className="min-w-0">
        <p className="text-sm text-text-muted" role="status">
          {label}
        </p>
        <p className="font-mono text-2xs text-text-faintest">
          This usually takes under a minute. You can leave this page open.
        </p>
      </div>
    </div>
  );
}

function Notice({
  tone,
  title,
  children,
  onRetry,
  retryLabel = "Try again",
}: {
  tone: "muted" | "bad";
  title: string;
  children: React.ReactNode;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  const bad = tone === "bad";
  return (
    <div
      className="border border-l-2 px-4 py-3"
      style={
        bad
          ? { backgroundColor: "var(--color-bad-bg)", borderColor: "var(--color-border)", borderLeftColor: "var(--color-bad)" }
          : { backgroundColor: "var(--color-surface-alt)", borderColor: "var(--color-border)", borderLeftColor: "var(--color-border-strong)" }
      }
    >
      <p className="mb-1 text-sm font-semibold text-text">{title}</p>
      <p className="text-sm leading-relaxed text-text-subtle">{children}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 border border-border-strong px-3 py-1.5 text-sm font-medium text-text hover:bg-surface"
        >
          {retryLabel}
        </button>
      )}
    </div>
  );
}

function FitBadge({ rating }: { rating: string }) {
  const meta = FIT_LABELS[rating];
  if (!meta) {
    // An unrecognized future rating still renders, just neutrally.
    return (
      <span className="border border-border px-2 py-0.5 font-mono text-2xs uppercase tracking-[0.04em] text-text-muted">
        {rating}
      </span>
    );
  }
  return (
    <span
      className="border px-2 py-0.5 font-mono text-2xs uppercase tracking-[0.04em]"
      style={{ color: meta.color, backgroundColor: meta.bg, borderColor: meta.color }}
    >
      {meta.label}
    </span>
  );
}

function Section({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  return (
    <div className="mt-5 border-t border-divider pt-4">
      <div className="mb-2 flex items-center gap-2">
        <h3 className="text-sm font-semibold text-text">{title}</h3>
        {count !== undefined && (
          <span className="font-mono text-2xs text-text-faintest">{count}</span>
        )}
      </div>
      {children}
    </div>
  );
}

function Result({ result, onRetry }: { result: DeepDiveResult; onRetry: () => void }) {
  // Defensive reads throughout - see this module's docstring and
  // types/api.ts's DeepDiveResult.
  const strengths: DeepDiveStrength[] = Array.isArray(result.strengths) ? result.strengths : [];
  const gaps: DeepDiveGap[] = Array.isArray(result.gaps) ? result.gaps : [];
  const edits: DeepDiveResumeEdit[] = Array.isArray(result.resume_edits) ? result.resume_edits : [];
  const interview: string[] = Array.isArray(result.interview_focus) ? result.interview_focus : [];
  const caveats: string[] = Array.isArray(result.caveats) ? result.caveats : [];

  return (
    <div>
      {result.summary && (
        <p className="text-sm leading-relaxed text-text-secondary">{result.summary}</p>
      )}

      {strengths.length > 0 && (
        <Section title="Strengths" count={strengths.length}>
          <ul className="flex flex-col gap-2.5">
            {strengths.map((s, i) => (
              <li key={`${s?.point ?? "strength"}-${i}`} className="border-l-2 pl-3" style={{ borderLeftColor: "var(--color-good)" }}>
                <p className="text-sm font-medium text-text">{s?.point}</p>
                {s?.resume_evidence && (
                  <p className="mt-0.5 text-sm leading-relaxed text-text-subtle">
                    {s.resume_evidence}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {gaps.length > 0 && (
        <Section title="Gaps" count={gaps.length}>
          <ul className="flex flex-col gap-3">
            {gaps.map((g, i) => (
              <li key={`${g?.skill ?? "gap"}-${i}`} className="border border-dashed border-border-strong bg-surface-alt p-3">
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm text-text">{g?.skill}</span>
                  {g?.importance && (
                    <span
                      className="font-mono text-2xs uppercase tracking-[0.04em]"
                      style={{ color: IMPORTANCE_COLORS[g.importance] ?? "var(--color-text-faint)" }}
                    >
                      {g.importance}
                    </span>
                  )}
                </div>
                {g?.why_it_matters && (
                  <p className="text-sm leading-relaxed text-text-subtle">{g.why_it_matters}</p>
                )}
                {g?.how_to_close && (
                  <p className="mt-1.5 text-sm leading-relaxed text-text-muted">
                    <span className="font-mono text-2xs uppercase tracking-[0.04em] text-text-faint">
                      To close:{" "}
                    </span>
                    {g.how_to_close}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {edits.length > 0 && (
        <Section title="Resume edits" count={edits.length}>
          <ul className="flex flex-col gap-2">
            {edits.map((e, i) => (
              <li key={`${e?.section ?? "edit"}-${i}`} className="flex flex-col gap-0.5 border-b border-divider pb-2 last:border-b-0 last:pb-0">
                <span className="font-mono text-2xs uppercase tracking-[0.04em] text-text-faint">
                  {e?.section}
                </span>
                <span className="text-sm leading-relaxed text-text-secondary">{e?.suggestion}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {interview.length > 0 && (
        <Section title="Interview focus" count={interview.length}>
          <ul className="flex flex-col gap-1.5">
            {interview.map((item, i) => (
              <li key={`${item}-${i}`} className="flex gap-2 text-sm leading-relaxed text-text-secondary">
                <span className="mt-1.5 h-1 w-1 flex-none bg-accent" aria-hidden="true" />
                {item}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {caveats.length > 0 && (
        <Section title="Caveats">
          <ul className="flex flex-col gap-1">
            {caveats.map((item, i) => (
              <li key={`${item}-${i}`} className="font-mono text-2xs leading-relaxed text-text-faintest">
                {item}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-divider pt-4">
        <button
          type="button"
          onClick={onRetry}
          className="border border-border-strong px-3 py-1.5 text-sm font-medium text-text hover:bg-surface-alt"
        >
          Run again
        </button>
        <span className="font-mono text-2xs text-text-faintest">
          LLM-generated from your resume and this posting. Check it before acting on it.
        </span>
      </div>
    </div>
  );
}

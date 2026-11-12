"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { deepDiveErrorInfo, getDeepDive, startDeepDive } from "@/lib/api";
import { timeAgo } from "@/lib/theme";
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
 * no one-off text-[Npx]. The finished report is laid out as a report
 * rather than a flat stack of rows: a verdict header carrying the fit
 * stamp and the summary at reading size, then ruled sections with mono
 * counts, a reading measure capped so lines stay short on wide screens.
 *
 * The backend reports no progress stages, so the running state shows an
 * honest indeterminate bar plus a real elapsed counter - never a faked
 * step sequence.
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

/** Gaps beyond this many are folded behind a "Show N more" control. */
const GAPS_SHOWN_BY_DEFAULT = 3;
/** How long the Copy button holds its "Copied" confirmation. */
const COPIED_FEEDBACK_MS = 2_000;

const TERMINAL: ReadonlyArray<string> = ["done", "failed"];

function isTerminal(status: string | undefined): boolean {
  return status !== undefined && TERMINAL.includes(status);
}

/**
 * The fit rating as a ruled stamp. The word itself carries the verdict -
 * the colour is a second, redundant signal, never the only one. Stretch
 * is deliberately neutral rather than red: a stretch role is a real
 * option worth seeing, not an error.
 */
const FIT_META: Record<string, { label: string; color: string }> = {
  strong: { label: "Strong fit", color: "var(--color-good)" },
  moderate: { label: "Moderate fit", color: "var(--color-warn)" },
  stretch: { label: "Stretch", color: "var(--color-text-subtle)" },
};

/** high -> 3 filled steps and the hottest colour, low -> 1 and neutral. */
const IMPORTANCE_META: Record<string, { steps: number; color: string }> = {
  high: { steps: 3, color: "var(--color-bad)" },
  medium: { steps: 2, color: "var(--color-warn)" },
  low: { steps: 1, color: "var(--color-text-faint)" },
};

const IMPORTANCE_RANK: Record<string, number> = { high: 0, medium: 1, low: 2 };

/**
 * High, then medium, then low, stable within a level (Array#sort is
 * stable, so an unsorted-equal pair keeps the model's own ordering).
 * An unrecognised importance sorts last rather than being dropped.
 */
function sortGapsByImportance(gaps: DeepDiveGap[]): DeepDiveGap[] {
  return [...gaps].sort(
    (a, b) =>
      (IMPORTANCE_RANK[a?.importance] ?? 3) - (IMPORTANCE_RANK[b?.importance] ?? 3),
  );
}

export function DeepDivePanel({
  jobId,
  jobTitle,
  companyName,
}: {
  jobId: number;
  /** Shown in the report's meta line. Optional - the panel works without it. */
  jobTitle?: string;
  companyName?: string;
}) {
  const [deepDiveId, setDeepDiveId] = useState<number | null>(null);
  // Set when polling gives up at MAX_POLL_MS, so the copy can say so
  // instead of sitting on a spinner forever.
  const [pollTimedOut, setPollTimedOut] = useState(false);
  // The same instant twice over: the ref is read from refetchInterval
  // and the timeout effect (neither of which may depend on a re-render),
  // the state drives the elapsed counter the running state renders.
  const startedAtRef = useRef<number | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);

  const start = useMutation({
    mutationFn: () => startDeepDive(jobId),
    onMutate: () => {
      setPollTimedOut(false);
      const now = Date.now();
      startedAtRef.current = now;
      setStartedAt(now);
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
  const isReport = record?.status === "done" && Boolean(record.result);

  return (
    <section className="border border-border bg-surface p-6" aria-labelledby="deep-dive-title">
      <h2 id="deep-dive-title" className="text-lg font-semibold tracking-tight text-text">
        Resume Deep-Dive
      </h2>

      {isReport ? (
        <ReportMeta
          jobTitle={jobTitle}
          companyName={companyName}
          generatedAt={record?.updated_at ?? record?.created_at ?? null}
        />
      ) : (
        <p className="mt-1.5 max-w-[65ch] text-sm leading-relaxed text-text-subtle">
          A deeper read of your active resume against this specific posting — strengths with
          evidence, gaps worth closing, and concrete resume edits.
        </p>
      )}

      <div className="mt-4">
        <Body
          record={record}
          isStarting={start.isPending}
          startedAt={startedAt}
          startError={start.error}
          startErrorInfo={startErrorInfo}
          pollError={poll.error}
          pollTimedOut={pollTimedOut}
          onRun={() => start.mutate()}
          onRetry={runAgain}
        />
      </div>
    </section>
  );
}

/**
 * The report's meta line: which posting this was written against, and
 * when. Doubles as the panel's polite live region, so finishing a run
 * is announced rather than just appearing.
 */
function ReportMeta({
  jobTitle,
  companyName,
  generatedAt,
}: {
  jobTitle?: string;
  companyName?: string;
  generatedAt: string | null;
}) {
  const when = timeAgo(generatedAt);
  return (
    <p
      role="status"
      aria-live="polite"
      className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-text-subtle"
    >
      {jobTitle && <span className="font-medium text-text-muted">{jobTitle}</span>}
      {jobTitle && companyName && <span aria-hidden="true">·</span>}
      {companyName && <span>{companyName}</span>}
      {when && (
        <>
          {(jobTitle || companyName) && <span aria-hidden="true">·</span>}
          <span className="font-mono text-xs text-text-faint">Generated {when}</span>
        </>
      )}
    </p>
  );
}

function Body({
  record,
  isStarting,
  startedAt,
  startError,
  startErrorInfo,
  pollError,
  pollTimedOut,
  onRun,
  onRetry,
}: {
  record: DeepDiveResponse | null;
  isStarting: boolean;
  startedAt: number | null;
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
    return <Progress label="Starting the analysis…" startedAt={startedAt} />;
  }

  // --- idle: nothing started in this session yet ---
  if (!record) {
    return (
      <button
        type="button"
        onClick={onRun}
        className="inline-flex items-center gap-2 rounded-sm bg-accent px-5 py-2.5 text-sm font-semibold text-white hover:bg-accent-hover"
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

  return <Progress label="Analyzing your resume against this role…" startedAt={startedAt} />;
}

/**
 * The running state. The backend reports no stages at all, so this says
 * only what is actually known: that it is running, for how long, and
 * roughly how long that usually takes. The bar is indeterminate by
 * design and is not animated under prefers-reduced-motion (see
 * .deep-dive-bar in globals.css).
 */
function Progress({ label, startedAt }: { label: string; startedAt: number | null }) {
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 1_000);
    return () => clearInterval(tick);
  }, []);

  const elapsed = startedAt === null ? 0 : Math.max(0, Math.floor((now - startedAt) / 1_000));

  return (
    <div className="max-w-[65ch] border border-border bg-surface-alt px-4 py-3.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p role="status" aria-live="polite" className="text-sm font-medium text-text-muted">
          {label}
        </p>
        <p className="font-mono text-xs tabular-nums text-text-faint">
          <span aria-hidden="true">{elapsed}s</span>
          <span className="sr-only">{elapsed} seconds elapsed</span>
        </p>
      </div>

      <div
        className="mt-3 h-1 w-full overflow-hidden bg-border"
        role="progressbar"
        aria-label="Deep-Dive analysis in progress"
      >
        <div className="deep-dive-bar h-full w-1/3 bg-accent" />
      </div>

      <p className="mt-2 text-xs text-text-faintest">
        Usually takes 10 to 20 seconds. You can leave this page open.
      </p>
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
      role="status"
      aria-live="polite"
      className="max-w-[65ch] border border-l-2 px-4 py-3.5"
      style={
        bad
          ? {
              backgroundColor: "var(--color-bad-bg)",
              borderColor: "var(--color-border)",
              borderLeftColor: "var(--color-bad)",
            }
          : {
              backgroundColor: "var(--color-surface-alt)",
              borderColor: "var(--color-border)",
              borderLeftColor: "var(--color-border-strong)",
            }
      }
    >
      <p className="mb-1 text-sm font-semibold text-text">{title}</p>
      <p className="text-sm leading-relaxed text-text-subtle">{children}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-sm border border-border-strong bg-surface px-3 py-1.5 text-sm font-medium text-text hover:bg-surface-alt"
        >
          {retryLabel}
        </button>
      )}
    </div>
  );
}

/** The fit verdict, as a ruled stamp with the rating spelled out. */
function FitStamp({ rating }: { rating: string }) {
  const meta = FIT_META[rating];
  const color = meta?.color ?? "var(--color-text-muted)";
  const label = meta?.label ?? rating;
  return (
    <span
      className="inline-flex flex-none items-center border-2 px-2.5 py-1 font-mono text-xs font-semibold uppercase tracking-[0.1em]"
      style={{ color, borderColor: color }}
    >
      {label}
    </span>
  );
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-6 border-t border-border pt-4">
      <div className="mb-3 flex items-baseline gap-2">
        <h3 className="text-sm font-semibold uppercase tracking-[0.08em] text-text">{title}</h3>
        {count !== undefined && (
          <span className="font-mono text-xs text-text-faint">{count}</span>
        )}
      </div>
      {children}
    </section>
  );
}

/** A labelled block inside a gap - "Why it matters" / "How to close it". */
function GapNote({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1 font-mono text-xs uppercase tracking-[0.06em] text-text-faint">{label}</p>
      <p className="text-sm leading-relaxed text-text-secondary">{children}</p>
    </div>
  );
}

/** Importance as a three-step bar plus the word - never colour alone. */
function ImportanceTag({ importance }: { importance: string }) {
  const meta = IMPORTANCE_META[importance];
  const color = meta?.color ?? "var(--color-text-faint)";
  const steps = meta?.steps ?? 0;
  return (
    <span
      className="inline-flex flex-none items-center gap-1.5 border px-1.5 py-0.5"
      style={{ borderColor: color }}
      aria-label={`${importance} importance`}
    >
      <span className="flex items-end gap-px" aria-hidden="true">
        {[1, 2, 3].map((step) => (
          <span
            key={step}
            className="h-2 w-1"
            style={{
              border: `1px solid ${color}`,
              backgroundColor: step <= steps ? color : "transparent",
            }}
          />
        ))}
      </span>
      <span
        className="font-mono text-2xs font-semibold uppercase tracking-[0.08em]"
        style={{ color }}
        aria-hidden="true"
      >
        {importance}
      </span>
    </span>
  );
}

/** Copies one resume-edit suggestion. A real button, so it is reachable
 *  and operable from the keyboard like any other control. */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), COPIED_FEEDBACK_MS);
    return () => clearTimeout(timer);
  }, [copied]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      // Clipboard denied or unavailable (an insecure origin, a browser
      // that refuses without a permission): stay silent rather than
      // claiming a copy that never happened.
      setCopied(false);
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? "Suggestion copied" : "Copy suggestion"}
      className="flex-none rounded-sm border border-border-strong bg-surface px-2 py-0.5 font-mono text-xs text-text-subtle hover:bg-surface-alt hover:text-text"
    >
      {copied ? "Copied" : "Copy"}
    </button>
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

  const sortedGaps = sortGapsByImportance(gaps);
  const [allGapsShown, setAllGapsShown] = useState(false);
  const hiddenGapCount = Math.max(0, sortedGaps.length - GAPS_SHOWN_BY_DEFAULT);
  const visibleGaps = allGapsShown ? sortedGaps : sortedGaps.slice(0, GAPS_SHOWN_BY_DEFAULT);

  return (
    <div className="max-w-[78ch]">
      {/* --- verdict --- */}
      {(result.fit_rating || result.summary) && (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:gap-4">
          {result.fit_rating && <FitStamp rating={String(result.fit_rating)} />}
          {result.summary && (
            <p className="max-w-[65ch] text-base leading-relaxed text-text-secondary">
              {result.summary}
            </p>
          )}
        </div>
      )}

      {strengths.length > 0 && (
        <Section title="Strengths" count={strengths.length}>
          <ul className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
            {strengths.map((s, i) => (
              <li key={`${s?.point ?? "strength"}-${i}`}>
                <p className="text-base font-semibold leading-snug text-text">{s?.point}</p>
                {s?.resume_evidence && (
                  <blockquote
                    className="mt-1.5 border-l-2 pl-3"
                    style={{ borderLeftColor: "var(--color-accent)" }}
                  >
                    <p className="font-mono text-xs uppercase tracking-[0.06em] text-text-faint">
                      From your resume
                    </p>
                    <p className="mt-0.5 text-sm leading-relaxed text-text-subtle">
                      {s.resume_evidence}
                    </p>
                  </blockquote>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {sortedGaps.length > 0 && (
        <Section title="Gaps" count={sortedGaps.length}>
          <ul className="flex flex-col gap-4">
            {visibleGaps.map((g, i) => (
              <li
                key={`${g?.skill ?? "gap"}-${i}`}
                className="border-t border-divider pt-3 first:border-t-0 first:pt-0"
              >
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <h4 className="text-base font-semibold leading-snug text-text">{g?.skill}</h4>
                  {g?.importance && <ImportanceTag importance={String(g.importance)} />}
                </div>
                <div className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
                  {g?.why_it_matters && (
                    <GapNote label="Why it matters">{g.why_it_matters}</GapNote>
                  )}
                  {g?.how_to_close && <GapNote label="How to close it">{g.how_to_close}</GapNote>}
                </div>
              </li>
            ))}
          </ul>
          {hiddenGapCount > 0 && (
            <button
              type="button"
              onClick={() => setAllGapsShown((shown) => !shown)}
              className="mt-4 rounded-sm border border-border-strong bg-surface px-3 py-1.5 font-mono text-xs text-text-subtle hover:bg-surface-alt hover:text-text"
            >
              {allGapsShown ? "Show fewer" : `Show ${hiddenGapCount} more`}
            </button>
          )}
        </Section>
      )}

      {edits.length > 0 && (
        <Section title="Resume edits" count={edits.length}>
          <ul className="flex flex-col">
            {edits.map((e, i) => (
              <li
                key={`${e?.section ?? "edit"}-${i}`}
                className="flex items-start gap-3 border-b border-divider py-3 first:pt-0 last:border-b-0 last:pb-0"
              >
                <div className="min-w-0 flex-1">
                  <p className="font-mono text-xs uppercase tracking-[0.06em] text-text-faint">
                    {e?.section}
                  </p>
                  <p className="mt-1 text-sm leading-relaxed text-text-secondary">
                    {e?.suggestion}
                  </p>
                </div>
                {e?.suggestion && <CopyButton text={e.suggestion} />}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {interview.length > 0 && (
        <Section title="Interview focus" count={interview.length}>
          <ul className="flex flex-wrap gap-2">
            {interview.map((item, i) => (
              <li
                key={`${item}-${i}`}
                className="border border-border bg-surface-alt px-2.5 py-1.5 text-sm leading-snug text-text-secondary"
              >
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
              <li key={`${item}-${i}`} className="text-sm leading-relaxed text-text-faint">
                {item}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <div className="mt-6 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border pt-4">
        <button
          type="button"
          onClick={onRetry}
          className="rounded-sm border border-border-strong bg-surface px-3 py-1.5 text-sm font-medium text-text hover:bg-surface-alt"
        >
          Run again
        </button>
        <span className="font-mono text-xs text-text-faintest">
          LLM-generated from your resume and this posting. Check it before acting on it.
        </span>
      </div>
    </div>
  );
}

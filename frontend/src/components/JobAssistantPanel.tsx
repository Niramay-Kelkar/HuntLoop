"use client";

import { useState, type FormEvent } from "react";

import { draftAnswer } from "@/lib/api";
import { formatWage } from "@/lib/theme";
import type { DraftAnswerProvider, JobDetail } from "@/types/api";

/**
 * Slice 1 of the page-scoped job-detail chat assistant: a factual Q&A
 * panel answered entirely by structured lookup against fields already on
 * `detail` (the same JobDetail the page already fetched via GET
 * /jobs/{id}) - no LLM call, no new network request, no free-text input.
 *
 * Every answer is written to be honest about data gaps rather than
 * printing a raw null - see each answer* function below.
 *
 * Slice 2 (below, DraftingSection): resume-grounded free-text drafting
 * via POST /jobs/{id}/draft-answer (huntloop.api.routers.drafting) -
 * BYOK, the user's own provider API key. The key is kept ONLY in this
 * browser's localStorage (per provider, so switching providers doesn't
 * lose the other one's key) so the user doesn't have to retype it every
 * question - it is never sent anywhere except in the request body of
 * this one endpoint, and this app never persists it server-side. See
 * CLAUDE.md's TLS warning on this endpoint before pointing
 * NEXT_PUBLIC_API_URL at anything other than a localhost API.
 */

const DRAFT_PROVIDERS: { value: DraftAnswerProvider; label: string }[] = [
  { value: "groq", label: "Groq" },
  { value: "gemini", label: "Gemini" },
];

function apiKeyStorageKey(provider: DraftAnswerProvider): string {
  return `huntloop.draftAnswer.apiKey.${provider}`;
}

function readStoredApiKey(provider: DraftAnswerProvider): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(apiKeyStorageKey(provider)) ?? "";
  } catch {
    // Private-browsing / blocked storage - fall back to an empty field
    // rather than throwing.
    return "";
  }
}

function DraftingSection({ jobId }: { jobId: number }) {
  const [provider, setProvider] = useState<DraftAnswerProvider>("groq");
  const [apiKey, setApiKey] = useState<string>(() => readStoredApiKey("groq"));
  const [prompt, setPrompt] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [answer, setAnswer] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  function handleProviderChange(next: DraftAnswerProvider) {
    setProvider(next);
    setApiKey(readStoredApiKey(next));
  }

  function handleApiKeyChange(next: string) {
    setApiKey(next);
    if (typeof window === "undefined") return;
    try {
      if (next) window.localStorage.setItem(apiKeyStorageKey(provider), next);
      else window.localStorage.removeItem(apiKeyStorageKey(provider));
    } catch {
      // Ignore storage failures - the in-memory value still works for
      // this session.
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!prompt.trim() || !apiKey.trim() || status === "loading") return;

    setStatus("loading");
    setErrorMessage(null);
    setAnswer(null);
    try {
      const result = await draftAnswer(jobId, { prompt, provider, api_key: apiKey });
      setAnswer(result.answer);
      setStatus("success");
    } catch (err) {
      setStatus("error");
      setErrorMessage(describeDraftError(err));
    }
  }

  return (
    <div className="mt-6 flex flex-col gap-3 border-t border-divider pt-4">
      <div>
        <h3 className="text-sm font-semibold text-text">Draft an application answer</h3>
        <p className="mt-1 text-xs text-text-subtle">
          Uses your own API key, grounded in this job&apos;s description and your active resume.
          Your key is stored only in this browser and sent only to your chosen provider - never
          saved on our servers.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-2">
        <div className="flex flex-wrap gap-2">
          <select
            value={provider}
            onChange={(e) => handleProviderChange(e.target.value as DraftAnswerProvider)}
            className="border border-border bg-surface px-2 py-1.5 text-sm text-text"
          >
            {DRAFT_PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => handleApiKeyChange(e.target.value)}
            placeholder={`Your ${provider === "groq" ? "Groq" : "Gemini"} API key`}
            className="min-w-0 flex-1 border border-border bg-surface px-2 py-1.5 text-sm text-text"
          />
        </div>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g. Draft a why-this-company answer"
          rows={2}
          className="border border-border bg-surface px-2 py-1.5 text-sm text-text"
        />
        <button
          type="submit"
          disabled={!prompt.trim() || !apiKey.trim() || status === "loading"}
          className="self-start border border-border px-3 py-1.5 text-sm text-text hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
        >
          {status === "loading" ? "Drafting…" : "Draft answer"}
        </button>
      </form>

      {status === "error" && errorMessage && (
        <div className="border-l-2 border-bad bg-surface px-3 py-2 text-sm text-bad">{errorMessage}</div>
      )}
      {status === "success" && answer && (
        <div className="whitespace-pre-wrap border-l-2 border-accent bg-surface px-3 py-2 text-sm leading-relaxed text-text-secondary">
          {answer}
        </div>
      )}
    </div>
  );
}

function describeDraftError(err: unknown): string {
  if (err instanceof Error && "status" in err) {
    const status = (err as { status: number | null }).status;
    const detail = (err as { detail?: string }).detail;
    let parsedDetail: string | undefined;
    if (detail) {
      try {
        const parsed = JSON.parse(detail);
        parsedDetail = typeof parsed?.detail === "string" ? parsed.detail : undefined;
      } catch {
        parsedDetail = detail;
      }
    }
    if (status === 400) return parsedDetail ?? "Missing or invalid request - check your API key and prompt.";
    if (status === 401) return parsedDetail ?? "The provider rejected this API key.";
    if (status === 429) return parsedDetail ?? "Rate limited - try again shortly.";
    if (status === 404) return parsedDetail ?? "This job could not be found.";
    if (status === 502) return parsedDetail ?? "The provider call failed. Try again in a moment.";
    return parsedDetail ?? "Something went wrong drafting this answer.";
  }
  return "Could not reach the server.";
}

type Question = {
  id: string;
  label: string;
  answer: (detail: JobDetail) => string;
};

const QUESTIONS: Question[] = [
  { id: "sponsor", label: "Does this company sponsor visas?", answer: answerSponsor },
  { id: "salary", label: "What's the salary estimate?", answer: answerSalary },
  { id: "missing-skills", label: "What skills am I missing?", answer: answerMissingSkills },
  { id: "score-basis", label: "What's my match score based on?", answer: answerScoreBasis },
];

function answerSponsor(detail: JobDetail): string {
  const company = detail.company_name;
  if (detail.sponsor_check_status === "confirmed" && detail.sponsor) {
    const s = detail.sponsor;
    const wage = s.median_wage !== null ? `, median wage ${formatWage(s.median_wage)}` : "";
    return (
      `Yes — ${company} has confirmed H-1B sponsorship history: ${s.total_lcas_most_recent_fiscal_year} ` +
      `LCA filing(s) in FY${s.most_recent_fiscal_year}${wage}. This is based on DOL LCA disclosure data for ` +
      `the company overall, not a guarantee for this specific role.`
    );
  }
  if (detail.sponsor_check_status === "checked_no_match") {
    return (
      `We checked DOL LCA filing records for ${company} and found no sponsorship history on file. ` +
      `That doesn't guarantee they won't sponsor a visa — only that we found no record — so it's worth ` +
      `confirming directly with the recruiter.`
    );
  }
  return (
    `We haven't checked sponsorship history for ${company} yet — this is different from confirming they ` +
    `don't sponsor. Nothing to report either way right now.`
  );
}

function answerSalary(detail: JobDetail): string {
  if (!detail.salary_estimate) {
    return (
      `No salary estimate is available for this role. That's because ${detail.company_name} either has no ` +
      `resolved DOL sponsor match or no annual-wage filings on record to estimate from — not because the ` +
      `salary is known to be zero or unlisted by choice.`
    );
  }
  return (
    `Estimated around ${formatWage(detail.salary_estimate.amount)}/year. ${detail.salary_estimate.basis}.`
  );
}

function answerMissingSkills(detail: JobDetail): string {
  if (detail.missing_skills === null) {
    return (
      "Skills analysis hasn't run for this job yet, so I can't tell you what's missing. Check back once " +
      "it's been processed — this isn't the same as having no gaps."
    );
  }
  if (detail.missing_skills.length === 0) {
    return "No skill gaps identified — the analysis didn't find anything in this posting that your resume doesn't already cover.";
  }
  return `Based on the skills analysis, you're missing: ${detail.missing_skills.join(", ")}. These are mentioned in the posting but not evident on your active resume.`;
}

function answerScoreBasis(detail: JobDetail): string {
  if (detail.match_score === null) {
    return "There's no active resume on file, so no match score has been computed for this job.";
  }
  const pct = Math.round(detail.match_score * 100);
  if (detail.score_basis === "full") {
    return (
      `Your match score of ${pct}% blends resume-to-job semantic similarity with your matched/missing ` +
      `skills ratio — a full composite score.`
    );
  }
  return (
    `Your match score of ${pct}% is provisional — it's based on resume-to-job semantic similarity only. ` +
    `Skills analysis hasn't run for this job yet, so the skills component isn't included yet.`
  );
}

type Exchange = { id: string; question: string; answer: string };

export function JobAssistantPanel({ detail }: { detail: JobDetail }) {
  const [open, setOpen] = useState(true);
  const [history, setHistory] = useState<Exchange[]>([]);

  function ask(question: Question) {
    setHistory((prev) => [
      ...prev,
      { id: `${question.id}-${prev.length}`, question: question.label, answer: question.answer(detail) },
    ]);
  }

  return (
    <div className="border border-border bg-surface p-6">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between text-left"
      >
        <h2 className="text-base font-semibold text-text">Ask about this job</h2>
        <span className="font-mono text-xs text-text-subtle">{open ? "hide" : "show"}</span>
      </button>
      <p className="mt-1 text-sm text-text-subtle">
        Quick answers looked up directly from this job&apos;s data — no AI generation in this version.
      </p>

      {open && (
        <div className="mt-4 flex flex-col gap-4">
          <div className="flex flex-wrap gap-2">
            {QUESTIONS.map((q) => (
              <button
                key={q.id}
                type="button"
                onClick={() => ask(q)}
                className="border border-border px-3 py-1.5 text-sm text-text hover:border-accent hover:text-accent"
              >
                {q.label}
              </button>
            ))}
          </div>

          {history.length > 0 && (
            <div className="flex flex-col gap-3 border-t border-divider pt-4">
              {history.map((exchange) => (
                <div key={exchange.id} className="flex flex-col gap-1.5">
                  <div className="self-start bg-surface-alt px-3 py-1.5 text-sm font-medium text-text">
                    {exchange.question}
                  </div>
                  <div className="self-start border-l-2 border-accent bg-surface px-3 py-2 text-sm leading-relaxed text-text-secondary">
                    {exchange.answer}
                  </div>
                </div>
              ))}
            </div>
          )}

          <DraftingSection jobId={detail.id} />
        </div>
      )}
    </div>
  );
}

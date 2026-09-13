"use client";

import { useState } from "react";

import type { JobDetail } from "@/types/api";
import { formatWage } from "@/lib/theme";

/**
 * Slice 1 of the page-scoped job-detail chat assistant: a factual Q&A
 * panel answered entirely by structured lookup against fields already on
 * `detail` (the same JobDetail the page already fetched via GET
 * /jobs/{id}) - no LLM call, no new network request, no free-text input.
 * Free-text NLU and resume-grounded drafting are a separate, later slice
 * (see the BYOK-shaped provider interface work tracked for it).
 *
 * Every answer is written to be honest about data gaps rather than
 * printing a raw null - see each answer* function below.
 */

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
        </div>
      )}
    </div>
  );
}

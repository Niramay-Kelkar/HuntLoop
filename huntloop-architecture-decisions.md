# HuntLoop architecture decisions

Longer-form design notes that don't fit as a one-liner in CLAUDE.md's
"Key architectural decisions" list. CLAUDE.md stays the fast-orientation
index; this file is where a decision's full reasoning, alternatives, and
"not yet wired" design work live. SESSIONS.md remains the chronological
log.

---

## Multi-provider skills-matching routing (designed 2026-08-29, NOT yet wired)

### Context

`huntloop.skills_matching` (Groq, `openai/gpt-oss-20b`) is the sole
production backend for matched/missing skills extraction. Groq's free
tier has a hard **200,000 tokens-per-day (TPD)** cap. A full
`job_postings` backfill (~600 rows, batched 5/call) is a ~3.4-day job
because of it; `scripts/backfill_skills_matching.py` already self-paces
and stops cleanly on `DailyQuotaExhausted`, re-running daily until caught
up. The daily *incremental* volume (~1–2 relevant new jobs/day) fits one
day's budget trivially — the TPD cap only bites during bulk backfill.

Two alternative backends have been built, both contract-identical and
deliberately unwired:

- `huntloop.skills_matching_ollama` — local model. **NO-GO on current
  hardware** (2026-08-29): every RAM-viable model failed quality.
- `huntloop.skills_matching_gemini` — Google Gemini free tier.
  Evaluated 2026-08-29 (see SESSIONS.md + the verdict below).

### Gemini free-tier limits (checked 2026-08-29, per-account, from AI Studio)

Google **removed the static per-model free-tier rate-limit table** from
`ai.google.dev/gemini-api/docs/rate-limits` (page last updated
2026-08-18; it now says "view your active rate limits in AI Studio").
Limits are per-account now. This account's free-tier text-model limits:

| Model | RPM | TPM | RPD |
|---|---|---|---|
| Gemini 2.5 Flash | 5 | 250K | **20** |
| Gemini 2.5 Flash Lite | 10 | 250K | **20** |
| Gemini 3.1 Flash Lite | 15 | 250K | **500** |
| Gemini 3.5 Flash Lite | 15 | 250K | **500** |
| Gemini 3.x Flash (non-lite) | 5 | 250K | 20 |
| Gemma 4 26B / 31B | 30 | 16K | 14,400 |

The 2.5-generation models have been cut to **20 RPD** on the free tier
(the last *documented* figure was 1,000 RPD for 2.5 Flash-Lite — this is
the "reportedly changed recently" the task flagged, and it is real and
large). The only viable free options for this workload are the **3.x
`*-flash-lite`** models: **15 RPM / 250K TPM / 500 RPD**, no separate TPD
cap surfaced. Gemma 4's 14,400 RPD is tempting but its 16K TPM is too
small for a resume + a batch of 5 job descriptions (~6–7K prompt tokens
plus output — one big batch call can breach 16K TPM), so it's not a fit
for the batch path.

**Effective fallback capacity (Gemini 3.5 Flash Lite):** 500 RPD ×
batch-of-5 = **2,500 jobs/day**, and 250K TPM dwarfs Groq's 8K TPM.
As a *fallback* for the days Groq's 200K TPD is spent, that's ample —
it would clear a full backlog in ~1 day instead of ~3.4.

### Verdict on Gemini quality

11-job side-by-side vs the Groq baseline, 2026-08-29 (same jobs as the
Ollama validation: 9 Step 4/5 samples + Duolingo soft-match + Palantir
Deployment Strategist). Full data in `scratch_gemini_validation.json`;
SESSIONS.md 2026-08-29 has the job-by-job breakdown.

**GO — good enough as a fallback, not as a replacement.**

- **Reliability:** Gemini 11/11 successful; Groq 9/11 (two
  `json_validate_failed` 400s from gpt-oss-20b's JSON-mode validator —
  a real Groq wart). A fallback that's *more* reliable on raw structured
  output is a genuine plus.
- **Latency:** ~0.9s/call typical (one 86s cold-start anomaly, not
  representative), comparable to Groq's ~1.3s.
- **Quality — equivalent** on the 6 clear-cut jobs (both correctly
  return `matched: []` for the irrelevant creative/fraud roles; both
  catch the Chief-of-Staff soft matches). **Gemini better** on two
  high-similarity Palantir SWE jobs where Groq returned `matched: []`
  outright. **Groq better** on (a) the Duolingo "Senior Data Science
  Manager" soft-match case — Gemini matched only literally-stated
  `Python`/`SQL` and dropped the inferable ML/pipeline skills to
  missing, the same conservative direction (but far milder) as the
  rejected Ollama models; and (b) thoroughness on "AI Conversation
  Designer" (Groq 12 matched vs Gemini 1).
- **The 53-item full-resume-dump failure mode did NOT reproduce** on
  either provider this run (Deployment Strategist: Groq
  `['data','software']`, Gemini `[]` — both weak, neither catastrophic).
  `MAX_PLAUSIBLE_MATCHED_SKILLS = 20` stays as the backstop regardless.

**Known limitation to carry:** Gemini 3.5 Flash Lite is more
conservative on soft/inferred matches than gpt-oss-20b. Acceptable for a
*fallback* whose whole job is clearing backlog that would otherwise wait
a full day for Groq's TPD window to reset — but it's why Groq stays
primary, not a coin-flip.

### Routing design (Groq primary, Gemini fallback)

**Principle:** reuse the existing self-pacing batch-loop pattern from
`scripts/backfill_skills_matching.py`. No queue, no worker, no new
infra — there is still no live-latency requirement. The only new
behavior is: when Groq says "daily quota gone", don't stop the whole
run — switch the backend and keep going until *that* one is exhausted
too, then stop.

**Shape:**

```
# huntloop/skills_matching_router.py  (NOT YET CREATED)

from huntloop import skills_matching as groq_backend
from huntloop import skills_matching_gemini as gemini_backend

PROVIDER_CHAIN = [groq_backend, gemini_backend]   # order = preference

class AllProvidersExhausted(Exception):
    """Every backend in PROVIDER_CHAIN has raised DailyQuotaExhausted
    within this run. The caller stops for the day, same as it does today
    on Groq's DailyQuotaExhausted."""

def match_skills_batch(resume_text, job_descriptions, _state):
    """Same signature/return contract as a single backend's
    match_skills_batch, plus a mutable _state dict tracking which
    providers are exhausted this run. Tries the first non-exhausted
    provider; on that provider's DailyQuotaExhausted, marks it exhausted
    and falls through to the next. Raises AllProvidersExhausted when the
    chain is empty. Every non-quota failure still returns [None] * n,
    exactly as today (the batch is retried later, not switched away on a
    transient blip)."""
```

**Key decisions:**

1. **Fall over only on `DailyQuotaExhausted`, never on a `None` return.**
   A `None` (per-minute 429, timeout, malformed JSON) is transient and
   already handled by the existing "leave NULL, reprocess next run"
   contract. Switching providers on a transient blip would burn the
   fallback's quota for no reason and make runs non-deterministic.

2. **Provider-exhaustion state is per-run, in memory.** No new table,
   no persisted cursor. A fresh `backfill_skills_matching.py` invocation
   starts by trying Groq again — correct, because the TPD windows reset
   on their own schedules (Groq midnight UTC-ish, Gemini RPD midnight
   Pacific) and the script is already designed to be re-run daily.

3. **The router is the only integration point.**
   `scripts/backfill_skills_matching.py` imports four names from
   `huntloop.skills_matching` today: `match_skills_batch`,
   `DailyQuotaExhausted`, `MODEL_NAME`, `_BATCH_SYSTEM_PROMPT` (the last
   two feed its token-estimation pacing). The router must re-export all
   four: `match_skills_batch` (dispatching), `AllProvidersExhausted`
   (replacing the `DailyQuotaExhausted` catch), and `MODEL_NAME` /
   `_BATCH_SYSTEM_PROMPT` proxied from whichever provider is currently
   active (they're near-identical across providers anyway — the prompt
   is byte-identical, the model name only labels logs). So the script
   changes exactly: one import line and one `except` clause. Nothing
   else in the codebase imports a skills-matching backend directly. The
   daily-orchestrator wrapper (`scripts/run_orchestrator_cron.sh`
   stage 2) is unchanged.
   **Note the TPM pacing:** `backfill_skills_matching.py`'s `TokenPacer`
   is tuned to Groq's 8K TPM. Gemini's free tier is 250K TPM — 30x
   headroom — so while the fallback is active the pacer just never
   sleeps, which is fine. If Gemini ever became primary, the pacer's
   `TARGET_TPM` should rise accordingly, but that's out of scope here.

4. **Batch size stays 5.** Validated for Groq's 8K per-request cap;
   Gemini's 250K TPM makes it a non-constraint there, so 5 is safe for
   both. No per-provider batch sizing.

5. **`MAX_PLAUSIBLE_MATCHED_SKILLS = 20` sanity filter stays, unchanged,
   in each backend.** It's model-agnostic (guards the full-resume-dump
   failure mode) and each backend already enforces it independently.

6. **Config:** `SKILLS_MATCHING_PROVIDERS` env var (comma-separated,
   default `groq,gemini`) to reorder or disable the chain without a code
   change — e.g. `groq` alone reproduces today's exact behavior.

**Explicitly out of scope of this design:** a live/interactive
skills-matching endpoint, concurrent multi-provider fan-out, a persisted
job queue, cost accounting, or automatic model-tier upgrades. If a
live-latency requirement ever appears, revisit — this design is for the
batch backfill path only.

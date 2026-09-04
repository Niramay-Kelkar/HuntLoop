# HuntLoop architecture decisions

Longer-form design notes that don't fit as a one-liner in CLAUDE.md's
"Key architectural decisions" list. CLAUDE.md stays the fast-orientation
index; this file is where a decision's full reasoning, alternatives, and
"not yet wired" design work live. SESSIONS.md remains the chronological
log.

---

## Multi-provider skills-matching routing (designed 2026-08-29, WIRED 2026-08-29 / Step K)

**Status: live.** `huntloop.skills_matching_router` dispatches Groq
primary → Gemini fallback; `scripts/backfill_skills_matching.py` uses it.
See the "Step K — wiring" section at the bottom for what was actually
built and the real verification numbers. The design notes below are kept
as the rationale.

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

### Real-scale demand check (2026-08-29, see SESSIONS.md)

Step G's `date_posted`-anchored volume methodology, re-run against the
full 380-company coverage: **~120 new relevant postings/day** (was ~2/day
at 5-company scale). Meanwhile real job descriptions from the wider set
average 9,496 chars (3.2x the original sample), which collapses Groq's
mean batch size from ~4 to ~1.6 and drops its real throughput to
**~55 jobs/day** — i.e. **Groq alone can no longer keep up**; the Gemini
fallback (still ~2,500/day at batch-5, its per-request limit is large)
is what makes this design sufficient (21x combined headroom). Three
items below are now load-bearing rather than nice-to-have:
- **`backfill_skills_matching.py` must filter `is_relevant = true`** —
  57% of the current NULL backlog is irrelevant postings.
- **Batch sizing must be per-provider** — `MAX_BATCH_ESTIMATED_TOKENS =
  7000` is a Groq per-request artifact; Gemini wants a much larger cap
  (else 500 RPD × 1.6 = 800/day, not 2,500). The "batch size stays 5"
  note below is superseded: Groq's real batch size is ~1.6 at these JD
  sizes.
- **Batch-5 quality on Gemini at ~17K-token prompts is unverified** (the
  Gemini validation was single-job).

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

---

### Step K — wiring (2026-08-29, see SESSIONS.md)

What was built, and where the design above changed once it met reality:

- **`huntloop/skills_matching_errors.py`** — a dependency-free module
  holding the two shared exceptions (`DailyQuotaExhausted`,
  `ProviderResponseInvalid`) so both backends raise the *same* class and
  neither drags in the other's API-key requirement.
- **`huntloop/skills_matching_router.py`** — `make_run_state()`,
  `match_skills_batch(resume, jds, state)`, `active_provider(state)`,
  `batch_limits(state)`, `run_summary(state)`, `AllProvidersExhausted`.
  `SKILLS_MATCHING_PROVIDERS` env (default `groq,gemini`); `groq` alone
  reproduces pre-routing behaviour and never imports the gemini backend.
- **Two failover triggers, not one** (design decision 4 was wrong):
  - `DailyQuotaExhausted` → provider marked spent for the whole run.
  - `ProviderResponseInvalid` (Groq `json_validate_failed` 400) → **just
    that batch** fails over; the provider stays primary. This is the
    Step I open item — without it those ~18%-of-calls failures silently
    became `[None]*n` and got retried against the same flaky provider.
- **Per-provider batch sizing + pacing** (design decision 4 fully
  reversed). Each backend module owns `MAX_BATCH_SIZE` /
  `MAX_BATCH_ESTIMATED_TOKENS` / `TARGET_TPM` / `MAX_RPM`. Groq:
  5 / 7,000 / 6,000 / 30 (artifacts of its real 8,000 per-request +
  8,000 TPM caps; RPM non-binding). Gemini: 5 / **16,000** / 200,000 /
  **14** — 16,000 computed from `250,000 TPM ÷ 15 RPM ≈ 16,666` (the
  request-size ceiling before sustained max-rate calls breach TPM),
  which at the real ~9.5k-char job descriptions yields a **measured
  batch size of 4.4** and **~2,200 jobs/day** (500 RPD × 4.4), not the
  2,500 the design guessed. The `MAX_RPM=14` was added after the Step K
  verification run: Gemini's small batches let the loop hit ~30 req/min,
  over its real 15 RPM cap (it didn't 429 that run, but a full-scale run
  would). `TokenPacer` now enforces both a tokens/min and a
  requests/min sliding-window bound. `backfill_skills_matching.py`
  chunks *incrementally*, re-reading `router.batch_limits(state)` (a
  4-tuple) before every batch, so the caps flip the moment Groq gets
  exhausted mid-run.
- **`is_relevant = true` filter** added to the backfill's query
  (`matched_skills IS NULL AND is_relevant IS TRUE`). Real effect:
  29,921 → 12,972 rows (16,949 / 56.6% irrelevant rows no longer burn
  quota).
- **Backlog logging** — `backfill_skills_matching.py` logs
  `skills-matching backlog: N relevant rows awaiting a result` at the
  start and end of every run (it runs daily via the orchestrator). One
  cheap INFO line is the leading indicator for a future capacity
  regression — Gemini's own free-tier limits changed 50× mid-project
  without notice, and re-deriving the whole volume analysis to notice is
  expensive.

**Real verification (Step K):**
- Groq real throughput: **~58 jobs/day** (mean 3,453 batched tok/job,
  batch 1.67 at 7,000 cap).
- Gemini real: batch **4.4**, ~12,816 tok/request (× 15 RPM = 192k/min,
  under the 250k TPM cap), **~2,200 jobs/day** (RPD-bound).
- Combined ≈ **2,257 jobs/day** → the 12,972-row backlog clears in
  **~5.7 days**.
- `json_validate_failed` → Gemini per-batch failover **fired on real
  Groq 400s** in the `--limit 250` verification run — 19 times (Groq's
  JSON mode was ~76% flaky that session, vs Step I's 18%); each failed
  one batch over to Gemini while Groq stayed primary.
- `DailyQuotaExhausted` → full failover **also fired for real**: Groq
  hit its TPD wall after 11 jobs (budget pre-spent that day), the router
  marked it exhausted and every subsequent batch went to Gemini with its
  own 16,000-token cap.
- Real `--limit 250` run: **242 stored / 8 left NULL (3.2%) / 26.7 min**;
  split groq 11 jobs, gemini 231. Backlog 12,972 → 12,730. Full clear at
  steady state (fresh Groq budget) ≈ **5.6 days**.

---

## Backup (3rd) skills-matching provider candidate list (documented 2026-08-30, NOT built)

**Purpose:** pre-considered short list only. Nothing here is ported,
wired, or validated. If `backfill_skills_matching.py`'s daily
`skills-matching backlog: N relevant rows awaiting a result` line ever
shows *sustained* growth with Groq→Gemini routing already active (i.e.
combined ~2,258 jobs/day is no longer keeping up, or Gemini's free tier
gets cut again the way its 2.5-gen RPD went 1,000→20), reach for this
list instead of researching under pressure. The router
(`SKILLS_MATCHING_PROVIDERS` env, per-provider `MAX_BATCH_SIZE` /
`MAX_BATCH_ESTIMATED_TOKENS` / `TARGET_TPM` / `MAX_RPM`) is already
shaped to take a third contract-identical backend module with only an
env change plus one new `huntloop/skills_matching_<name>.py`.

All rate-limit figures below pulled from the providers' current docs on
2026-08-30 — re-verify before acting, free tiers move fast (this project
has watched Groq, then Gemini, change limits mid-flight).

### Candidate 1 — Cerebras Inference (free tier)

- **Limits (inference-docs.cerebras.ai/support/rate-limits, June 2026
  revision):** 5 RPM · 30K TPM · 1M tokens/hour · **1M tokens/day**.
  Applies to the free-tier models `gpt-oss-120b` and `gemma-4-31b`.
  **Free-tier context window is capped at 8,192 tokens** — this is the
  real constraint for HuntLoop: the wider ATS set's job descriptions
  average ~9,500 chars (~2.4K tokens) and a resume + batch of several
  JDs plus JSON output will breach 8K context. Batch size would have to
  drop to ~2 JDs/call, and even single-JD calls on the longest postings
  risk truncation.
- **Structured output:** Yes, real support —
  `inference-docs.cerebras.ai/capabilities/structured-outputs`:
  `response_format` with a full JSON schema (nested objects, required
  fields, enums, `$ref`/`$defs`, number constraints) *and* simple
  `{"type": "json_object"}` JSON mode. Reliable enough for this task on
  paper; note a known third-party bug where Cerebras rejects schemas
  missing `additionalProperties` (agno issue #6013) — our schema is
  simple, low risk.
- **Effective capacity:** 1M TPD ÷ ~4K tokens per (resume + 2 JDs +
  output) call ≈ **~250 calls/day ≈ ~500 jobs/day** at the forced
  batch-of-2. TPM (30K) and RPM (5) are generous relative to that. Lower
  than Gemini's ~2,200/day but a real, independent bucket.
- **Catch:** the $5 free credits now require a verified payment method
  and expire in 30 days; the standing 1M TPD free tier itself does not,
  but confirm at signup.

### Candidate 2 — Mistral La Plateforme ("Experiment" free tier)

- **Limits (help.mistral.ai free-tier article / admin.mistral.ai
  console — Mistral stopped publishing exact numbers inline, they're
  per-workspace now): 1 request/second · 500K TPM · ~1 billion
  tokens/month**, across all API models including `mistral-large` and
  `mistral-small`. No separate per-day cap surfaced.
- **Structured output:** Yes —
  `response_format: {"type": "json_object"}` JSON mode on all models,
  plus custom JSON-schema structured outputs on recent models
  (`mistral-small-latest`, `mistral-large-latest`). Generally reliable;
  historically a bit looser than OpenAI/Gemini schema enforcement, so
  keep the `MAX_PLAUSIBLE_MATCHED_SKILLS = 20` backstop.
- **Effective capacity:** the binding limit is **~1B tokens/month ≈
  ~33M/day**. At ~7K tokens per (resume + batch-5) call that's
  effectively unlimited for HuntLoop's ~120 relevant jobs/day — 1 req/s
  is the only real throttle and is far above what the daily volume
  needs. Comfortably the highest-headroom option of the three.
- **Catch:** the free tier is explicitly labelled "for evaluation, not
  production" and Mistral reserves the right to rate-limit or revoke;
  no context-window problem (`mistral-small` is 128K). Quality for a
  short JSON extraction task is expected to be fine but is **unvalidated**
  — a side-by-side against the Groq baseline (same 11-job harness used
  for Gemini/Ollama) would be the first step if this is ever picked up.

### Candidate 3 — OpenRouter aggregated free models

- **Limits (openrouter.ai/docs/api_reference/limits): 20 RPM** on all
  `:free` model variants; **50 requests/day** with < $10 lifetime
  credit purchased, rising to **1,000 requests/day** after a one-time
  $10 purchase (lifetime unlock, balance can then go to zero). No
  separate TPM/TPD — request-count capped only.
- **Structured output:** Model-dependent, not uniform. OpenRouter
  passes `response_format` through to the upstream provider; JSON
  schema / `json_object` works reliably on the OpenAI/Gemini/Mistral-
  backed free routes but is best-effort or unsupported on some
  community-hosted free models, and free routes are frequently rate-
  limited upstream or rotated out. Least predictable of the three for a
  task that needs dependable JSON every call.
- **Effective capacity:** 1,000 req/day (after the $10 unlock) ×
  batch-of-5 ≈ **~5,000 jobs/day** in principle, but the practical
  ceiling is lower because free routes throttle and disappear. Main
  value is *breadth* — one API key fronting many models, so if a
  specific free model degrades you switch model strings, not providers.
- **Catch:** requires the $10 purchase to be useful (50/day is too low);
  free-route availability is not contractual.

### Next provider if backlog monitoring signals trouble

**Try Mistral La Plateforme first.** Reasoning:

1. **Highest real headroom** — ~1B tokens/month with no per-day request
   cap and a 128K context window means no forced batch-size reduction
   (unlike Cerebras's 8K context) and no request-count ceiling (unlike
   OpenRouter's 1,000/day). It slots into the existing per-provider
   token-based pacing cleanly.
2. **Native, documented JSON mode + schema support** on first-party
   models — more predictable than OpenRouter's pass-through lottery.
3. **Single first-party provider** — same integration shape as Groq and
   Gemini (one base URL, one key, OpenAI-compatible endpoint), so the
   third `skills_matching_mistral.py` backend is a near-copy of the
   Gemini one.

**Cerebras is the fallback-to-the-fallback** — genuinely fast and a
clean independent quota bucket, but the 8K free-tier context window
forces batch-of-2 and risks truncating the longest job descriptions,
which is exactly the failure mode the wider ATS set already made worse.
**OpenRouter is last** — only worth it for model breadth, needs the $10
unlock to clear 50 req/day, and its free routes are the least reliable
for guaranteed structured output.

Whichever is picked: run the existing 11-job side-by-side harness
(the one used for Gemini and Ollama — 9 Step 4/5 samples + the Duolingo
soft-match + Palantir Deployment Strategist) against the Groq baseline
before wiring, and keep `MAX_PLAUSIBLE_MATCHED_SKILLS = 20` as the
model-agnostic backstop.

### Outcome — Mistral built + validated 2026-09-03: WIRED, NOT RECOMMENDED, NOT in the default chain

Built `src/huntloop/skills_matching_mistral.py` (contract-identical to
the Gemini backend) and added `"mistral"` as a known third router stage,
appendable via `SKILLS_MATCHING_PROVIDERS=groq,gemini,mistral`. **The
default chain is unchanged (`groq,gemini`).**

**Live-limits re-check contradicted the 2026-08-30 research:** the
1 RPS / 500K TPM / ~1B-tokens/month figures apply to Mistral's *flagship*
models, which are now effectively pulled from the free tier —
`mistral-small-latest` returns HTTP 429 with
`x-ratelimit-limit-req-minute: 0` (the same silent cut Gemini made to its
2.5-gen models). Only the smaller `ministral-*` models are free-usable;
`ministral-8b-latest`'s live headers show 625,000 tokens/min and 188
req/min, no per-day/month header. The module defaults to
`ministral-8b-latest` with pacing constants read from those real headers
(`TARGET_TPM=500_000`, `MAX_RPM=120`).

**11-job harness verdict — POOR, worse than Gemini's was:**
- 7 of 11 results are full-résumé dumps exceeding
  `MAX_PLAUSIBLE_MATCHED_SKILLS = 20` (56, 53, 46, 44, 44, 42, 26 matched
  skills) — the batch path rejects these and leaves the rows NULL, so on
  the hard cases it produces *nothing usable*. Groq returns 0–7 on the
  same jobs and correctly `[]`/`[]` on Palantir "Deployment Strategist".
- Grounding inversion (résumé skills asserted as matched for jobs that
  don't ask for them) and short-phrase-rule violations (63 long/
  parenthetical entries across the 11 jobs).
- ~4x slower per call (6.1s vs 1.4s).
- Same conclusion as the 2026-08-29 Ollama 3B/7B experiment: an 8B-class
  model is too small for this extraction task. The Mistral models that
  might clear the bar aren't free-usable.

**Decision:** keep the module + wiring (the option exists and is
reproducible via `scripts/validate_mistral_skills_match.py`), but treat
`mistral` as a **last-resort capacity bucket only** — enable it if and
only if Groq *and* Gemini are ever both daily-walled and a stalled
backlog is worse than batch-cap-filtered low-quality output. Re-validate
on a non-free-tier-gated model before ever promoting it. Cerebras / the
other candidates above remain the next things to try if real headroom
(not just a fallback-of-last-resort) is what's needed.

### Groq/Gemini quota-scoping investigation (2026-09-03/04, reporting only, then built 2026-09-04)

Before reaching for a fourth external provider, a reporting-only pass
checked whether the existing Groq/Gemini/Mistral accounts already had
unused headroom via a second MODEL on an account already held, rather
than a new account. Real findings, from live API calls and current docs,
not assumption:

- **Groq: per-model, confirmed live.** A direct hammer-test - call one
  model 8x rapidly, check three OTHER models' rate-limit headers before
  and after - showed the other models' `x-ratelimit-remaining-*`
  counters completely untouched by the hammered model's traffic. Groq's
  own docs page reads ambiguously here ("Rate limits apply at the
  organization level, not individual users" - about the user/key axis,
  not the model axis, but easy to misread as "pooled across models");
  the live header behavior is unambiguous and is what should be trusted.
- **Mistral: per-*bucket*, not strictly per-model.** `ministral-8b-
  latest` (already tested, rejected), `open-mistral-nemo`, `open-
  mistral-7b`, and `mistral-tiny(-latest)` all share ONE draining
  request counter live - a "second model" from that group adds NO
  capacity. `ministral-3b-latest` has its own separate, larger bucket,
  but it's a smaller/weaker model than the already-rejected 8B.
  `open-mixtral-8x7b`/`8x22b` are now pulled from the free tier
  entirely (`req-minute limit: 0`), same as the flagship `mistral-
  small/-medium` models found in the original build.
  `codestral-latest` has its own separate bucket too, but is
  code-specialized, not a general extraction-task fit.
- **Gemini: genuinely unconfirmed.** Gemini's REST responses carry ZERO
  rate-limit/quota headers on either success or error - checked directly
  across 10 live calls, nothing. Docs confirm limits vary "per model"
  in the sense that different model names get different numbers, but do
  not resolve whether two different model names draw from one project-
  level pool per resource type or track independently; secondary
  sources disagree with each other on this. Not guessed either way.
  `gemini-2.5-flash` / `gemini-2.5-flash-lite` are now fully sunset
  ("no longer available to new users" - HTTP 404 with an explicit
  redirect message to `gemini-3.5-flash-lite`/`gemini-3.6-flash`);
  `gemini-3.1-flash-lite` and `gemini-flash-lite-latest` are both still
  real, live, callable models (confirmed after retrying past a transient
  503 "high demand" on each) - a possible second Gemini stage IF the
  scoping question is ever resolved, not acted on here.

**Follow-up (2026-09-04): the Groq per-model finding was acted on -
`openai/gpt-oss-120b` was built as a same-account capacity stage
(`huntloop.skills_matching_groq_120b`) and validated. See the SESSIONS.md
2026-09-04 entry for the full rate-limit reconciliation (both gpt-oss-20b
and gpt-oss-120b confirmed live at 1,000 requests/day / 8,000
tokens/minute, independent buckets) and the 11-job validation result
(clean - 0/11 dumps, fixed the standing Palantir "Deployment Strategist"
failure case every other backend has hit). Wired as `"groq_120b"` in the
router; **promoted into the default rotation the same day, ahead of the
original `"groq"` stage** (`SKILLS_MATCHING_PROVIDERS` default is now
`"groq_120b,groq,gemini"`) - two independent same-account Groq buckets
tried bigger-model-first, Gemini still the fallback behind both. See
CLAUDE.md for the current default-chain status, which can change
independently of this document.**

---

## `is_relevant` relevance gate: title-only blue-collar denylist (redesigned 2026-09-03, see SESSIONS.md)

### Context

The relevance gate (`src/huntloop/relevance_filter.py`,
`job_postings.is_relevant`) decides whether a scraped posting is worth
spending resume-match and skills-analysis effort on. The original design
(2026-08-24) was a hybrid: `is_relevant = (keyword-include OR category
embedding similarity >= 0.29) AND NOT keyword-exclude`, with
`HARD_EXCLUDE_KEYWORDS` (~50 terms) and a `SOFT_EXCLUDE_KEYWORDS` rescue
list layered on top (2026-08-30).

Problems that accumulated:

1. **Whole business functions were structurally blocked.** `sales`,
   `marketing`, `recruiter`, `legal`, `tax`, `accounting`,
   `partnerships`, `procurement`, `communications`, `chief of staff` and
   more were hard-excludes. But an "Account Executive", "HR Business
   Partner", or "Tax Manager" at a visa-sponsoring company is a
   legitimate posting a user might want — the gate is supposed to remove
   *manual* work, not *non-engineering* work.
2. **The embedding half caused real false negatives.** The
   `manufacturing` / `warehouse` / `retail` hard-excludes wrongly killed
   `Sr. Forward Deployed Engineer (FDE) - Retail` and `Senior Solutions
   Architect (EDW Enterprise Data Warehouse Migrations)`.
3. **It needed torch at insert time.** The embedding-similarity input
   meant the pipeline's relevance classification degraded to NULL in any
   torch-less environment.

### Decision

`is_relevant = NOT title_matches_denylist(title)` — a pure, title-only,
word-boundary denylist of manual / blue-collar / front-line hourly work.
No description text (never validated). No embedding, similarity
threshold, or reference text in the decision.

`DENYLIST_KEYWORDS` is 191 terms: a 190-term hand-validated list
(driving/delivery, warehouse/fulfillment, production/assembly-line,
skilled trades, automotive service-bay, janitorial/housekeeping, food
service/kitchen, retail floor, other manual front-line) plus
`hoist operator` (the `forklift operator` phrase missed "Forklift/Hoist
Operator"). Matching: space-containing terms are substring matches; other
terms use `(?<![a-z0-9])term(?![a-z])` — word boundary in front,
digit-tolerant behind ("picker/packer" matches "Picker/Packer2"), letter
still blocks ("mason" ≠ "masonry").

One carve-out: the bare `warehouse` term is skipped when the title
contains "data warehous" (so "Staff Data Warehouse Engineer" and the EDW
architect role pass), while `warehouse operator` / `warehouse selector`
still apply.

Clinical/healthcare roles were never denylisted and remain relevant.

### Consequences

- **Recompute (all 94,060 rows, since the logic itself changed):**
  is_relevant True **35,897 → 90,869**; False **58,163 → 3,191**. 55,314
  rows changed — 171 True→False (all blue-collar mislabeled under the old
  logic), 55,143 False→True (the unblocked business functions + the
  embedding-threshold misses).
- Previously-excluded categories now pass (verified by query): Account
  Executive, HR Business Partner, Tax Manager, Marketing Manager,
  Recruiter, Sales Engineer, Customer Success Manager, Solutions
  Consultant.
- Old false negatives fixed (verified): FDE - Retail (7/7 True), EDW Data
  Warehouse Migrations architect (5/5 True).
- Denylisted work stays out (verified): Store Driver, Warehouse
  Associate, Custodian, Line Cook, Forklift/Hoist Operator.
- Accepted residual imprecision: ~6 "Warehouse Automation Engineer" /
  "Warehouse & Logistics Engineer" titles are still denied by bare
  `warehouse` — MVP noise, same class as the old filter's accepted "GRC
  Program Manager" / "Product Designer".

### What was deliberately NOT touched

- `job_postings.embedding` computation and the query-time `match_score` —
  a completely separate mechanism.
- `huntloop.pipelines` — still imports `REFERENCE_TEXT` /
  `cosine_similarity` (now inert for relevance) and still passes a
  computed similarity to `classify_relevance` (now ignored). In a
  torch-less pipeline run `is_relevant` is still left NULL early; the
  daily scrape runs in Docker with torch, and
  `scripts/recompute_relevance.py` / `backfill_relevance.py` mop up
  NULLs. Decoupling the two in the pipeline is a separate follow-up.
- The API/frontend default sort and filter behaviour — a separate
  follow-up.
- `HARD_EXCLUDE_KEYWORDS` / `SOFT_EXCLUDE_KEYWORDS` / the two thresholds —
  retained as inert constants only because
  `scripts/reclassify_soft_excludes.py` /
  `scripts/calibrate_soft_exclude_threshold.py` import them.
  `scripts/calibrate_relevance_threshold.py` carries a "superseded" note.

---

## `resume_versions.owner_id`: multi-user schema groundwork (added 2026-09-04, NOT wired)

**Status: schema only, inert.** A nullable `owner_id` column exists on
`resume_versions` (migration `df1f114b5aee`). Nothing in the application
reads or writes it — no Pydantic schema, no API request/response model,
no query filter references it. This is not a feature; it's a deliberate,
narrow piece of groundwork for a possible future direction.

### Why now

HuntLoop today is a single-user local tool — one active resume, one
person's job search, no auth, no concept of "whose" data anything is. A
multi-tenant direction (multiple people, each with their own resume
versions and match results) has been discussed but **not committed to** —
there's no product decision to build multi-user support, no timeline, no
users table.

The reasoning for adding the column now anyway: `resume_versions` is
currently a small table (a handful of rows — every real ingest/upload
creates a new version, but nobody deletes old ones, so it only grows
slowly). Adding a nullable column to a small table is a cheap,
zero-risk `ALTER TABLE` today. Doing the same later, after this table
has real production volume (or after other tables/queries have grown
dependent assumptions around "there is exactly one implicit owner"),
would be a more disruptive retrofit. Since the column is nullable with
no default and nothing reads it, adding it now costs nothing and forecloses
nothing — it's pure optionality, not a bet on the multi-user direction
actually happening.

### Why nullable, no default, no FK

- **Nullable, no default**: every existing row (and every row inserted by
  today's single-user code paths) legitimately has no owner — there is no
  "current user" concept anywhere in the app to backfill it from. A
  NOT NULL column or a synthetic default (e.g. `0` or `1` standing in for
  "the one user") would misrepresent that nothing has actually been
  decided about *how* ownership will work, and would need an immediate,
  fake backfill just to satisfy the constraint.
- **No `ForeignKey`**: there is no `users` table yet. Adding a FK now
  would mean either pointing at a table that doesn't exist (impossible)
  or inventing a placeholder `users` table purely to hang a constraint
  off of — scope well beyond "add one nullable column," and a real
  design decision (what does a user record even look like here — email?
  an external auth provider's ID? something else?) that hasn't been made.

### What "wiring this up" would actually require later

Landing this column is intentionally the smallest possible first step.
Turning it into a real feature would need, at minimum:

- A `users` table (or equivalent — an external auth provider's user ID
  referenced directly, with no local `users` table at all, is also a
  legitimate design once that decision is made) for `owner_id` to
  meaningfully reference, plus the FK this migration deliberately
  doesn't add yet.
- An actual authentication mechanism — HuntLoop has none today; every
  request to the FastAPI backend is unauthenticated and implicitly
  "the one local user."
- Every query that currently assumes a single global active resume
  (`ResumeVersion.filter_by(is_active=True).first()`, used by both the
  resumes API and the live match-score query in `huntloop.api.routers.jobs`)
  would need to become scoped by owner — "the active resume" would need
  to mean "the active resume *for this user*," which changes the shape
  of that query everywhere it's called, not just where the column lives.
  It would very likely also mean `job_postings.matched_skills`/
  `missing_skills`/scoring becoming per-user rather than a single global
  precomputed value, since a match result is inherently resume-relative —
  a materially larger change than this migration, not addressed here.
- Probably an equivalent `owner_id` (or a shared `user_id` concept) on
  other tables too (`job_applications` at minimum, since "which jobs I've
  applied to" is exactly the kind of state that shouldn't be shared
  across users) — not added here; this step deliberately touches only
  `resume_versions`, the table named in the actual request.

None of the above is being built now. This entry exists so that if/when
the multi-user decision is actually made, the reasoning for why this one
column already exists — and everything it still doesn't do — is written
down rather than rediscovered.

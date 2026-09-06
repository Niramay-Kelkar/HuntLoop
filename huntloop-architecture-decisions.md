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

---

## Hosted Postgres migration: Supabase vs Neon + GitHub Actions cron (researched 2026-09-05, PLANNING ONLY — nothing migrated, no infra changed)

Research pass to put real current numbers behind the already-made
decision (CLAUDE.md) to move the DB off this laptop's local Postgres and
move scheduling off launchd. This section is the reference for the future
migration prompt. **No database was created, no `DATABASE_URL` changed,
no `docker-compose.yml` / GitHub Actions workflow touched.** All provider
numbers below were read from official pricing/docs pages in September
2026 (sources inline); all HuntLoop numbers were measured directly
against the live local DB and the archived `logs/cron.log`.

### Real numbers this analysis is grounded in

**The database (measured 2026-09-05, local system Postgres :5432 / `jobsight`):**

| Item | Value |
|---|---|
| `pg_database_size` (total) | **1341 MB (~1.31 GiB)** |
| `lca_disclosures` | 1,431,321 rows — 646 MB (383 MB heap + 262 MB indexes) |
| `job_postings` | 98,167 rows — 642 MB (229 MB heap + ~413 MB indexes/TOAST) |
| `job_postings.embedding` populated | 98,167 / 98,167 — every row is a `vector(384)` (~150 MB of raw vector data alone) |
| `job_metadata` / `job_locations` | 32 MB / 11 MB |
| `companies` / `resume_versions` | 743 rows / 3 rows |
| skills-matching backlog (`matched_skills IS NULL AND is_relevant IS TRUE`) | 83,575 rows |
| Server / extension versions | PostgreSQL **18.0**, pgvector **0.8.6**; extensions in use: `plpgsql`, `vector` |
| pgvector ANN index (ivfflat/hnsw) | **none** — every match-score query is a Seq Scan + top-N heapsort, by deliberate decision (see CLAUDE.md) |

The dataset does not fit under 500 MB by any realistic trimming —
`job_postings` alone is 642 MB and ~413 MB of that is indexes + the
embedding column + description TOAST, none of it optional to the product.

**The daily scheduled job (`run_orchestrator_cron.sh`), real wall-clock
from the archived `logs/cron.log`:**

| Date | Total run wall-clock | Stage 2 "processed in" (self-reported) |
|---|---|---|
| 2026-08-26 | **45 min** | 41.1 min |
| 2026-08-27 | **55 min** | 51.5 min |
| 2026-08-28 | **51 min** | 47.5 min |
| 2026-08-24 | 56 min | 55.6 min |
| 2026-08-25 | 4 h 22 min | 39.5 min |
| 2026-08-29 | 4 h 49 min | 51.8 min |
| 2026-08-30 | 4 h 45 min | 44.8 min |
| 2026-08-31 | 6 h 24 min (killed, exit 143) | — |
| 2026-09-01 | 4 h 20 min | 79.8 min |
| 2026-09-03 | **8 h 36 min** | 97.8 min |
| 2026-09-04 | **7 h 30 min** | 238.7 min (1648 of 2304 calls failed) |
| 2026-09-05 | 3 h 30 min | 135.0 min |

"Good" days are ~45–56 min end-to-end. The multi-hour days are driven by
two things, neither of which is DB-bound: (a) stage 1's `docker compose
run --rm --build` rebuilding the image every run (worse recently under
host disk pressure — see the 2026-09-05 disk-full incident in
SESSIONS.md), and (b) stage 2's `TokenPacer` sleeping against Groq/Gemini
**free-tier daily quotas** — most of stage 2's wall-clock is deliberate
rate-limit backoff, not compute. Stage 2 runs until
`AllProvidersExhausted` and stops; the 83,575-row backlog clears over
weeks regardless of where it runs.

### Supabase — current free & paid limits (Sept 2026)

- **Free:** 500 MB database, shared compute / 500 MB RAM, 5 GB egress, 2
  active projects, **project paused after 7 days of inactivity**, no
  backups. pgvector **is** available on Free (pgvector 0.8.0 with a
  current Postgres version). Source:
  [supabase.com/pricing](https://supabase.com/pricing),
  [Supabase pgvector docs](https://supabase.com/docs/guides/database/extensions/pgvector).
- **Pro:** **$25/mo** per organization. 8 GB disk included, then
  **$0.125/GB**; "Micro" compute included in the base price (60 direct /
  200 pooler connections, shared CPU, ~1 GB RAM); 250 GB egress included;
  no inactivity pause; daily backups. **Spend cap is ON by default** on
  Pro — usage beyond plan limits is refused rather than billed unless you
  explicitly turn the cap off. Source:
  [supabase.com/pricing](https://supabase.com/pricing).
- **pgvector:** included at every tier, no add-on. HNSW + IVFFlat both
  supported. `vector` up to 2000 dims (we use 384).
- **Postgres version:** new projects provision PG15 / PG17 —
  **not 18 yet** (our source is 18.0).
- **Networking gotcha:** new Supabase projects are **IPv6-only** on the
  direct connection since Jan 2024; an IPv4 direct address is a **+$4/mo
  add-on**. The Supavisor **pooler is IPv4** and free — so an IPv4-only
  client (GitHub Actions runners are IPv4-only) **must** use the pooler
  connection string, not the direct one. Source:
  [Supabase IPv4 address docs](https://supabase.com/docs/guides/platform/ipv4-address),
  [PgBouncer/IPv4 deprecation changelog](https://supabase.com/changelog/17817-pgbouncer-and-ipv4-deprecation).

**Verdict for HuntLoop:** Free is a non-starter — 1.31 GiB > 500 MB (hard
limit, writes are refused past it) and the 7-day pause would kill a DB
that a daily-only job touches. So Supabase means **Pro at $25/mo flat**.
1.31 GiB fits the 8 GB included disk with zero overage; Micro compute is
fine for a single-user tool + one daily batch.

### Neon — current free & paid limits (Sept 2026)

- **Free:** **0.5 GB storage per project**, **100 CU-hours/month**
  compute, autoscale up to 2 CU (1 CU = 1 vCPU / 4 GB RAM),
  **scale-to-zero after 5 min idle (always on, can't disable on Free)**,
  5 GB egress, 100 projects / 10 branches. Source:
  [neon.com/pricing](https://neon.com/pricing),
  [Neon plans docs](https://neon.com/docs/introduction/plans).
- **Launch:** **no monthly minimum** — pure usage-based since the
  Dec 2025 pricing change. Storage **$0.35/GB-month** (no included
  allowance), compute **$0.106/CU-hour** (no included hours), autoscale
  up to 8 CU, scale-to-zero configurable, 500 GB egress/project
  included. Source:
  [neon.com/pricing](https://neon.com/pricing),
  [Neon "new usage-based pricing" blog](https://neon.com/blog/new-usage-based-pricing).
- **Scale:** usage-based, compute $0.222/CU-hour — only relevant if
  HIPAA/SOC2/higher autoscale is ever needed. Not for this project.
- **pgvector:** available on **every** Neon plan, no add-on; HNSW +
  IVFFlat supported, `vector` up to 2000 dims. Source:
  [Neon pgvector docs](https://neon.com/docs/extensions/pgvector).
- **Postgres version:** Neon supports **PG 14–17** for new projects —
  **not 18** (same version-gap issue as Supabase). Source:
  [Neon migrate-from-Postgres docs](https://neon.com/docs/import/migrate-from-postgres).
- **Connection gotcha:** Neon publishes a **direct** endpoint and a
  **`-pooler`** endpoint (PgBouncer, transaction mode). Long-lived
  SQLAlchemy pools should use the direct endpoint; short-lived / many
  concurrent clients use the pooler. `pg_dump`/`pg_restore` **must** use
  the direct (unpooled) endpoint.

**Verdict for HuntLoop:** Free is disqualified by **storage only** —
1.31 GiB > 0.5 GB. Compute-wise the workload would nearly fit Free's
100 CU-h/month, but that doesn't matter once storage forces the upgrade.
So Neon means **Launch**, which has **no minimum**: storage is
1.31 GiB × $0.35 ≈ **$0.46/month**, plus metered compute. Realistic
compute estimate for a single-user tool that scales to zero between a
once-daily batch: ~2–4 CU-h/day for the batch (autoscaling 1–2 CU for
~1–2 h of actual DB-active time) ≈ 60–120 CU-h/month ≈ **$6–13/month**,
plus a few CU-h for ad-hoc/dev queries. **All-in ≈ $7–15/month**, and it
genuinely drops toward the storage floor in a quiet month.

### Recommendation: **Neon (Launch plan).**

Reasoning, in priority order:

1. **Neither free tier fits** (1.31 GiB vs 0.5 GB on both), so the real
   choice is Supabase **Pro ($25/mo flat)** vs Neon **Launch (~$7–15/mo,
   usage-based, no minimum)**. For a workload that is one developer's
   local tool plus a single daily batch job — idle the vast majority of
   the time — Neon's scale-to-zero + metered model matches the shape;
   Supabase's flat compute charge is paying 24/7 for compute that's used
   maybe an hour a day.
2. **HuntLoop uses none of Supabase's platform.** It's a plain
   SQLAlchemy + Alembic app — no Supabase Auth, Storage, Edge Functions,
   PostgREST, Realtime. The $25 buys a managed Postgres and a dashboard;
   Neon is also a managed Postgres with a dashboard, for less.
3. **pgvector parity** — both include it at every tier, both support the
   index types we don't currently use, both handle `vector(384)` /
   `public.vector`. No differentiation here.
4. **Capacity is not close to a limit on either** — 1.31 GiB is small;
   the concern is cost efficiency, not headroom, and Neon wins that.
5. **Migration cost is identical** — a `pg_dump` / restore either way, so
   a later Neon→Supabase move (if HuntLoop ever grows a always-on hosted
   API with steady traffic, the one scenario where Supabase's flat rate
   becomes the safer bet) is the same operation, not a lock-in.

Secondary tie-breakers for Neon: cheaper/branchable for testing the
migration itself (spin a branch, dry-run the restore, throw it away);
usage-based means a botched heavy backfill costs a dollar of compute, not
a plan upgrade.

The one real reason to pick Supabase instead: if predictable flat billing
matters more than absolute cost, or if a future step wants Supabase Auth
for the multi-user work sketched in the `resume_versions.owner_id` entry
above. Neither applies today.

### GitHub Actions as the scheduler — realistic?

- **Free tier:** 2,000 Actions minutes/month for private repos (public
  repos are unmetered). Source:
  [github.com/pricing](https://github.com/pricing),
  [GitHub Actions billing docs](https://docs.github.com/en/actions/concepts/billing-and-usage).
- **Minimum cron interval:** 5 minutes. **`schedule` runs are delayed
  under load** — "can be delayed during periods of high loads … high
  load times include the start of every hour"; community reports of
  10–30 min and occasionally >1 h delays. A `0 3 * * *`-style job will
  fire, just not punctually — fine for this workload. Source:
  [GitHub "events that trigger workflows" docs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).
- **Auto-disable:** scheduled workflows auto-disable after 60 days of no
  repo activity (documented for public repos; HuntLoop commits far more
  often than that, so moot).
- **HuntLoop is a PRIVATE repo** (confirmed 2026-09-05 — the GitHub API
  returns 404 unauthenticated; the existing `.github/workflows/ci.yml`
  runs push/PR-triggered Actions on it today, so Actions itself is
  enabled). Several third-party 2026 write-ups claim `schedule:` events
  are disabled on private repos on the **Free** personal plan and need
  **Pro ($4/mo)** — GitHub's own docs neither confirm nor deny this.
  **This must be verified directly before relying on GitHub Actions
  cron** (create a trivial `schedule:` workflow on a throwaway private
  repo under this account and see if it fires). If true, GitHub Pro at
  $4/mo also raises the minutes allowance to 3,000/month.

**Timing fit — this is the real problem, independent of the private-repo
question:**

- A GitHub-hosted job has a **6-hour hard limit** per job. Real runs on
  2026-08-31 (6 h 24 m), 2026-09-03 (8 h 36 m) and 2026-09-04 (7 h 30 m)
  **would have been killed.**
- Minutes budget: even "good" days at ~50 min × 30 = 1,500 min/month
  leaves almost no margin under the 2,000 free minutes, and one bad day
  (238 min of stage 2 alone on 2026-09-04) burns an eighth of the
  monthly budget. The current shape does **not** fit 2,000 min/month
  with any safety margin.
- **Root cause is fixable but not by lifting-and-shifting:** most of the
  wall-clock is (a) the per-run Docker `--build` (eliminate by building
  the image once in a separate workflow / using GHCR, or by not
  containerising stage 1 on a GHA runner that has Python), and (b) stage
  2 sleeping on external LLM free-tier daily quotas — which is *paying
  for an idle runner to sleep*. Stage 2 is a poor fit for per-run CI
  minutes no matter what; it wants to be a cheap always-on worker
  (small VM, Fly.io/Railway/a Neon-adjacent worker) or stay on the
  local machine until the LLM-quota situation changes.

**GitHub Actions recommendation:** split the two stages.
- **Stage 1 (scraper)** → GitHub Actions `schedule:` is a fine fit once
  the image build is moved out of the hot path: ~5–15 min/run ≈
  150–450 min/month, well inside 2,000. Do this after the DB is hosted
  (a GHA runner is IPv4-only → it must reach the hosted DB over an
  IPv4-reachable endpoint: Neon's endpoints are dual-stack/IPv4-OK;
  Supabase would force the pooler string here).
- **Stage 2 (skills-matching backfill)** → do **not** put on GHA cron as
  currently structured. Keep it on launchd locally for now, or move it
  to a small persistent worker later. Revisit only if the LLM provider
  mix stops being free-tier-daily-quota-bound.

### Migration checklist (DB move specifically) — real steps

Ordered. Assumes target = Neon Launch (Supabase Pro differs only where
noted).

1. **Pre-flight / inventory.**
   - Confirm source is reachable and note exact versions: PG 18.0,
     pgvector 0.8.6, extensions `plpgsql` + `vector` only (already
     measured above).
   - Confirm no ANN indexes exist on vector columns (already true — the
     `public.vector` DDL from `huntloop.db_models.Vector` plus plain
     b-tree / FK indexes are all that restore needs to recreate).
   - `alembic current` on the source — record the revision; it travels
     inside the dump as the `alembic_version` table, so post-restore
     `alembic current` against the target must return the **same**
     revision with **no** `alembic upgrade` needed.
2. **Create the target.**
   - Neon: create org → project → **choose the highest available PG
     major (17)**, region close to the dev machine / future runner.
     Note that Neon auto-creates a default database; either use it or
     `CREATE DATABASE jobsight`.
   - `CREATE EXTENSION IF NOT EXISTS vector;` on the target
     (`neon_superuser` can do this; pgvector is pre-listed as available).
     Verify `\dx` shows `vector` before restoring.
   - Supabase variant: pgvector is toggled on via the dashboard
     Extensions page (or `create extension vector;` in the SQL editor).
3. **Handle the PG 18 → PG 17 major-version gap.** Neither provider
   offers PG 18 yet. `pg_restore` of a custom-format dump into an older
   major is not officially supported, so use the **plain-SQL** path:
   - `pg_dump` with an **18.x client** (match the source) against the
     source, `--format=plain --no-owner --no-privileges
     --no-tablespaces --quote-all-identifiers`, over the **direct
     (unpooled)** connection.
   - Dump schema and data (single file is fine at this size — ~1.3 GiB
     logical, compresses well; `--format=custom -Z` + `pg_restore
     --no-owner -j4` is the faster alternative *if* a same-major restore
     ever becomes possible).
   - Skim the resulting SQL for any PG18-only syntax before loading —
     this schema is vanilla (tables, FKs, one `JSON` column, one
     `vector(384)` column, b-tree indexes) so there should be nothing,
     but confirm rather than assume.
   - Load with `psql "$NEON_DIRECT_URL" -v ON_ERROR_STOP=1 -f dump.sql`.
   - The `CREATE EXTENSION vector` line in the dump is a harmless no-op
     if step 2 already created it; keep `ON_ERROR_STOP` but expect and
     allow the "extension already exists" notice.
4. **Verify data parity** before any cutover:
   - Row counts table-by-table match the numbers above
     (`lca_disclosures` 1,431,321; `job_postings` 98,167; `companies`
     743; `resume_versions` 3).
   - `SELECT count(*) FROM job_postings WHERE embedding IS NOT NULL` ==
     98,167 (vectors survived the text round-trip).
   - A spot pgvector query works on the target:
     `SELECT id FROM job_postings ORDER BY embedding OPERATOR(public.<=>)
     (SELECT embedding FROM resume_versions WHERE is_active) LIMIT 5;`
     — must not error and should rank sanely.
   - `alembic current` against the target == the revision recorded in
     step 1.
5. **Cut over `DATABASE_URL` (the actual switch — do last).**
   - `.env`: `DATABASE_URL` → the Neon **direct** URL, `postgresql+psycopg2://…`,
     `?sslmode=require` (Neon requires TLS). Keep the local URL commented
     out for rollback.
   - `.env` also documents the Docker-side URL substitution
     (`localhost` → `host.docker.internal`) used by
     `run_orchestrator_cron.sh` and `scripts/backfill_embeddings.py`:
     with a hosted DB that `sed 's/localhost/host.docker.internal/'` step
     becomes **a no-op / wrong** — a hosted hostname needs no rewrite.
     `run_orchestrator_cron.sh` line ~100 (`DB_URL_FOR_DOCKER=…sed…`) and
     the equivalent in the embeddings script must be updated to pass the
     hosted URL straight through.
   - `docker-compose.yml`: the `app` and `api` services currently default
     `DATABASE_URL` to the compose `db` service / `host.docker.internal:5432`.
     Point them at the hosted URL (via `.env` interpolation, not a
     hardcoded value). **Decide the fate of the compose `db` service**
     (the :5433 instance) — see "two-instance situation" below.
   - `alembic.ini` / `tests/conftest.py`: **do not** point tests at the
     hosted DB. `conftest.py`'s throwaway-schema isolation reuses
     whatever `DATABASE_URL` points at — running the suite against the
     hosted DB would create/drop schemas on it and burn compute. Keep a
     separate local Postgres (or a Neon *branch*) for tests, set via a
     test-only env override. This interacts with the
     **`search_path` must never include `public`** rule (CLAUDE.md) —
     unchanged, but now with a remote blast radius if violated.
   - CI (`.github/workflows/ci.yml`) already spins its own
     `pgvector/pgvector:pg18` service container — **leave that as-is**,
     it should not talk to the hosted DB.
6. **Downtime:** effectively zero and not a real concern — HuntLoop has
   no live external users, the API is run locally on demand, and the
   scraper is idempotent (`job_url` unique key, upserts). Do the dump
   at a quiet moment (no scrape / backfill running — check `ps` and the
   `pg_try_advisory_lock` key 1751937901 first, per the standing
   rule), accept that any rows written to local Postgres after the dump
   and before cutover are lost, and re-run the scraper once after
   cutover to backfill the gap. No maintenance window needed.
7. **Post-cutover:** keep the local Postgres data intact (do not drop
   `jobsight` locally) for at least one full successful scheduled run
   against the hosted DB, as rollback.

### Project-specific risks & gotchas (evidence-backed)

- **PG 18 → 17 downgrade on restore** (both providers). Real, handled by
  the plain-SQL dump path in step 3 — but it *is* a manual review step,
  not a clean `pg_restore`. The schema is simple enough that this is low
  risk; flagging it so it isn't discovered mid-migration.
- **The two-instance situation** (CLAUDE.md: local system Postgres on
  :5432 holds the real data; the `docker-compose` `db` service on :5433
  is a separate, smaller instance). After migration the **hosted DB
  becomes the single source of truth** and both local instances become
  vestigial. Decide explicitly: (a) drop the compose `db` service
  entirely and have `app`/`api` only ever talk to the hosted DB, or
  (b) keep `db` on :5433 purely as a local scratch/test DB. Option (a)
  is cleaner but means you can't work fully offline; (b) keeps the
  current "which Postgres am I hitting?" ambiguity alive. This is a
  decision to make in the migration prompt, not silently.
- **SQLAlchemy connection pooling vs scale-to-zero / serverless.** The
  app uses SQLAlchemy's default `QueuePool` (`create_engine` with no
  explicit pool args, per `src/huntloop/db.py`-style setup). Against a
  serverless/pooled hosted DB:
  - Set `pool_pre_ping=True` — Neon (and Supabase pooler) will drop idle
    server-side connections; without pre-ping the first query after an
    idle gap throws a stale-connection error. This is the single most
    likely "it worked locally, breaks hosted" failure.
  - Keep the pool small (`pool_size=5`, `max_overflow=5`) — this is a
    single-user tool; a large idle pool just holds a serverless compute
    awake and costs money (defeats scale-to-zero) or exhausts pooler
    slots (Supabase Micro = 60 direct / 200 pooler).
  - The daily batch scripts (`backfill_skills_matching.py`,
    `backfill_embeddings.py`) open a session for a multi-hour run — fine,
    but `pool_pre_ping` matters there too across the `TokenPacer` sleeps.
  - Neon direct endpoint is fine for the app's long-lived pool; only use
    Neon's `-pooler` string for the GHA scraper job if it opens many
    short connections (it doesn't — one Scrapy process, one pool).
- **`pg_dump` must not run over a pooled connection** (both providers
  document this). Use the direct/unpooled string for the migration
  itself; easy to get wrong because the pooled string is often the one
  the dashboard shows first.
- **TLS required.** Both hosted providers require `sslmode=require` (or
  stricter). The local setup uses no SSL. `psycopg2` honours `sslmode`
  in the URL query string; confirm every consumer builds the URL from
  `DATABASE_URL` verbatim and doesn't strip query params.
- **IPv4 from GitHub Actions.** GHA runners are IPv4-only. Neon
  endpoints resolve on IPv4 — fine. Supabase direct is IPv6-only without
  the +$4/mo add-on, so a Supabase + GHA-scraper combo is **forced onto
  the Supavisor pooler string** (transaction mode — set
  `prepare_threshold=0` / disable prepared statements for `psycopg2`, or
  use session-mode port 5432). One more reason Neon is the lower-friction
  pick here.
- **Test suite blast radius.** `tests/conftest.py` runs real
  CREATE/DROP SCHEMA against `DATABASE_URL`. Once that points at a hosted
  DB, an accidental `pytest` run mutates production and spends compute.
  The migration must introduce a hard test/prod env split (test-only
  `DATABASE_URL` override, or a Neon branch), not rely on remembering.
- **Egress / embedding backfill.** `scripts/backfill_embeddings.py` and
  the relevance/skills backfills read `job_postings` in batches; a full
  re-backfill pulls the whole 642 MB table across the wire. Well within
  both providers' included egress (Supabase 250 GB, Neon 500 GB/project)
  but it's real compute-time on the hosted side — run big backfills
  deliberately, not casually.
- **No connection from the migration to the skills-matching LLM quota
  problem.** Moving the DB does not speed up or unblock the 83,575-row
  skills backlog — that's gated entirely by Groq/Gemini free-tier daily
  quotas (see the routing entries above). Stated here so the migration
  isn't expected to help with it.

---

## Hosted DB, part 2: Oracle Cloud Always Free + a full free-tier survey (researched 2026-09-05, RESEARCH ONLY — nothing created, nothing deployed, no `DATABASE_URL` / `docker-compose.yml` / workflow change)

Extends the Supabase-vs-Neon section above. Two questions the first pass
didn't cover: (1) is Oracle Cloud's Always Free tier a credible *self-hosted*
$0-forever alternative, and (2) does *any* other genuinely-permanent (not
trial-credit) free option — including a different database engine — beat the
already-recommended Neon Launch plan? Every provider number below was read
from official docs / pricing pages or primary news reporting in September
2026 (sources inline); where sources disagree that is called out rather than
resolved by guessing.

### Project numbers this is measured against (re-confirmed 2026-09-05, local system Postgres :5432 / `jobsight`)

Unchanged from the first pass — nothing has grown meaningfully in a day:

| Item | Value |
|---|---|
| `pg_database_size` | **1341 MB (~1.31 GiB)** |
| `job_postings` | **98,167 rows**, **98,167 / 98,167** with a populated `vector(384)` `embedding` |
| `lca_disclosures` | 1,431,321 rows |
| `companies` / `resume_versions` | 743 / 3 rows |
| Server / extensions | PostgreSQL **18.0**, pgvector **0.8.6**; `plpgsql` + `vector` only |
| pgvector ANN index | none (deliberate — every match query is Seq Scan + top-N heapsort) |

The workload shape that matters for this analysis: **one developer's local
tool plus a single ~once-daily batch job.** The DB is genuinely idle 22–23 h
of every day. That single fact drives most of the Oracle verdict below.

---

## Part 1 — Oracle Cloud Infrastructure (OCI) Always Free, real current terms

### 1a. What Oracle's own docs say today (Ampere A1 compute)

From `docs.oracle.com/iaas/Content/FreeTier/` (Always Free Resources /
`resourceref.htm`), read 2026-09-05:

| Resource | Current Always Free allowance |
|---|---|
| **Ampere A1 (Arm) compute** | **1,500 OCPU-hours/month + 9,000 GB-hours/month** → sustained **2 OCPU / 12 GB RAM** (one instance, or split across two) |
| AMD micro compute | 2× `VM.Standard.E2.1.Micro` (1/8 OCPU, 1 GB RAM each) — too small to matter here |
| **Block storage** | **200 GB total** across all boot + block volumes in the home region; **5 volume backups**; min boot volume 47 GB (default 50 GB) |
| **Object Storage** (Always-Free-only accounts) | **20 GB combined** across Standard + Infrequent Access + Archive; **50,000 API requests/month** |
| Outbound data transfer | 10 TB/month |
| Autonomous DB (Oracle DB, not Postgres) | 2 instances × 20 GB — irrelevant, not Postgres/pgvector |

### 1b. The mid-2026 cut — official docs vs. third-party reporting

**This is real and the sources broadly agree on the facts, disagree on the edges:**

- **The cut itself (agreed):** On **June 15, 2026** Oracle halved the Always
  Free Ampere A1 allowance from **4 OCPU / 24 GB** to **2 OCPU / 12 GB**
  (3,000→1,500 OCPU-hours, 18,000→9,000 GB-hours). Reported by InfoQ
  (`infoq.com/news/2026/07/oracle-cloud-free-tier-limits/`), Linuxiac, heise
  online, TerminalBytes. Oracle **published no blog post, sent no
  notification** — the docs were edited silently and users found out when
  instances were shut down or when the numbers on the pricing page changed.
- **Enforcement (agreed):** Oracle emailed Always Free users that instances
  exceeding the new limits would be **terminated on or after August 18,
  2026**. Multiple reports note that **a terminated instance may not be
  recreatable** above the new cap (and OCI's chronic ARM capacity shortages
  in popular regions make *any* A1 launch non-trivial).
- **Does it hit Pay-As-You-Go accounts? (sources disagree, unresolved as of
  today):** Oracle's docs now say "all tenancies get the first 1,500 OCPU
  hours and 9,000 GB hours per month for free" — reads as *everyone*. But
  Oracle **support agents told some users** (June 22) that PAYG accounts keep
  the old 4/24 for free; **other users report support confirming PAYG is also
  capped** (and would be *billed* for overage). There is no official written
  clarification. Treat the PAYG "keeps 4/24 free" claim as **unconfirmed
  folklore**, not a plan you can rely on.
- **Idle-instance reclamation (official, and the decisive point for HuntLoop):**
  `resourceref.htm` states Oracle **may reclaim Always Free VM/bare-metal
  compute instances** that, over any **7-day window**, have **all** of: CPU
  utilisation (95th percentile) < 20%, network utilisation < 20%, and (A1
  only) memory utilisation < 20%. **Paid instances are explicitly exempt.**
  A Postgres box serving one small daily batch will sit far below all three
  thresholds essentially every week — it is close to the *textbook* profile
  this policy targets. The well-known escape hatch is to **upgrade the tenancy
  to PAYG** (stays $0 as long as you remain within the Always Free
  allowances) — but that puts a payment card on file and walks straight into
  the unresolved PAYG-limits ambiguity above.

### 1c. Storage fit

Not a concern. 200 GB block storage, minus a ~50 GB boot volume, leaves
~150 GB for a dedicated data volume. The DB is **1.31 GiB**. Postgres 18 +
pgvector 0.8.6 on Ubuntu/Oracle-Linux ARM64 (both ship in the PGDG apt repo
for `aarch64`; pgvector has no `-march=native`/universal-build problem here —
that was a macOS/EDB-installer quirk, not an ARM one) would fit with ~100×
growth headroom. 12 GB RAM is ample — the entire DB fits in page cache with
room for `shared_buffers`, work_mem, and the daily Scrapy/embedding load.
**On raw capacity, Oracle Always Free is wildly oversized for this project.**
Capacity was never the question.

### 1d. The real operational burden (this is self-hosted — none of it is managed)

Everything Neon/Supabase do invisibly becomes the developer's standing job:

- **OS patching:** `unattended-upgrades` for security patches + periodic
  manual kernel-reboot windows. ~monthly attention, forever.
- **Postgres upgrades:** minor versions via apt; **major versions
  (18→19→…) are a manual `pg_upgrade` or dump/restore** you schedule and
  babysit. Neon/Supabase roll these for you.
- **Backups — there is no managed/automated/PITR backup at all.** A real
  strategy would be: nightly `pg_dump -Fc` via cron → gzip (~300–400 MB
  compressed from 1.31 GiB) → `oci os object put` to Object Storage (20 GB
  free tier easily holds ~2 weeks of dailies) → a retention-prune script →
  **and monitoring that the upload actually succeeded**, plus periodic
  **test restores** (an untested backup is not a backup). OCI's 5 free
  block-volume backups can supplement this but are volume snapshots, not
  logical/PITR. This is the single biggest ongoing chore and the easiest to
  quietly get wrong.
- **Security surface (all hand-managed):**
  - **Double firewall.** OCI security lists / NSGs *and* the stock image's
    own `iptables`/`firewalld` rules both must allow 5432 — the most common
    first-timer trip on OCI.
  - **SSH hardening:** key-only auth, root login disabled, `fail2ban`, NSG
    restricting SSH to the dev's IP.
  - **Postgres hardening:** `scram-sha-256`, TLS (self-signed or Let's
    Encrypt via DNS-01), a tight `pg_hba.conf`, `listen_addresses` limited,
    strong role passwords.
  - No managed "IP allowlist" feature — you edit security-list CIDRs by hand.
- **Monitoring / uptime — no SLA, no status page, no alerting.** You own
  detection of: disk-full (WAL can fill the data volume and wedge the DB),
  connection exhaustion, OOM kills, and the instance simply **disappearing**
  (reclamation, or a region capacity event). Realistically that means
  standing up `node_exporter`/netdata + a dead-man's-switch (e.g.
  healthchecks.io pinged by the daily job). Neon/Supabase include all of
  this at $0 on their *free* tiers, let alone paid.

### 1e. Does an OCI public IP satisfy "GitHub Actions must reach the hosted DB"?

**Basic reachability: yes.** An OCI VM gets a routable public IPv4 (use the
**1 free reserved public IP** so it survives stop/start), and 5432 on it is a
perfectly good endpoint for a GitHub-hosted runner (which is IPv4-only —
fine here; no IPv6/pooler dance like Supabase's direct endpoint would force).

**But the security story has an Oracle-flavoured complication:**
GitHub-hosted runners have **no stable egress IP range** you can practically
allowlist (the published ranges are huge and churn). So locking 5432 down to
"just GitHub Actions" is not feasible with OCI's hand-edited security lists.
The realistic options are (a) expose 5432 to `0.0.0.0/0` and lean entirely on
strong auth + TLS + `fail2ban` (workable, but a world-open Postgres port is
exactly the kind of thing a portfolio reviewer frowns at), or (b) put a
**Tailscale / WireGuard / Cloudflare Tunnel** layer in front and join the GH
Action to it (Tailscale's free tier + `tailscale/github-action` is the usual
answer) — which is *another* moving part to run and monitor. Neon hands you a
TLS connection string that is simply reachable, with the allowlisting
concern handled provider-side.

### 1f. Verdict on Part 1 — **not a credible option for this project. Pay Neon.**

Taking a real position, backed by what's above:

1. **The idle-reclamation policy is aimed squarely at this exact workload.**
   A DB idle 22–23 h/day will trip the <20% CPU/network/memory 7-day test
   almost every week. The only escape is converting to PAYG (card on file) —
   at which point the "free forever" guarantee is already conditional, and
   **Oracle just demonstrated in June 2026 that it will cut terms with zero
   notice and terminate non-compliant instances.** That is the opposite of
   what "low ongoing maintenance" and "don't want to babysit infra" asks for.
2. **The operational surface is precisely the burden this project wants to
   avoid** — self-managed `pg_dump`→Object-Storage backups with restore
   tests, double-firewall config, no monitoring/alerting, manual major-version
   upgrades, OS patching, and standing capacity/reclamation risk — *forever*,
   not once.
3. **GitHub Actions reachability works but needs a VPN/tunnel layer** to be
   defensible, adding another component to run.
4. **Portfolio value is a wash at best, arguably negative.** "I hardened my
   own OCI Postgres with off-box backups" is a fine sentence; "the DB vanished
   because Oracle reclaimed my idle free instance and I lost a day of scrapes"
   is the more probable one. Choosing a managed provider and writing up *why*
   (the section above this one) reads as better engineering judgment than
   running a pet server to save $7.
5. **~$7–15/mo for Neon Launch buys** zero patching, automated PITR backups,
   monitoring, an effective SLA, PG18 already, pgvector, scale-to-zero, and
   branch-based migration testing. For a project whose stated priorities are
   *low maintenance* and *portfolio value*, that is not a close call.

Oracle Always Free would only make sense here if $7/mo were genuinely
unaffordable **and** the sysadmin work were wanted as a hobby in itself.
Neither is true for HuntLoop.

---

## Part 2 — broader survey: any other genuinely-free option, incl. a different engine?

### 2a. Every managed Postgres-compatible free tier, checked Sept 2026

"Permanent" below means an ongoing free tier, **not** a trial or trial credit.
"Fits?" is against the hard number: **1.31 GiB of data.**

| Provider | Free storage | Permanent? | pgvector | Fits 1.31 GiB? | Notes / source |
|---|---|---|---|---|---|
| **Neon** Free | 0.5 GB / project | yes | yes (every plan) | **No** (storage) | PG **18 is now the default** since June 2026 (`neon.com` changelog) — see "version-gap update" below. Forced scale-to-zero after 5 min. |
| **Supabase** Free | 500 MB | yes | yes (0.8.0) | **No** (storage) | 7-day inactivity pause. PG 17 max (no 18 yet). |
| **Aiven** for PostgreSQL Free | **1 GB** disk, 1 GB RAM, 1 vCPU, `max_connections=20` | yes ("no time limitation") | not listed among free extensions — unverified | **No** (1 GB < 1.31 GiB, and no HA) | One free service per type per org; **powered off after prolonged inactivity** (notice given, manual restart). `aiven.io/docs/products/postgresql/concepts/pg-free-tier` |
| **Tiger Data** (ex-Timescale) | sources disagree: PricingSaaS/Koyeb say **30-day trial only**; other write-ups cite a **10 GB** "free tier" | **disputed** | yes (built for vector) | disputed / moot | Flagged as a source conflict; treat as trial-grade until confirmed on their pricing page. Paid starts $29/mo. |
| **CockroachDB Basic** (ex-Serverless) | **10 GiB** + 50M RUs/month | yes | **pgvector-compatible `VECTOR` type + `<=>`/`<->`/`<#>` operators since v25.1, distributed vector index v25.2** | size: **yes** | **Not Postgres** — wire-compatible distributed SQL. Engine switch, not a migration (see 2b). RU-metered: a 98k-row Seq Scan per match query burns RUs. |
| **Render** Free Postgres | 1 GB | **no** — **expires 30 days after creation**, then 14-day grace then deleted | n/a | **No** (size + expiry) | `render.com/changelog` |
| **Railway** | — | **no** free tier | n/a | n/a | $5 trial credit, then $1/mo minimum; removed prepaid credits early 2026. |
| **Fly.io** | — | **no** free tier in 2026 (7-day / 2-VM-hour trial) | n/a | n/a | Postgres is unmanaged Machines anyway. |
| **Koyeb** Postgres | ~1 GB-class, 1 GB RAM / 0.25 vCPU | yes | yes (40+ extensions incl. pgvector) | **No** (size) | Auto-sleep after 5 min. |
| **Prisma Postgres** Free | 500 MB | yes | yes | **No** (size) | |
| **Nile** (`thenile.dev`) | not published in this pass — marketing cites scale-to-zero + pgvector; a real storage number wasn't found on an official page | yes (claimed) | **yes** (real Postgres + pgvector) | unverified | Small serverless-Postgres startup. **Same category as Tembo and Xata** — both killed their free tiers / shut down managed Postgres in 2025–2026. Longevity risk is the concern, not the tech. |
| **Tembo** | — | **gone** — shut down managed Postgres May 2025 | — | — | |
| **Xata** | — | **gone** — "Xata Lite" free tier retired Feb 28 2026; new Xata Postgres is usage-based, no free tier | — | — | |
| **ElephantSQL** | — | **gone** — shut down Jan 27 2025 | — | — | |
| **MongoDB Atlas M0** | **512 MB** | yes | yes (Atlas Vector Search, HNSW, ≤8192 dims) | **No** (size) | Not Postgres — full document-DB rewrite (see 2b). |

**Result of 2a:** *no* genuinely-permanent free tier both **fits 1.31 GiB**
**and** keeps the Postgres + pgvector architecture. The ones that fit on size
are either a different engine (CockroachDB Basic 10 GiB; MongoDB M0 is too
small anyway) or a shutdown-risk startup (Nile — and the Tembo/Xata/
ElephantSQL graveyard makes that risk concrete). Everything that *is*
managed Postgres with pgvector (Neon, Supabase, Aiven, Koyeb, Prisma) caps
free storage at 0.5–1 GB, below this dataset. This is the same conclusion the
first pass reached for Neon/Supabase specifically, now confirmed across the
whole market.

### 2b. Would switching database engines entirely be sensible?

**Short answer: no — it's a large rewrite that still doesn't yield a
free tier that fits, so it is pure cost.**

What HuntLoop actually leans on Postgres/pgvector for:
- `job_postings.embedding` / `resume_versions.embedding` as `vector(384)`
  columns (98,167 populated vectors, ~150 MB raw).
- Query-time cosine similarity via `embedding <=> :resume_vec` (schema-
  qualified `OPERATOR(public.<=>)`), used by `huntloop.match_scoring`
  (`match_score_expr` / `match_score_order_by`), `GET /jobs?sort=-score`,
  the skills-matching selection order, and several ad-hoc scripts.
- `pg_try_advisory_lock` (backfill single-instance lock, key 1,751,937,901).
- Postgres-schema-based test isolation (`tests/conftest.py` CREATE/DROP
  SCHEMA per session).
- SQLAlchemy + Alembic (12+ migrations), `pgvector.sqlalchemy.Vector`
  subclass, `JSON` columns, standard FKs/indexes.

**Path A — free-tier MySQL (e.g. MySQL 9.x `VECTOR`).** MySQL 9 has a
`VECTOR` column type and a `DISTANCE()` function, but:
- Rewrite the vector DDL (`vector(384)` → `VECTOR(384)`), drop the
  `pgvector.sqlalchemy.Vector` subclass and the `OPERATOR(public.<=>)`
  schema-qualification hack entirely.
- Rewrite every similarity query: `ORDER BY embedding <=> :v` →
  `ORDER BY DISTANCE(embedding, :v, 'COSINE')`; re-derive `match_score_expr`
  in the new dialect. Robust similarity indexing is largely a HeatWave
  (paid) feature — but HuntLoop uses no ANN index today, so a seq-scan
  approach ports.
- `pg_try_advisory_lock` → MySQL `GET_LOCK()` / `RELEASE_LOCK()` (different
  semantics — session-scoped, named).
- Test isolation: MySQL has no schema-namespace equivalent (schema == database)
  — `conftest.py`'s whole mechanism is rebuilt.
- SQLAlchemy dialect → `mysql+pymysql`; regenerate the **entire Alembic
  history** for MySQL (types, autoincrement, `JSON`, index syntax all differ);
  `pg_dump` → `mysqldump` for the one-time data move.
- **And after all that**: free managed MySQL tiers (Aiven 1 GB; PlanetScale
  killed its free tier) have the *same* 0.5–1 GB ceilings — **1.31 GiB still
  doesn't fit.** Zero payoff.

**Path B — MongoDB Atlas M0 (document DB + Atlas Vector Search).**
- **M0 is 512 MB. The dataset is 1.31 GiB. It does not fit — full stop**,
  before considering the rewrite.
- The rewrite is total: relational → document model; **SQLAlchemy and Alembic
  are removed entirely** (replace with PyMongo/Beanie); every join re-modeled
  (companies ↔ job_postings ↔ job_locations ↔ job_metadata, and the fuzzy
  `lca_disclosures` matching); pgvector `ORDER BY <=>` → a `$vectorSearch`
  aggregation stage against an Atlas Vector Search index; every FastAPI router
  query, every test, and the migration history all rewritten. This is a
  **rewrite of the whole data layer — weeks of work** — on a part of the
  project that currently works and is essentially done.

**Verdict 2b:** switching engines trades a finished, working data layer for
weeks of rewrite and **still** lands on a free tier too small for the data
(MySQL, Mongo M0) or a non-Postgres engine with RU-metered billing
(CockroachDB). Not sensible.

### 2c. Version-gap update to the first section

The first pass flagged a **PG 18 → 17 downgrade-on-restore** step for both
Neon and Supabase. **For Neon this is now resolved:** Neon made **Postgres 18
the default for new projects in June 2026** (`neon.com` changelog). A
migration to Neon today is a same-major `pg_dump`/restore — the plain-SQL
downgrade dance in step 3 of the migration checklist is **no longer needed
for Neon** (it still applies to Supabase, which was PG17-max as of this
research). One more point in Neon's favour.

### 2d. Final recommendation — **the research reinforces Neon (Launch plan). Nothing found beats it.**

1. **No genuinely-permanent free tier fits 1.31 GiB while staying on
   Postgres + pgvector.** Every managed Postgres free tier caps at 0.5–1 GB.
   The only permanent free tiers large enough are a different engine
   (CockroachDB Basic) or a document DB that's *still too small* (Mongo M0),
   or a startup with a demonstrated peer-group pattern of killing free tiers
   (Nile — cf. Tembo, Xata, ElephantSQL, all gone in 2025–2026).
2. **Oracle Cloud Always Free technically fits but fails on fit-for-purpose:**
   it converts a managed, near-zero-maintenance need into a self-run server
   with hand-rolled backups, double-firewall config, no monitoring, manual
   upgrades, and an idle-reclamation policy that targets this precise
   workload — on a provider that silently halved the tier and terminated
   instances in mid-2026.
3. **Switching database engines is a multi-week data-layer rewrite that
   doesn't even deliver a free tier that fits** — cost with no benefit.
4. **Neon Launch (~$7–15/mo, usage-based, no minimum)** keeps the entire
   Postgres/pgvector/SQLAlchemy/Alembic stack byte-for-byte, is now on PG18
   (removing the only real migration wrinkle for Neon), includes pgvector,
   PITR backups, monitoring, scale-to-zero, and branch-based migration
   testing, and matches the "idle most of the day, one daily batch" shape far
   better than any flat-rate plan. For a one-developer portfolio project that
   explicitly values low maintenance burden and keeping the architecture
   intact, it remains the right call — this survey strengthens that
   conclusion rather than complicating it.

### Sources (all read 2026-09-05)

- Oracle: `docs.oracle.com/iaas/Content/FreeTier/freetier.htm`,
  `.../freetier_topic-Always_Free_Resources.htm`, `.../resourceref.htm`
  (idle-reclamation thresholds).
- Oracle free-tier cut: `infoq.com/news/2026/07/oracle-cloud-free-tier-limits/`,
  `linuxiac.com/oracle-quietly-cuts-free-tier-ampere-a1-resources-in-half/`,
  `heise.de/en/news/Oracle-halves-free-cloud-resources-11334516.html`,
  `terminalbytes.com/oracle-cloud-free-tier-changes-2026/`; PAYG-vs-Always-Free
  ambiguity: Oracle Cloud Customer Connect discussion 964620.
- Neon: `neon.com/pricing`, `neon.com/faqs/free-plan-limits-and-quotas`,
  `neon.com/blog/new-usage-based-pricing`, `neon.com` June 2026 changelog
  (PG18 default).
- Supabase: `supabase.com/pricing`; PG18 status: supabase GitHub discussion 42681.
- Aiven: `aiven.io/docs/products/postgresql/concepts/pg-free-tier`, `aiven.io/free-tier`.
- Tiger Data / Timescale: `pricingsaas.com/companies/timescale`,
  `koyeb.com/blog/top-postgresql-database-free-tiers-in-2026` (source conflict noted).
- CockroachDB: `cockroachlabs.com/blog/vector-search-pgvector-cockroachdb/`,
  `cockroachlabs.com/docs/.../vector`, `cockroachlabs.com/blog/serverless-free/`,
  `cockroachlabs.com/docs/releases/cloud` (Serverless→Basic rename).
- Render: `render.com/changelog/free-postgresql-instances-now-expire-after-30-days-previously-90`.
- Railway / Fly.io: `devtoolpicks.com/blog/railway-vs-render-vs-fly-io-solo-developers-2026`,
  `saaspricepulse.com/blog/flyio-free-tier-2026`.
- Tembo shutdown: Hacker News 44038896; `rywalker.com/research/tembo`.
- Xata free-tier retirement: `xata.io/blog/changes-free-tier`.
- ElephantSQL shutdown: `elephantsql.com/blog/end-of-life-announcement.html`.
- MongoDB Atlas M0: `mongodb.com/docs/atlas/reference/free-shared-limitations/`, `mongodb.com/pricing`.
- Cross-provider free-tier surveys: `koyeb.com/blog/top-postgresql-database-free-tiers-in-2026`,
  `github.com/freebase-cloud/free-postgres-hosting` (used only to cross-check, not as a primary number).

---

## Hosted DB, part 3: Oracle idle-reclamation mechanics + Object Storage for log archiving (researched 2026-09-05, RESEARCH ONLY — no Oracle account or resources created, no infra / `DATABASE_URL` / `docker-compose.yml` / `run_orchestrator_cron.sh` / workflow change)

Two follow-ups to "Hosted DB, part 2" above. Part 1 here goes deeper on the
idle-reclamation policy that part 2 flagged as the decisive risk — exact
mechanics from Oracle's own docs, then the real utilization math for running
HuntLoop's actual daily pipeline (not the DB alone) on the Always Free A1.
Part 2 here is a separate, lower-stakes question: using Oracle's Always Free
Object Storage as an offsite home for the gzipped cron-log archives.

### Part 1 — idle-reclamation mechanics, in detail

#### 1.1 Exact wording (Oracle official docs, `docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm`, read 2026-09-05)

Verbatim, the entire relevant section:

> **Reclamation of Idle Compute Instances**
>
> Idle Always Free compute instances may be reclaimed by Oracle. Oracle will
> deem virtual machine and bare metal compute instances as idle if, during a
> 7-day period, the following are true:
>
> - CPU utilization for the 95th percentile is less than 20%
> - Network utilization is less than 20%
> - Memory utilization is less than 20% *(applies to A1 shapes only)*

**Which metrics / AND vs OR:** Three metrics — CPU, network, and (A1 only)
memory. The phrasing is **"the following are true"** — a conjunction. **All
three must be below threshold for the instance to be deemed idle.** Keeping
**any single metric at or above 20%** for the window is enough to *not* be
flagged. For a non-A1 shape only CPU + network are checked; for the A1 Flex
shape HuntLoop would use, memory is the third gate.

**How the 20% is computed — this is where the official docs are thin:**
- **CPU** is explicitly *"for the 95th percentile"* over the 7-day period —
  i.e. the value that 95% of samples fall below. Concretely: the 95th-pct CPU
  is ≥ 20% only if CPU is ≥ 20% for **more than 5% of the window**. 5% of
  7 days = **8.4 hours/week ≈ 72 minutes/day** (cumulative). So to stay
  non-idle *on CPU alone*, the box needs CPU ≥ 20% for **> ~8.4 cumulative
  hours per week**.
- **Network** and **memory** say only *"is less than 20%"* — **Oracle does
  not state the aggregation** (average? 95th pct? peak? % of time above?).
  Secondary write-ups generally assume "same as CPU, 95th percentile," but
  **that is inference, not a quoted source.** Flagged as a real
  documentation gap.
- **The base of each percentage is also undefined:** CPU % of how many
  cores; network % of what bandwidth ceiling; memory % of total RAM vs. of
  "available" (i.e. whether Linux page cache / Postgres `shared_buffers`
  count as "used"). None of this is in the docs.

**Threshold value — a real source conflict:** the *current*
`resourceref.htm` says **20%** for all three. But multiple older/secondary
sources (Oracle Forums threads, community blog posts, 2023–2024) quote
**10%**. Either the threshold was relaxed 10 → 20 at some point, or the
secondary sources are stale. **Both figures are used below** (10% = the
conservative case).

**Process before reclamation (from community reports — NOT in the
reclamation doc itself, flagged as such):** Oracle emails the account when an
instance has been idle 7 days; if it stays idle, the instance is **stopped
(not deleted)** roughly a week after that email; termination comes later if
it's still idle. So in practice there's a warning + ~1 week to react —
*but* there are also documented reports of genuinely-active instances being
flagged (detection false positives), and of flagged instances being
terminated on the reclamation date. No SLA covers a wrong call.

Sources: `docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm`
(primary); `community.oracle.com/customerconnect/discussion/671904` and
`/680560`; `forums.oracle.com/ords/apexds/post/keep-always-free-instance-running-9889`;
`blog.51sec.org/2023/02/oracle-cloud-cleaning-up-idle-compute.html`
(10% figure + stop-then-terminate process).

#### 1.2 The real utilization math — full daily pipeline on a 2 OCPU / 12 GB A1

The question: if **both** pipeline stages (scraper + skills-matching
backfill) ran on the Always Free A1 instead of only the database, would the
real workload's utilization clear the reclamation formula on its own merits?

**Inputs — MEASURED (from `logs/cron.log` / the existing research table / direct inspection 2026-09-05):**

| Input | Value | Source |
|---|---|---|
| Stage 1 (scraper) wall-clock, good day | **45–56 min** end-to-end | `logs/cron.log` (part 2 table) |
| Stage 1 wall-clock, bad day | **4–8.5 h** | same — driven by per-run `docker compose run --rm --build` (image rebuild) + host disk pressure, **not** DB/compute |
| Stage 2 (skills-matching) "processed in" | **40–240 min** | `logs/cron.log` |
| Stage 2 wall-clock composition | **mostly `TokenPacer` `time.sleep()`** against Groq/Gemini free-tier rate limits — near-zero CPU during the sleeps | `backfill_skills_matching.py` design (part 2 + routing sections) |
| One gzipped cron-log archive | **996 MB compressed** (`logs/archive/cron-20260905T110948.log.gz`) | `ls -la` 2026-09-05 — evidence stage 2 emits huge log volume on failure-heavy days |
| A1 Flex shape | 1 OCPU = 1 Arm core (no SMT); 2 OCPU ≈ 2 vCPU; ~1 Gbps network per OCPU → **~2 Gbps** | Oracle A1 Flex docs |

**Inputs — ESTIMATED (never measured on an A1, or on any Linux host for this workload):**

| Input | Best guess | Why uncertain |
|---|---|---|
| CPU % Scrapy drives during a crawl | low, with bursts | Scrapy is network-I/O-bound (`DOWNLOAD_DELAY` + AutoThrottle); CPU spikes only for parsing + the per-row `_classify_and_embed` MiniLM/torch inference |
| CPU-minutes for a day's embedding + `is_relevant` inference | minutes, not tens of minutes | all-MiniLM-L6-v2 on ARM64 CPU, a few hundred short texts/day; torch can peg a core *while it runs* |
| Resident memory: Postgres + loaded `sentence-transformers` + Scrapy | ~4–6 GB during a run; ~2–3 GB between runs | depends heavily on `shared_buffers` config and whether OCI counts page cache as "used" |
| Whether `--build` even applies on the VM | probably not | on a real Linux host you'd run `main.py` directly / from a prebuilt image (part 2 migration checklist) — which *removes* the single biggest CPU consumer from bad days |

**Metric-by-metric against the formula:**

- **Network — clears "idle" comfortably (solid).** 2 Gbps ceiling; 20% =
  400 Mbps sustained. The daily crawl pulls maybe a few hundred MB of API/
  HTML responses over ~1 h — peak a few Mbps, 7-day average ≈ 0.01–0.1% of
  the ceiling. Even a 10× traffic underestimate doesn't get near 20%.
  **Network will read "idle" essentially always.**

- **CPU (95th percentile) — borderline, probably UNDER 20% on a typical
  day (estimated).** The bar is CPU ≥ 20%-of-2-OCPU (≈ 0.4 of one core) for
  **> ~72 cumulative min/day**.
  - *Good day:* stage 1 ≈ 50 min of mostly-I/O work with short embedding
    bursts; stage 2 mostly sleeping. Plausible CPU-≥-20% time ≈ **10–40
    min/day** → **under the 72-min bar.**
  - *Bad day with `--build` on the VM:* the rebuild alone can peg both cores
    20–60+ min → likely **clears** the bar those days. But bad days are the
    minority, and a sane Linux setup deletes the `--build` step, removing
    exactly the thing that would have helped.
  - *If the real threshold is 10% (older sources):* CPU more plausibly
    clears it; at 20% it likely doesn't on a normal day.
  - **Net: CPU 95th-pct is not a reliable non-idle signal for this
    workload.**

- **Memory (A1 only) — the wildcard, and the one metric that might hold
  ≥ 20% on its own (genuinely uncertain).** 12 GB box; 20% = 2.4 GB.
  - If OCI counts page cache + `shared_buffers` as "used": a warm box
    (1.31 GiB DB hot in cache + Postgres backends + a loaded ML process
    during runs) plausibly sits **> 2.4 GB continuously** → **defeats
    reclamation by itself**, regardless of CPU/network.
  - If OCI counts only non-reclaimable application memory: between runs the
    box might be **~1.5–2.5 GB ≈ 12–21%** — right on the line, possibly
    under 20% for the ~22 h/day nothing is running.
  - Oracle **does not document how memory utilization is measured**, and
    this has not been observed on an A1. This is the best candidate for a
    metric that stays non-idle — but the whole outcome then rests on an
    **undocumented, unmeasured** definition.

#### 1.3 Honest verdict — Part 1

**Running the real daily pipeline on the Always Free A1 does not reliably
clear the reclamation threshold on its own merits, and the outcome is
genuinely uncertain rather than safe.**

- **Network** is nowhere near 20% — it will always read idle.
- **CPU 95th-percentile** is borderline and, on a normally-configured Linux
  host (no per-run image build), **probably sits under 20%** on a typical
  day — the pipeline's wall-clock is dominated by network I/O and deliberate
  rate-limit sleeping, not compute.
- **Memory (A1-only)** *might* stay ≥ 20% purely from a resident Postgres +
  ML-model footprint on a 12 GB box — but Oracle doesn't define how it's
  measured, it's unverified on this shape, and since the rule is AND-logic
  the entire question reduces to "does this one undocumented metric hold?"

Add the documented false-positive reports (active instances flagged anyway),
the no-SLA-if-wrong reality, and the fact that the *only* reliable
mitigation is converting to PAYG (which reintroduces the billing-ambiguity
risk from part 2), and **the reclamation risk stays a real, unresolved
strike against Oracle even when the box is doing genuine daily work.** It is
not something the real workload clearly earns its way out of.

Also worth stating plainly: moving the **pipeline** (not just the DB) onto
the VM is a materially bigger change than "host the database" — it means
rebuilding the entire scheduled-execution setup (currently stage 1 in Docker
on the Mac, stage 2 in `.venv` via launchd) on a Linux host. That's scope
well beyond the migration part 2 was scoping.

**Per the task's own constraint: no artificial-load / keepalive workaround
was explored or is recommended.** If the real workload doesn't clearly clear
the threshold, that is the finding — not a problem to engineer around with
fake CPU/network activity.

### Part 2 — Oracle Always Free Object Storage for cron-log archiving

#### 2.1 Current terms (reconfirmed 2026-09-05, `freetier_topic-Always_Free_Resources.htm`)

- **20 GB total**, combined across Standard + Infrequent Access + Archive
  tiers (for Always-Free-only accounts — a Free Trial with credits gets
  10 GB in *each* of the three tiers instead).
- **50,000 API requests/month** free.
- **10 TB/month outbound transfer** (shared account-wide allowance).
- **No idle-reclamation or inactivity policy on Object Storage.** The 7-day
  idle-reclamation rule is **compute-only** (VM / bare-metal instances). The
  only other inactivity rule anywhere in the Always Free docs is the
  **Autonomous Database 90-day inactivity stop** — also not Object Storage.
  Stored objects persist regardless of how often they're read or written.
- **Account-level caveat only:** an Always Free *tenancy* with no activity at
  all for a long stretch can be flagged for account reclamation — but a
  daily cron `PUT` is activity, so this is minor.
- Source: `docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm`,
  `oracle.com/cloud/storage/object-storage/faq/`.

#### 2.2 Real integration path for the gzipped archives

The existing stopgap accumulates `logs/archive/cron-*.log.gz` locally
(`logs/` is gitignored). Pushing them to Object Storage:

- **S3 Compatibility API — no Oracle SDK needed.** Endpoint:
  `https://<namespace>.compat.objectstorage.<region>.oraclecloud.com`
  (path-style or virtual-host style). Works with **`boto3` / `aws-cli` / any
  standard S3 client** — a `scripts/` uploader could be a ~10-line `boto3`
  `put_object` loop, not an `oci`-SDK integration.
  Source: `docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi.htm`.
- **Auth:** create an IAM user → generate a **Customer Secret Key** for it
  (an `aws_access_key_id` / `aws_secret_access_key` pair) → scope an IAM
  policy to one bucket. Put the pair + namespace + region in the cron
  environment (one more credential to protect/rotate).
- **Documented gotcha:** recent `boto3` / `aws-cli` (≥ ~2.23.5) send
  checksum headers OCI's S3 API rejects — set
  `AWS_REQUEST_CHECKSUM_CALCULATION=when_required` and
  `AWS_RESPONSE_CHECKSUM_VALIDATION=when_required` (or pin an older client).
- **Alternative:** the `oci` CLI (`oci os object put`) with an API signing
  key — heavier setup (config file + PEM key), Oracle-specific, no reason to
  prefer it here.
- **Independence from the DB decision: YES, fully independent — flagged
  explicitly per the task.** Object Storage is a separate product with its
  own free allowance. Archiving logs to OCI Object Storage requires an
  Oracle *account* but touches neither the compute nor the database tiers —
  you could do this with the database on Neon and nothing else on Oracle.

#### 2.3 Honest assessment — Part 2

**Low-risk technically, but not clearly worth doing — and if offsite
archival is wanted, OCI is probably not the right destination.**

- **The real numbers reframe it:** the one existing archive is **996 MB
  gzipped**. At ~1 GB/archive, 20 GB free ≈ **~20 archives** — months, not
  "forever." A ~1 GB *compressed* log is itself a symptom (stage 2 logging
  thousands of retried API calls on failure-heavy days). **Trimming stage-2
  log verbosity is the better first move** than shipping giant logs offsite.
- **Complexity added:** a new Oracle account (if the DB isn't going there),
  a Customer Secret Key in the cron env, the checksum-header gotcha, a
  retention-prune to stay under 20 GB, and monitoring that the upload
  actually worked.
- **Value is modest:** `logs/` is gitignored; the disk-full incident in
  SESSIONS.md (2026-09-05) was Docker build cache, not logs. A local
  "keep the last N gzipped archives, delete the rest" prune is simpler and
  free.
- **If offsite archival is genuinely wanted:** prefer a provider that
  doesn't require standing up an Oracle account solely for this —
  **Backblaze B2 (10 GB free) or Cloudflare R2 (10 GB free)** are both
  S3-compatible, need no compute account, and have no inactivity policy.
  Only pick OCI Object Storage for this if the database *also* ends up on
  Oracle (which part 2 recommends against).

**Verdict:** do the cheap things first — cap local archive retention and
reduce stage-2 log verbosity. Reach for object storage (B2/R2 over OCI)
only if logs need long-term retention for audit/debugging.

### Sources (all read 2026-09-05)

- Oracle idle reclamation (primary): `docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm`.
- Reclamation process / 10%-vs-20% conflict / false-positive reports:
  `community.oracle.com/customerconnect/discussion/671904`,
  `.../discussion/680560`,
  `forums.oracle.com/ords/apexds/post/keep-always-free-instance-running-9889`,
  `blog.51sec.org/2023/02/oracle-cloud-cleaning-up-idle-compute.html`.
- Object Storage terms: `docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm`,
  `oracle.com/cloud/storage/object-storage/faq/`.
- S3 Compatibility API: `docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi.htm`,
  `blogs.oracle.com/cloud-infrastructure/s3-compat-objectstorage-post-virthost`.
- HuntLoop pipeline numbers: `logs/cron.log` archive (part 2 table),
  `logs/archive/cron-20260905T110948.log.gz` size, `scripts/run_orchestrator_cron.sh`.

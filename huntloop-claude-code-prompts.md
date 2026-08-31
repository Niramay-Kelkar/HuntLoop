# HuntLoop — Claude Code prompt log

A running log of the task prompts handed to Claude Code sessions on this
repo, one entry per step, newest last. Each entry: **Goal** (one line),
**Prompt** (the actual instruction given, trimmed of boilerplate), and
**Outcome** (what shipped + where the detail lives — usually a dated
SESSIONS.md entry).

This file is a quick index of *what was asked and delivered*; the full
narrative, evidence, and decisions for every step are in SESSIONS.md.

---

## 2026-08-31 — Resolve the 6 Workday `needs_review` companies

**Goal:** Confirm or exclude the 6 companies flagged `needs_review` by
the Workday ATS-detection pass — no guesses, only commit what's genuinely
verified.

**Prompt:** Two distinct problems. (1) Ownership ambiguity (`red`,
`western`, `tera`) — generic one-word tenant slugs that may belong to a
different company than the LCA sponsor. Fetch real postings, cross-check
the company's name/branding against the DOL employer name; commit only if
ownership is confirmed with real evidence, else leave excluded and say
so. (2) Site segment undiscoverable (`harman`, `daiichisankyo`,
`wholefoods`) — robots.txt blocked/empty, curated site list didn't match.
Try sitemap.xml, a broader site-name list, any other real signal; verify
any found segment by real job retrieval + a live rendered-board
cross-check. Report unresolvable ones explicitly rather than forcing a
match. Verify: per-company outcome, same verification bar as the original
5 for anything new, a real scrape with real row counts and no unexpected
NULLs, full test suite, SESSIONS.md/CLAUDE.md updates. No
`Co-Authored-By: Claude` trailer.

**Outcome:** 1 resolved, verified, and scraped (`harman.wd3`, site
`HARMAN` — CXS total 556 == live rendered board, exact `hiringOrganization`
match, 556 rows, 0 NULL `is_relevant`/`date_posted`/`embedding`). 5 stay
excluded: `red`/`western`/`tera` disconfirmed (tenant belongs to Virgin
Voyages / Western Colorado University / Teranet Inc respectively);
`daiichisankyo` (site `DSI`) and `wholefoods` (site `wholefoods`)
identified but currently unverifiable — both Workday tenants are in a
maintenance outage (CXS 403/502, sitemaps redirect to a Workday
maintenance page). No code changed. Full suite 166 passing. Detail:
SESSIONS.md 2026-08-31 "Resolve the 6 Workday `needs_review` companies".

---

## 2026-08-31 — Stale advisory-lock detection for backfill_skills_matching

**Goal:** Detect (and surface, not auto-fix) the case where a
`backfill_skills_matching.py` run dies abnormally and leaves its Postgres
session-level advisory lock (key `1751937901`) held for hours on an idle
connection — which happened in production and needed a manual `SIGTERM`.

**Prompt:** When `pg_try_advisory_lock` returns false, query
`pg_locks`↔`pg_stat_activity` for the holder's PID/state/`state_change`/
`query_start` and compute how long it's held the lock. Pick a staleness
threshold justified from real logged run durations (recent full-backlog
runs: <1h to ~3.7h) — some multiple of the longest, not an arbitrary
round number. Past the threshold, log a distinct `CRITICAL`/ALERT line
(not the normal "already locked" `WARNING`) with PID, held duration, and
last query/state. Do **not** auto-terminate the backend or clear the lock
from the automated path (standing rule: no unverified automated DB ops) —
instead write a separate human-run `scripts/check_lock_staleness.py` that
inspects the holder and, only after explicit confirmation, calls
`pg_terminate_backend`. Wire the detection (not termination) into the
daily orchestrator path. Don't touch the existing lock acquire/release
logic. Verify with real held-lock runs (paste the actual CRITICAL line
with real PID/duration), real sub-threshold silence, the standalone
script end-to-end including its confirmation gate, the full test suite,
and before/after prod row counts. Update SESSIONS.md/CLAUDE.md/this file.
No `Co-Authored-By: Claude` trailer.

**Outcome:** Added `lock_holder_info()` +
`check_lock_holder_staleness()` to `backfill_skills_matching.py` (called
once from `main()` after the existing WARNING; `_backfill_lock` itself
untouched). Threshold `STALE_LOCK_THRESHOLD_SECONDS = 3 × 13,373s ≈ 11.1h`
(3× the longest real full-backlog run, SESSIONS.md 2026-08-22;
`HUNTLOOP_LOCK_STALE_SECONDS` overrides). New
`scripts/check_lock_staleness.py`: read-only by default (exit 2 if
stale), `--terminate` requires retyping the holder's PID before
`pg_terminate_backend`, never automated. Verified for real: CRITICAL
fires past threshold with live `pg_stat_activity` values, silent below
threshold (only the pre-existing WARNING), standalone script demonstrated
end-to-end incl. wrong-PID abort and correct-PID termination. Full suite
**169 passing** (+3 new lock tests); prod row counts identical
before/after. Detail: SESSIONS.md 2026-08-31 "Stale advisory-lock
detection for backfill_skills_matching".

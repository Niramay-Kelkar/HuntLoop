# CLAUDE.md

Context for Claude Code sessions working in this repo. Keep this short — it's a
reference for fast orientation, not documentation. Full setup/usage detail lives
in README.md.

## Project overview

HuntLoop, today, is a multi-ATS job-board scraper (Scrapy: Greenhouse + Lever
implemented) that pipes postings into a Postgres database via SQLAlchemy, with
an ATS-detection layer (`detect_ats()`) driving which spider runs for which
company. The full loop: `detect_ats()` identifies a company's ATS platform
from its careers URL → `scripts/detect_and_store_ats.py` stores that in
`companies.ats_platform`/`ats_token` → `main.py` (the entrypoint) queries
those rows, groups by platform, and runs `GreenhouseScraper`/`LeverScraper`
once each with the full token list for their platform → each spider
normalizes into `JobPostingItem` → the single, source-agnostic
`JobDataPipeline` upserts companies/sources/postings/locations/skills.
Platforms without an implemented spider (ashby, workday) or companies with no
detected platform are skipped with a clear log message, not silently dropped.

Broader vision: aggregate job postings across many sources and add
sponsorship-aware matching so candidates can filter for companies that
actually sponsor visas (e.g. H1B). **Update: Greenhouse, Lever, Workday,
SmartRecruiters, and Ashby spiders are all built now** (this overview
paragraph predates them — see the spider bullets under "Tech stack and
conventions" and SESSIONS.md for the real current state).

## Tech stack and conventions

- Package root: `huntloop` lives under `src/` (`src/huntloop/...`). Anything
  importing it needs `src/` on `sys.path` (see `main.py`, `pytest.ini`).
- Config: `.env` (gitignored) + `python-dotenv`, loaded in `settings.py`. Never
  hardcode credentials — `.env.example` documents the required shape.
- Migrations: Alembic, config at repo root (`alembic.ini`, `alembic/`).
- Tests: pytest, config at repo root (`pytest.ini`), tests live in `tests/`.
- **FastAPI backend service, `src/huntloop/api/`, added 2026-08-22 — has
  real endpoints now: `GET /health`, `GET /jobs`, `GET /jobs/{id}`,
  `GET /jobs/departments`, `PATCH /jobs/{id}/application`, `GET
  /dashboard/stats`, `GET /resumes`, `POST /resumes/upload`, `PATCH
  /resumes/{id}/activate`, `POST /jobs/{id}/draft-answer`.**
  **>>> SECURITY / DEPLOYMENT WARNING — READ THIS BEFORE DEPLOYING THIS
  API ANYWHERE PUBLIC. `POST /jobs/{id}/draft-answer`
  (`huntloop.api.routers.drafting`, `huntloop.drafting`, added
  2026-09-13 — slice 2 of the job-detail chat assistant, BYOK
  resume-grounded answer drafting) accepts a user-supplied third-party
  LLM API key with every request and forwards it to that provider. That
  key travels over whatever transport this API is served on. **This was
  a real pre-launch blocker when first written (the stack ran `api`/
  `app`/`db` over plain HTTP with no TLS layer anywhere) — it is now
  CLOSED, as of 2026-09-14 (see the `caddy` reverse-proxy entry below
  and SESSIONS.md "Add an optional Caddy reverse proxy for self-hosted
  HTTPS")**: `docker-compose.yml` has an opt-in `caddy` service (behind
  the `proxy` Compose profile) that terminates real Let's Encrypt HTTPS
  for a self-hoster's own domain in front of `api`/`frontend`. **This
  is opt-in, not the default** — plain `docker compose up` (no
  `--profile proxy`) still serves `api`/`frontend` over plain HTTP on
  their own host-mapped ports (now bound to `127.0.0.1` only as of
  2026-09-15, see the port-binding fix below — no longer reachable from
  the LAN/internet even without Caddy). **The rule is unchanged in
  substance, just no longer "there is no way to do this safely": DO NOT
  expose this endpoint (or this API generally) on a public/non-localhost
  deployment without enabling the `proxy` profile (or fronting it with
  some other real TLS terminator) first** — a non-local deploy that
  skips the `proxy` profile is still serving this credential-handling
  endpoint over plain HTTP. **`huntloop/drafting.py`'s and
  `huntloop/api/routers/drafting.py`'s module docstrings still say
  "the stack currently runs plain HTTP with no TLS anywhere" verbatim —
  confirmed stale against the code itself during this documentation
  pass (2026-09-16), not just this file; those two docstrings need the
  same update this entry just got, as a follow-up code change (not made
  here — this pass only touches `.md` files).** <<<**
  **`POST /jobs/{id}/draft-answer` itself (added 2026-09-13, see
  SESSIONS.md "Slice 2: resume-grounded application-answer drafting" —
  builds on the read-only Q&A panel from slice 1, `JobAssistantPanel.tsx`)
  drafts free-form text (e.g. "draft a why this company answer"), NOT
  the structured matched/missing-skills JSON slices 1-8's
  `huntloop.skills_matching*` modules produce — genuinely different
  shape, different module, different key-handling model.** Request:
  `{"prompt": str, "provider": "groq"|"gemini", "api_key": str}` -
  `job_id` is a path param; the frontend sends ONLY these three fields -
  the job description and the active resume's `extracted_text` are
  looked up server-side (`huntloop.api.routers.drafting.
  _lookup_job_and_active_resume`, reusing the exact "fresh
  `is_active=true` query per request" pattern
  `huntloop.api.routers.jobs._active_resume_embedding()` already
  established). **`api_key` is BYOK end to end — never read from
  `GROQ_API_KEY`/`GEMINI_API_KEY`, never persisted, never logged; a
  request with no key is a 400, never a silent fallback to any
  server-side key.** One provider per request, no failover — this
  module never imports `huntloop.skills_matching_router` or any
  `skills_matching_*` module; that router's failover exists to solve
  *this project's own* quota scarcity across its own accounts, an
  unrelated problem to a visitor bringing one key of their own. Errors
  are real and distinct, never a generic 500 or a silent 200: 400 (no
  `api_key`, or no active resume on file), 401 (provider rejected the
  key), 429 (provider-reported rate limit — **not distinguished in the
  response shape from this endpoint's own separate per-IP rate limiter's
  429, which caps this specific unauthenticated endpoint at 10
  requests/60s/client purely to bound server compute/bandwidth exposure,
  not to protect any shared LLM quota since the user's own key means
  this project bears no LLM cost here; the two 429s differ only in their
  `detail` text — a real, minor, flagged gap, not fixed in this slice**),
  502 (provider unreachable/malformed response), 404 (job not found).
  The rate limiter is a bare in-process sliding-window counter (no new
  dependency — `requirements.txt` has no `slowapi`/Redis) — per-process
  memory only, resets on restart, does not share state across multiple
  workers; fine for today's single-process deployment, would need a
  shared store behind multiple workers.
  **`GET /jobs` gained a `department` query param, and `GET
  /jobs/departments` (added 2026-09-02, see SESSIONS.md's "Add a
  department filter to job search") was added alongside it. **As of
  2026-09-07 (see SESSIONS.md "Canonical department categorization")
  both operate on the canonical `department_category` column, NOT the
  raw `department` free text:** `GET /jobs/departments` returns the ~18
  canonical categories present in the data (**alphabetical as of
  2026-09-07, see SESSIONS.md "Alphabetize and group the department and
  location filters"** — was most-common-first), and
  `GET /jobs?department=` matches `JobPosting.department_category`
  exactly, or `UNSPECIFIED_DEPARTMENT = "__unspecified__"` to filter to
  rows with no category (no raw department, or not yet categorized).
  Leaving `department` unset returns postings regardless of category,
  including uncategorized ones — the filter is additive/optional, never
  silently exclusionary, since `department`/`department_category` is
  NULL for a large share of postings (100% of Workday's, by source-data
  design) and those must stay visible by default. The raw `department`
  string is still returned per-posting on `GET /jobs` and `GET
  /jobs/{id}` for transparency.
  `/jobs/departments` is registered before `/jobs/{job_id}` in the
  router file — registering it after would let `{job_id}`'s int-typed
  path param intercept `/jobs/departments` and 422 before this handler
  ever runs.**
  **`GET /jobs` also gained an `employment_type` query param, and `GET
  /jobs/employment-types` (added 2026-09-05, see SESSIONS.md's "Add
  employment_type end-to-end" entry) alongside it — same exact-match/
  `UNSPECIFIED_EMPLOYMENT_TYPE = "__unspecified__"` shape as the
  department filter, and registered before `/jobs/{job_id}` for the
  same routing-order reason.** Unlike `department`, the values behind
  this filter are already normalized into a small fixed set (see the
  `employment_type` column bullet below) - `/jobs/employment-types`
  still queries real distinct values rather than hardcoding that set,
  since not all 5 are guaranteed present in the live data at any given
  moment (all 5 happen to be present as of this entry).
  **`GET /jobs` also gained `location`, `salary_min`/`salary_max`, and
  `salary_unspecified` params, plus `GET /jobs/locations` (added
  2026-09-06, see SESSIONS.md's "Add match-score, salary-estimate, and
  location filters" entry).** **As of 2026-09-07 (see SESSIONS.md
  "Canonical location normalization") `location` matches the CANONICAL
  location grouping, not raw substring:** `GET /jobs?location=` is an
  EXACT match on `job_locations.location_canonical` (EXISTS subquery),
  or `UNSPECIFIED_LOCATION = "__unspecified__"` for postings with no
  `job_locations` row; `GET /jobs/locations` returns the canonical
  labels present on ≥ `_LOCATION_MIN_POSTINGS` (100) distinct postings
  ("San Francisco, CA, United States" covers every "San Francisco" /
  "San Francisco, CA" / "SF Bay Area" variant; "Remote - United States";
  "London, United Kingdom"), with the unresolved long-tail excluded. The
  raw `location_name`(s) are still returned per-posting on `GET /jobs`
  and `GET /jobs/{id}` for transparency.
  **As of 2026-09-07 (see SESSIONS.md "Alphabetize and group the
  department and location filters") `GET /jobs/locations` returns a
  `list[LocationGroup]` — `{country, locations}` grouped by country,
  countries alphabetical with "Other" (labels carrying no resolved
  country, e.g. a bare "Remote") last, `locations` alphabetized within
  each — replacing the old flat most-frequent-first `list[str]`. The
  country grouping is real, not cosmetic: the data spans ~40 countries
  over the 100-posting floor (~119 US groups, ~116 non-US — India, UK,
  Germany, Canada, Poland, China, … — see SESSIONS.md for the full
  distribution). AND `GET /jobs?location=` is now MULTI-VALUE: repeat
  the param (`?location=X&location=Y`) and a posting matches if it is in
  ANY of them (OR within the location filter, still AND'd with the other
  filter types); empty values are ignored; `__unspecified__` still works
  and can be combined with real values.**
  Normalization is `huntloop.location_normalization` — rules + an
  OFFLINE gazetteer (`geonamescache`: ~34k cities pop > 15k, US states,
  ~250 countries), **no network geocoding, no LLM**: place names resolve
  cleanly against a gazetteer where department strings needed an LLM
  pass. It parses "City, Region, Country" (and reversed / ` - ` / ` > `
  / `_` variants, trailing office/campus noise, street/zip stripping)
  into `job_locations.location_{city,region,country,is_remote,canonical}`
  (nullable, migration `b2c3d4e5f6a7` — additive, `location_name`
  untouched). `region` is a 2-letter US-state / CA-province code only
  (other countries' 2-letter admin codes collide with US state
  abbreviations — "Chennai, TN" — so they're left unset). **Joined
  multi-location strings ARE split into multiple `job_locations` rows**
  (~1.3k distinct joined strings): the pipeline / backfill keep the
  first piece's row with its original raw `location_name` and add a
  sibling row per further place, so a "SF; NYC" posting is returned by
  both the "San Francisco" and the "New York City" filter.
  Auto-computed at insert by `JobDataPipeline` (rules + gazetteer only,
  no network on the hot path); `scripts/backfill_location_normalization.py`
  (plain `.venv`, idempotent/resumable) backfilled existing rows.
  **Real coverage: ~98.3% of postings with any location resolve to a
  canonical group; ~4.2% of rows stay unresolved** (internal building
  codes, "Hybrid"/"HQ", some non-ASCII European city names) — those
  keep the cleaned raw string as `location_canonical` and NULL
  city/country, and are filtered out of the `/jobs/locations` dropdown.
  **Location-radius/geocoding/"near me" (lat-long distance) search
  stays out of scope**, same as every prior step — this is text
  canonicalization only. `salary_min`/`salary_max` bound
  a correlated median-`'Year'`-wage scalar subquery
  (`_salary_estimate_expr()`, the same employer-level DOL-filing
  estimate `GET /jobs/{id}` exposes as `salary_estimate.amount` — never
  a real posted salary; NULL for the ~98% of postings whose company has
  no resolved sponsor match); postings with no estimate are excluded
  once a bound is set. `salary_unspecified=true` returns only
  estimate-less postings and overrides the bounds — the
  `__unspecified__`-style option for a range filter. `min_score` (match
  score ≥ X, needs an active resume) already existed and is unchanged.
  Runs as its own `api` service in
  `docker-compose.yml` (own container, port 8000 — deliberately not
  merged into `app`, a separate concern). **`api`'s `DATABASE_URL`
  points at `host.docker.internal:5432` — the real local system
  Postgres — NOT the docker-compose `db` service; `api` has no
  `depends_on: db` at all, it never talks to that service.** `api` also
  has a `./data/resumes:/app/data/resumes` volume mount, added
  2026-08-23 for `POST /resumes/upload` — `data/resumes/` is gitignored
  *and* dockerignored (real personal data), so without this mount an
  upload would only exist in the container's own writable layer and be
  lost on recreation; this makes uploads land in the same real
  `data/resumes/` directory `scripts/ingest_resume.py` already used.
  Building `api` requires real disk headroom in Docker Desktop's build
  VM (torch's CUDA-dependency wheels alone are ~2GB downloaded) — hit a
  real `No space left on device` build failure from accumulated build
  cache/dangling images during that step; `docker builder prune -af` +
  `docker image prune -af` fixed it (see SESSIONS.md). Run
  locally via `PYTHONPATH=src uvicorn huntloop.api.main:app --reload`.
  `huntloop.api.routers.jobs` computes match score at query time via
  pgvector against the active resume (same pattern as every prior ad
  hoc score query) and reads precomputed `matched_skills`/
  `missing_skills` from `job_postings` (Step 5) — doesn't call Groq
  itself. `job_applications` (migration `7d31cf7fed9c`) is one row per
  job (upserted via PATCH, not a history table); a job with no row is
  `not_applied` by default, never backfilled with a dummy row.
  **`huntloop.db_models.Vector` also schema-qualifies its distance
  operators now (`OPERATOR(public.<=>)` etc.), not just its DDL** — a
  bare `<=>`/`<->`/`<#>`/`<+>` doesn't resolve under
  `tests/conftest.py`'s isolated schema even with both operands cast to
  `public.vector`, confirmed directly via `psql` before fixing (see
  SESSIONS.md) — this is required for any future code comparing
  `Vector` columns via SQLAlchemy, not optional. **A SQLAlchemy
  `Enum(SomePythonEnum)` column needs `values_callable=lambda cls:
  [e.value for e in cls]`** if the enum's DB labels are lowercase
  `.value`s (as `ApplicationStatus`'s are) — without it, SQLAlchemy maps
  the Python member *name* instead and every read raises `LookupError`
  (caught by actually hitting the endpoint, not by writing the
  migration). **`CORSMiddleware` is configured** (added 2026-08-22 once
  `frontend/` needed it — a browser's CORS preflight `OPTIONS` 405'd
  with none configured, invisible to every prior server-to-server check
  since CORS is browser-only enforcement) — allows
  `http://localhost:3000`/`127.0.0.1:3000` by default, override via
  `CORS_ALLOWED_ORIGINS`. Don't add auth/further endpoints unprompted —
  see SESSIONS.md for what's still explicitly deferred.
  **`GET /dashboard/stats` (`huntloop.api.routers.dashboard`, added
  2026-08-23, API only — no frontend page consumes it yet) returns
  `total_jobs`/`total_companies`/`applications_by_status`/
  `new_jobs_last_7_days` via a real `DashboardStats` Pydantic schema
  (`huntloop.api.schemas.dashboard`), not a raw dict.**
  `applications_by_status` reuses the exact same
  `COALESCE(job_applications.status, 'not_applied')` pattern
  `huntloop.api.routers.jobs` already uses for a single job's
  `application_status` — grouped from `job_postings` with an outer join
  to `job_applications`, not a bare `GROUP BY status` on
  `job_applications` alone, so a job with no application row still counts
  as `not_applied` and the five counts always sum to `total_jobs`.
  Verified end-to-end against the real running system: every field
  cross-checked exactly against raw `psql` queries (not the app's own
  code path) — see SESSIONS.md for the real numbers
  (`total_jobs=605`, `total_companies=9`, all 605 `not_applied`,
  `new_jobs_last_7_days=605` since every real `job_postings` row was
  scraped within the last 7 days as of this entry).
- **Next.js frontend, `frontend/` (App Router, TypeScript, Tailwind,
  TanStack Query), added 2026-08-22, real job-list UI added 2026-08-23,
  reskinned + extended with a job detail page and an applications tracker
  2026-08-23 (see below and SESSIONS.md).**
  **VISUAL IDENTITY as of 2026-09-07: Direction A, "Register"
  (`visual-identity-register`, see SESSIONS.md "Frontend visual
  identity" + the "HuntLoop Visual Identity" proposal Artifact).** The
  2026-08-23 reskin below took the Claude Design mockup's warm-cream +
  terracotta house style; this replaced it deliberately with a
  public-records / federal-forms register: cool paper (`#f4f5f6`),
  near-black ink, ONE deep form-blue accent (`#1b4965`), hairline rules
  instead of card chrome, square/minimal corners (2-4px radius), NO drop
  shadows. **Structural type is Public Sans** (the US federal typeface —
  the app runs on DOL filing data); **every figure is IBM Plex Mono**,
  tabular. Both are `next/font/google` with the font CSS variable placed
  FIRST in `body`/`--font-sans`/`--font-mono` — the earlier setup led
  its stack with `"Helvetica Neue"` and the declared web font never
  rendered on macOS; don't reintroduce that ordering. Type is a real
  24/20/16/13/11 scale mapped onto Tailwind's size utilities in
  `globals.css` — don't add one-off `text-[Npx]`. Semantic
  score-gradient / sponsor / status colors were cooled one notch to sit
  with the blue (`--color-good #2f7d4f` etc. in `globals.css`;
  `STATUS_META` in `lib/theme.ts`) — they stay red/amber/green, just
  cooler. `globals.css` is a single fixed bright theme (the register is
  paper — no dark mode). The reskin paragraph further down and the
  "Palette extracted pixel-for-pixel from design/HuntLoop.dc.html" note
  are HISTORY now — that mockup is no longer the visual spec.
  `frontend/src/types/api.ts` hand-mirrors the backend's Pydantic
  schemas (no shared codegen — kept manually in sync, a known gap);
  `frontend/src/lib/api.ts` is a real fetch client (`getHealth`/
  `getJobs`/`getJob`/`updateApplicationStatus`/`getDashboardStats`),
  nothing mocked.
  **Routing, as of the 2026-08-23 dashboard step (see SESSIONS.md's
  "Real dashboard page" entry): `/` redirects to `/dashboard` (the
  mockup's home view); the job list itself lives at `/jobs`
  (`frontend/src/app/jobs/page.tsx`), not `/`.** The top nav
  (`frontend/src/components/NavBar.tsx`, a client component split out of
  `layout.tsx` for `usePathname()`-based active-tab highlighting) lists
  Dashboard/Jobs/Applications. Before this step, `layout.tsx`'s "Jobs"
  link and the job detail page's "← Back to jobs" link both pointed at
  `/jobs`, which didn't exist yet (the job list was mounted at `/`) —
  both silently 404'd; fixed as part of adding the dashboard, not a
  separate cleanup.
  **Now containerized, as of 2026-09-13 (see SESSIONS.md "Dockerize the
  frontend so docker compose up brings up the full stack") — supersedes
  the earlier "Deliberately NOT containerized" decision below.**
  `frontend/Dockerfile` (multi-stage: `npm ci`, `next build`, `next
  start` on `node:24-alpine`) plus a `frontend` service in
  `docker-compose.yml` mean a plain `docker compose up` (no profile
  flag) now brings up `db`/`app`/`api`/`frontend` together — a
  stranger's first run needs no local Node/npm install at all.
  `NEXT_PUBLIC_API_URL` is a Docker **build** arg, not a runtime
  `environment:` var (Next.js inlines `NEXT_PUBLIC_`-prefixed vars into
  the browser bundle at build time — the frontend always runs in the
  visitor's own browser regardless of containerization, so this is the
  URL the browser calls directly, never a container-to-container
  address); it defaults to the `api` service's own host-mapped
  `http://localhost:8000`. This is a production-style build (no hot
  reload) — `npm run dev` (Turbopack) remains the faster loop for
  active local development and is not replaced by this; the paragraph
  below describing the original non-containerized-only state is now
  historical context for *why* Docker wasn't added earlier, not a
  description of the current setup. Run either way — containerized (`cd
  <repo root> && docker compose up --build -d api frontend`) or
  locally via `cd frontend && npm install && npm run dev`, needs the API
  already running (`NEXT_PUBLIC_API_URL`, defaults to
  `http://localhost:8000`).
  Note: this dev machine's `.venv` console-script shebangs went stale
  after the `JobSight`→`HuntLoop` rename — run the API via
  `PYTHONPATH=src .venv/bin/python -m uvicorn huntloop.api.main:app`,
  not the `uvicorn` script directly, until the venv is recreated.
  `ScoreIndicator` is a conic-gradient **ring** again (2026-09-07 revert
  of a short-lived horizontal gauge) — the match percent in mono at its
  centre, a tier-colored arc around it. As of composite-match-score-v1
  (2026-09-08) **`score` is the calibrated composite `match_score` in
  `[0, 1]`** (see the match-scoring bullet below) — the ring prints
  `score * 100` and sweeps the same fraction, both straight off it;
  calibration moved server-side, so `lib/theme.ts` no longer rescales
  (`calibratedPercent` is now just `clamp(score) * 100`, `SCORE_CEILING`
  export removed). Don't reintroduce a frontend `/ SCORE_CEILING`.
  `ScoreIndicator` also takes a `provisional` prop (score_basis
  `"partial"`) — an accent-blue `*` on the small ring, a "score
  provisional · skills analysis pending" line under the large one — and
  `ProvisionalScoreNote` is the matching list/table legend.
  `SponsorBadge` is a ruled "stamp" ("H-1B on file" in form-blue /
  dashed "No LCA record"), not a filled pill. Both changes are applied
  at every call site (job cards, table, job detail, kanban).
  `JobSummary`/`JobDetail` also carry `locations: list[str]` (populated
  via the existing `JobPosting.locations` relationship, no migration
  needed).
  **The job detail page ("About the role") renders `job_description` as
  real sanitized HTML as of 2026-09-07 (see SESSIONS.md "Render job
  descriptions as real HTML instead of flattened text") — it previously
  ran `stripHtml()` and flattened everything into one `<p>`, which
  destroyed lists/headings for the raw-HTML sources and showed literal
  `<p>`/`&lt;`/`&nbsp;` text for Greenhouse (whose stored value is
  entity-escaped HTML).** Investigation confirmed ALL 7 sources store
  real semantic HTML (Greenhouse entity-escaped, the rest raw) — this
  was a pure rendering bug, no LLM extraction needed or built.
  `frontend/src/lib/sanitizeHtml.ts`'s `sanitizeJobDescription()`
  unescapes the Greenhouse case then runs **DOMPurify** (`dompurify`,
  added this step — this is third-party HTML, do NOT render it
  unsanitized) with a narrow allow-list: block/inline text tags + `a`
  + tables, NO `style`/`class`/`id` (Lever inlines `font-size` on every
  node — dropped for consistent typography), links forced to
  `target=_blank rel="noopener noreferrer nofollow"`, empty `<p>`/`<div>`
  spacer nodes removed. Returns `""` for nullish input and under SSR
  (the detail view renders this client-side only, via
  `dangerouslySetInnerHTML` inside a `.rich-text` container styled in
  `globals.css` outside `@layer base`). The raw `job_description` column
  is untouched — the fix is render-time only. `stripHtml()` was removed
  from `theme.ts` (now unused).
  **`JobFilters.tsx` gained a department `<select>`, added 2026-09-02
  (see SESSIONS.md) — populated via its own `useQuery` against the new
  `GET /jobs/departments`.** As of 2026-09-07 the options are the
  canonical department CATEGORIES (see the `department_category` bullet
  above), not the ~4,800 raw strings: "All departments" (unset), each
  canonical category actually present in the data, and "Not specified"
  (sends `UNSPECIFIED_DEPARTMENT` from `frontend/src/types/api.ts`,
  filters to postings with no category). No URL query-param sync for any filter, department included —
  matches the existing `company`/`min_score` pattern, not a gap
  introduced here.
  **`JobFilters.tsx` gained a matching `employment_type` `<select>`,
  added 2026-09-05 (see SESSIONS.md's "Add employment_type end-to-end"
  entry) — same shape as the department select: "All employment types"
  (unset), each real distinct value from `GET /jobs/employment-types`,
  and "Not specified" (sends `UNSPECIFIED_EMPLOYMENT_TYPE`, filters to
  NULL-`employment_type` postings). `JobFiltersValue` gained an
  `employmentType` field threaded through `frontend/src/app/jobs/
  page.tsx`'s query key/params the same way `department` already was.**
  **`JobFilters.tsx` gained a `location` filter (canonical location
  groups from `GET /jobs/locations`; see the `location_canonical`
  bullet above — plus "Not specified" → `UNSPECIFIED_LOCATION`), an
  estimated-salary min/max number-input pair with a "No estimate"
  checkbox (→ `salary_unspecified`, which disables the range inputs),
  and a visible caption stating the salary figure is an employer-level
  DOL-filing estimate, not a posted salary — added 2026-09-06 (see
  SESSIONS.md's "Add match-score, salary-estimate, and location
  filters" entry). `JobFiltersValue` gained `location`, `salaryMin`,
  `salaryMax`, `salaryUnspecified`, threaded through `jobs/page.tsx`'s
  query key/params like `department` already was. The match-score
  slider (`minScore` → `min_score`) already existed.**
  **As of 2026-09-07 (see SESSIONS.md "Alphabetize and group the
  department and location filters") the location `<select>` was
  replaced by a real multi-select — `frontend/src/components/
  LocationMultiSelect.tsx`, a no-dependency checkbox dropdown (trigger
  button + positioned panel, type-to-filter box, `<optgroup>`-style
  country section headings from the new `LocationGroup[]` response,
  closed on outside-click / Escape). `JobFiltersValue.location` is now
  `string[]` (`[]` = unset; `UNSPECIFIED_LOCATION` is just another entry
  in the list); `EMPTY_FILTERS`/`jobs/page.tsx` initial state updated;
  `jobs/page.tsx` sends `location: filters.location.length ?
  filters.location : undefined` and `lib/api.ts`'s `getJobs` query
  builder now `.append()`s array params once per value. Each selected
  location renders as its OWN removable chip in the existing chips row
  (not one combined chip), each clearing just itself and never `sort`.
  The department/employment-type/sort selects are unchanged.**
  **`JobFilters.tsx` was reorganized for usability 2026-09-06 (see
  SESSIONS.md's "Improve the job filter panel usability" entry) — a
  UI/UX-only change: no filter's behavior, sentinel values, or the
  `JobFiltersValue` shape / `jobs/page.tsx` query-key/param wiring
  changed at all (verified against the real API — request URLs are
  byte-identical, e.g. `?company=palantir&salary_min=150000&sort=-score&limit=12&offset=0`).
  Before: a flat always-visible two-row panel of ~8 controls with no
  summary of what was active (you scanned every control) and a small
  easy-to-miss "Clear filters ✕" text button as the only affordance;
  the result count lived only in the page `<h1>` subtitle above the
  panel. After: the controls sit in a collapsible body (`useState`
  `expanded`, default open); a persistent header row shows a "Filters"
  toggle with an active-count badge, the live result count for the
  current combination (`resultCount`/`isLoading` props fed from the
  `jobs` query's `total`/`isPending` in `page.tsx`), and a single
  "Clear all ✕" button; and a chips row renders one removable chip per
  active filter (`activeChips(value)` — company / dept / type / location
  / min-match / est-salary-range-as-one-chip / no-estimate), each
  clearing exactly its own field(s) and never `sort`. Selects went
  `w-full sm:w-auto` so they don't overflow at narrow widths; header and
  chip rows `flex-wrap`. `EMPTY_FILTERS` and "clear preserves sort" are
  unchanged.**
- **Frontend test suite: Vitest + React Testing Library, added
  2026-09-04 (see SESSIONS.md's "Frontend test suite (Vitest + RTL) + CI
  wiring" entry) — the frontend had zero test tooling before this.**
  Checked against this specific Next 16 / React 19 App Router setup
  before committing to it (Next's own bundled docs recommend it, RTL's
  peer deps support React 19) rather than assumed. Config:
  `frontend/vitest.config.mts` (native `resolve.tsconfigPaths` for the
  `@/*` alias — no extra plugin needed) + `frontend/vitest.setup.ts`
  (`@testing-library/jest-dom`). Run via `cd frontend && npm test`
  (`vitest run`, single pass — not watch mode). **This is a small
  starting suite (3 files), not full coverage — don't treat it as a
  finished testing effort.** Covers real logic only:
  `frontend/src/lib/api.ts`'s query-string construction + error handling
  + the multipart-upload Content-Type divergence
  (`frontend/src/lib/api.test.ts`); `useApplicationStatusMutation`'s
  optimistic-update/rollback cache behavior
  (`frontend/src/hooks/useApplicationStatus.test.tsx`); and
  `JobFilters`' slider/sentinel/clear-all/active-chip/collapse/result-count logic
  (`frontend/src/components/JobFilters.test.tsx`); and
  `sanitizeJobDescription()`'s entity-unescape, structure preservation,
  XSS stripping, and link hardening
  (`frontend/src/lib/sanitizeHtml.test.ts`, added 2026-09-07). **Not yet covered,
  deliberately**: every page component, `JobCard`/`JobTable`/
  `KanbanBoard`/`ApplicationsList`/`ScoreIndicator`/`SkillChips`/
  `SegmentedToggle`/`NavBar`/`Pagination`/`StatusControl`/`Toast`, and
  any E2E/browser-level testing — pure-presentation components are
  explicitly skipped as low-value rather than padded for a coverage
  number, but several of the untested ones (`ScoreIndicator`,
  `KanbanBoard`'s drag-and-drop, `StatusControl`) do have real logic and
  are reasonable next additions. **Wired into CI as a required check**:
  `.github/workflows/ci.yml`'s `frontend-test` job (separate from the
  existing backend `test` job — Node/npm, no Postgres needed) runs
  `npm ci && npm test` on every push/PR to `master`, same as the backend
  pytest job.
- Entrypoint: `python main.py` runs the multi-ATS orchestrator end-to-end
  — queries `companies.ats_platform`, groups by platform, and runs
  `GreenhouseScraper`/`LeverScraper` once each with all tokens for that
  platform (see the architectural decisions below). `scrapy crawl` is not
  a supported invocation path — there is no `scrapy.cfg` at the repo
  root (deliberate, see `05b833c`'s commit message); always run spiders
  via `main.py` or programmatically (`process.crawl(SpiderClass, ...)`).
- Docker: `Dockerfile` + `docker-compose.yml` (app + postgres:18) for a
  dev-oriented containerized setup. CI (`.github/workflows/ci.yml`) runs
  migrations + pytest against a real Postgres service container
  (`pgvector/pgvector:pg18`) on every push/PR to `master`. **Its `env:`
  block carries `GROQ_API_KEY` / `GEMINI_API_KEY` as the placeholder
  string `dummy-key-for-ci` (2026-09-03, see SESSIONS.md) — the
  skills-matching modules `raise` at import if these are unset and the
  branch's tests are the first to import them; every real API call in the
  suite is mocked, so a non-empty value is enough. The same block sets
  `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` so the
  `sentence-transformers` model fetch degrades deterministically instead
  of depending on the runner's network.** The app container runs as a
  dedicated non-root `huntloop` user (not root) — see the security audit
  entry in SESSIONS.md (2026-08-21). `.dockerignore` excludes `data/raw/`
  and `logs/` (mirroring `.gitignore`) so real LCA data and log output
  never get baked into an image layer.
- **`docker-compose.yml` has a Prometheus/Pushgateway/Grafana
  observability stack, added 2026-08-22 (see SESSIONS.md), gated behind
  `profiles: ["observability"]` so plain `docker-compose up` never starts
  it. This is now closed out — infra, real metrics, and a dashboard all
  exist.** Config lives under `observability/`:
  `observability/prometheus/prometheus.yml` (scrapes only the
  Pushgateway — `main.py` is a run-to-completion batch job, not something
  Prometheus could poll directly);
  `observability/grafana/provisioning/datasources/datasource.yml`
  (pre-provisions the Prometheus datasource with an explicit
  `uid: prometheus`, no manual UI setup); and
  `observability/grafana/provisioning/dashboards/` (a `dashboards.yml`
  file-provider config plus `huntloop-scraping.json`, the "HuntLoop
  Scraping Activity" dashboard — 4 panels: jobs scraped over time by
  company, jobs inserted vs. skipped-as-duplicate by company, scrape
  error count, run duration trend — all pre-provisioned, auto-loaded on
  container start, no manual import). Verified end-to-end against a real
  `python main.py` run: Grafana's own datasource proxy returned the same
  metric values as a direct Prometheus query, matching the run's actual
  DB row-count delta.
  **Real start/access instructions (re-confirmed 2026-09-04):** the stack
  is NOT started by a plain `docker-compose up` — start it explicitly with
  `docker compose --profile observability up -d pushgateway prometheus
  grafana` (the `db`/`app`/`api` services are unaffected, no profile
  flag needed for those). Real host ports as defined in
  `docker-compose.yml`: Pushgateway `9091`, Prometheus `9090`, Grafana
  `3001` (container-side still `3000`, Grafana's own default — only the
  host-side mapping was moved). **Originally mapped `3000:3000`, which
  collided with the Next.js frontend dev server's port — fixed
  2026-09-04 by remapping Grafana to host port `3001` (`"3001:3000"` in
  `docker-compose.yml`) instead of moving the frontend**, so `npm run
  dev` and the observability stack can now run at the same time with no
  conflict; confirmed live post-remap: `lsof -iTCP:3000 -sTCP:LISTEN`
  found nothing (port free for the frontend) while Grafana answered on
  `3001`. Login is `admin` / `admin` (`GF_SECURITY_ADMIN_PASSWORD` defaults to `admin` per
  `docker-compose.yml`; `.env.example`'s commented-out
  `GRAFANA_ADMIN_PASSWORD` overrides it — unset in this repo's real
  `.env`, so the default applies). **Whether the daily launchd cron job's
  metrics are visible depends on Docker being up AT THE TIME the cron job
  fires, not after** — both `push_run_metrics()` (scraping) and
  `push_backfill_metrics()` (skills-matching, added below) push directly
  to the Pushgateway container over `localhost:9091` mid-run; if that
  container isn't running when the push happens, the push fails, is
  caught, and logs one `WARNING` (`Failed to push ... metrics ...`) —
  the scrape/backfill's real DB work is unaffected, but that run's
  metrics are gone for good, not queued or retried. Starting the stack
  later does NOT backfill historical runs' metrics — only runs that
  happen to fire while both `pushgateway` and `prometheus` (which merely
  needs to be up to have scraped it before the next `push_to_gateway`
  overwrites it, since each push replaces the prior value under the same
  grouping key) are already up will show data. Don't add more
  dashboards/panels unprompted unless there's a real new need.
- **`main.py`'s orchestrator pushes real per-run metrics to the
  Pushgateway, added 2026-08-22 (see SESSIONS.md).**
  `src/huntloop/metrics.py` holds a module-level `CollectorRegistry` (not
  `prometheus_client`'s global default) with `Counter`s
  `huntloop_jobs_scraped_total`/`huntloop_jobs_inserted_total`/
  `huntloop_jobs_skipped_duplicate_total`/`huntloop_scrape_errors_total`
  (all labeled `company`/`source`) and a `Gauge`
  `huntloop_run_duration_seconds`. `JobDataPipeline.process_item()`
  increments the per-item counters; `GreenhouseScraper`/`LeverScraper`'s
  `parse()` increment `scrape_errors_total` for malformed/non-JSON
  responses (errors that never reach the pipeline as an item).
  `run_multi_ats_scrape()` wraps the whole run in `try/finally` and the
  `finally` always sets `run_duration_seconds` and calls
  `push_run_metrics()` — one batched `push_to_gateway()` call at the end
  of the run, never per item. **`push_run_metrics()` never raises** — a
  Pushgateway-down failure logs one `WARNING`
  (`huntloop.metrics: Failed to push run metrics...`) and leaves the real
  scrape/DB-insert outcome untouched, same defensive principle as
  `detect_ats()`'s Playwright-failure handling. Verified end-to-end twice
  (see SESSIONS.md): a real run with the stack up, cross-checked
  metric-for-metric against DB row-count deltas; and a real run with the
  stack down, confirming the scrape/insert work completes normally and
  only a warning is logged, not a crash.
- **`scripts/backfill_skills_matching.py` also pushes real per-run
  metrics to the Pushgateway, added 2026-09-04 (see SESSIONS.md) — a
  SEPARATE metrics module/registry/Pushgateway job from the scraping
  metrics above, not an extension of `src/huntloop/metrics.py`.**
  `src/huntloop/skills_matching_metrics.py` holds its own module-level
  `CollectorRegistry` with `Counter`s
  `huntloop_skills_matching_jobs_processed_total` /
  `huntloop_skills_matching_jobs_succeeded_total` /
  `huntloop_skills_matching_jobs_failed_total` (all labeled `provider` —
  `groq_120b`/`groq`/`gemini`/`mistral`/`none`, where `none` means every
  available provider structural-failed that specific batch — see
  `skills_matching_router.match_skills_batch`'s fall-through branch, not
  a real provider) and a `Gauge`
  `huntloop_skills_matching_backlog_remaining` (the real
  `matched_skills IS NULL AND is_relevant IS TRUE` count, measured via
  the existing `_log_backlog()` helper at the end of the run — same
  query the "skills-matching backlog: N relevant rows..." log line
  already used). Pushed to the Pushgateway under job name
  `huntloop_skills_matching_backfill` (deliberately distinct from
  `huntloop_orchestrator`, so a backfill push can never collide with /
  overwrite the scraper's own grouping key) via `push_backfill_metrics()`
  — same never-raises defensive contract as `push_run_metrics()`.
  Provider attribution for each batch is done by diffing
  `state["batch_giveups"]` before/after the router call, not by trusting
  `state["last_provider"]` directly — `match_skills_batch`'s giveup
  branch (every provider structural-failed the batch) returns
  `[None]*n` WITHOUT updating `last_provider`, so a naive read would
  misattribute that failure to whichever provider handled the *previous*
  batch. A second `--limit`-bounded run's or a lock-refused concurrent
  run's early return pushes nothing (same as before this change — no
  run happened, so there's nothing to report). **End-to-end dashboard
  verification (Grafana's datasource proxy vs. a direct Prometheus
  query against a real run) is DEFERRED — see the "HuntLoop
  Skills-Matching Backfill" dashboard bullet below and SESSIONS.md's
  2026-09-04 entry for why.**
- **A second provisioned Grafana dashboard, "HuntLoop Skills-Matching
  Backfill" (`observability/grafana/provisioning/dashboards/
  huntloop-skills-matching.json`, uid `huntloop-skills-matching`, added
  2026-09-04), covers the metrics above** — same file-provisioning
  pattern as `huntloop-scraping.json` (auto-loaded by the same
  `dashboards.yml` provider, no manual import). 4 panels: backlog
  remaining over time, jobs processed per run by provider, a
  succeeded-vs-failed stat panel, and success rate by provider. **NOT
  YET verified end-to-end against a real run** — the daily production
  backfill (started 2026-09-04 06:30 via launchd/cron, still running as
  of this entry, PID 10949) is running the OLD code (predates this
  metrics wiring) and holds the single-instance Postgres advisory lock
  (key 1,751,937,901) for its entire duration, so no second
  `backfill_skills_matching.py` invocation — bounded or not — can run
  concurrently to exercise the new code today. Deliberately not forced
  by killing that process (see SESSIONS.md — it's mid-run against a real
  84,757+-row backlog, actively spending real Groq/Gemini quota; killing
  it to unblock a verification step would waste that spend and lose
  in-flight progress for no real benefit, since tomorrow's scheduled run
  verifies the same code path for free). **Verify this specific item
  once tomorrow's ~3am launchd-triggered run completes**: confirm the
  new counters/gauge appear in a direct Prometheus query
  (`http://localhost:9090`, e.g. `huntloop_skills_matching_backlog_remaining`)
  and that Grafana's dashboard (`http://localhost:3001/d/huntloop-skills-matching`
  — `3001`, not `3000`, since Grafana's host port was remapped 2026-09-04,
  see the port-conflict fix above)
  renders the same values via its datasource proxy — same method the
  original scraping dashboard was verified with. Until then, treat this
  dashboard as "wired but not yet confirmed against real data," not as
  closed out the way the scraping dashboard above is.
- **Root cause found 2026-09-04 for "both dashboards show no data for
  the last 7 days": the observability profile has never once been
  running at 3am when the scheduled cron/launchd run actually pushes
  metrics — confirmed with real evidence, not assumed.** Both stages'
  push functions push over HTTP to `localhost:9091`; if nothing is
  listening there, the push fails, is caught, and logs one `WARNING` —
  by design (see the push-function bullets above), so this failure mode
  is invisible unless someone specifically checks `logs/huntloop.log`.
  **Evidence, not hypothesis:** a direct `curl` against Prometheus's own
  API (`/api/v1/query_range`) over the last 7/14 days returned zero
  `huntloop_*` samples; browsing the `prometheus_data` named volume
  directly showed exactly ONE persisted TSDB block, covering
  2026-08-22 18:15–20:00 UTC (11:15am–1:00pm PDT — the original
  from-scratch manual verification session, not a 3am firing) with
  nothing before or after it until this investigation restarted the
  stack; `docker inspect`'s `Created`/`StartedAt` on the running
  `prometheus`/`pushgateway`/`grafana` containers matched this session's
  own start time, not any historical 3am timestamp. **The named
  Docker volume itself is NOT the problem** — `prometheus_data` (created
  2026-08-22, per `docker volume inspect`) genuinely retained that one
  real block across 13 days of the containers not running at all, proof
  the volume survives `docker compose down`/container recreation fine;
  there was never a data-loss/persistence bug, only an "it was never
  started when it needed to be" gap. **`scripts/run_orchestrator_cron.sh`
  never referenced the `observability` profile at all** before this fix —
  confirmed by reading the whole script, not inferred.
  **Initial fix (2026-09-04, first pass)**: the wrapper started
  `docker compose --profile observability up -d pushgateway` at the top
  of every run, guarded so a failure to start it never aborted the real
  work. This closed "the push fails outright" but left a real residual
  gap: Pushgateway only holds the *latest* value per grouping key (not a
  time series), so that value only became real history if Prometheus
  *also* happened to be running and scraping before the next day's push
  overwrote the same key — otherwise the day's numbers were silently
  skipped, one day at a time.
  **That gap is now closed, same day, second pass — see the
  `restart: unless-stopped` bullet immediately below.** The cron-script
  startup line is kept, but demoted to a defensive fallback (in case
  Docker Desktop was fully quit) rather than the primary mechanism —
  continuity now comes from the restart policy plus a one-time manual
  start, not from the cron script re-starting things every run.
- **`pushgateway`/`prometheus`/`grafana` all carry `restart:
  unless-stopped` in `docker-compose.yml` (added 2026-09-04, second pass
  on the entry above) — real, ongoing background services now, not
  something started fresh around each cron run.** One-time setup:
  `docker compose --profile observability up -d prometheus pushgateway
  grafana` — after that they keep running in the background
  indefinitely (surviving a terminal close, a container crash, or a
  Docker Desktop restart) with nothing to re-run before each scheduled
  cron firing. **Verified for real, not just written in the compose
  file**: `docker inspect`'s `HostConfig.RestartPolicy.Name` reads
  `unless-stopped` on all three actually-running containers after
  recreating them with this config. **Also verified the restart
  mechanism genuinely fires on a real crash** — an isolated throwaway
  `alpine --restart unless-stopped` container whose process exited with
  a failure code was auto-restarted by Docker repeatedly (`RestartCount`
  climbing on its own with no intervention), proving the policy is live
  in this Docker Desktop install, not just configured.
  **One real, non-obvious finding from testing this**: `docker kill`
  (or `docker stop`) on a container does **NOT** trigger `unless-stopped`
  to restart it — confirmed directly (killed the real running
  `pushgateway` container to simulate an unattended crash, and a
  separate isolated test container; neither auto-restarted after 20-30s).
  This is correct, documented Docker behavior, not a bug or a gap in this
  setup: Docker treats any explicit Stop/Kill issued through its own API
  (which is what both `docker kill` and `docker stop` use, regardless of
  signal) as an intentional user action and deliberately will not
  override it — `unless-stopped` means exactly what it says, restart
  unless a human (or a script) explicitly stopped it. It's a real crash
  (the container's own process dying unexpectedly, e.g. an OOM kill or
  an internal bug) that the policy protects against, and that path was
  independently confirmed to work (see the alpine crash-loop test
  above). Practical consequence: a deliberate `docker stop pushgateway`/
  `docker compose --profile observability down` stays down until
  explicitly started again — expected, not something to "fix."
  **What this does NOT cover, stated plainly, not left ambiguous**: if
  the host machine itself reboots and Docker Desktop does not auto-start
  on login, no containers restart either — `restart: unless-stopped`
  only takes effect once the Docker daemon itself is running, it has no
  power over whether the daemon starts in the first place. This is a
  **real, still-open gap** for this specific machine: CLAUDE.md's Docker
  section (`main.py`'s Docker-migration entry, 2026-08-24) already
  documents that "Docker Desktop autostart-at-login" was separately
  enabled on this machine (`AutoStart` in `settings-store.json`) and
  verified surviving a `docker desktop restart`, but a genuine full OS
  reboot was explicitly never tested there either — so if this machine
  is ever fully rebooted (not just Docker Desktop restarted), whether
  Prometheus/Pushgateway come back on their own rests entirely on that
  separate, still-unverified autostart setting, not on anything added in
  this step. Not solved here; flagged honestly rather than implied fixed.
- One-off scripts live in `scripts/` (not part of the ongoing app pipeline
  or CI) — e.g. `scripts/ingest_lca_disclosures.py`, run manually. Uses
  `pandas`/`openpyxl` (in `requirements.txt`) to read DOL's `.xlsx`
  disclosure files from `data/raw/dol_lca/` (gitignored — see SESSIONS.md's
  Step 1 audit for how those files are obtained; they're downloaded
  manually, not fetched by any code in this repo).
- Logging: `src/huntloop/logging_config.py`'s `setup_logging()` is the
  single source of logging configuration (format, level, console + rotating
  `logs/huntloop.log` file handler) — every module gets its logger via
  plain `logging.getLogger(__name__)` and relies on this having already
  run. Level is controlled by the `LOG_LEVEL` env var (default `INFO`), not
  hardcoded per-module. `logs/` is gitignored (runtime artifact).
- **This dev machine has two distinct local Postgres instances — don't
  assume "the Postgres" means the Docker one.** `localhost:5432` is a
  system-installed PostgreSQL 18 (`/Library/PostgreSQL/18`, runs as a
  system service, not Homebrew's `postgresql@18` — that one fails to
  start here with "Address already in use" since the system one already
  holds the port) and is what `.env`'s `DATABASE_URL` points at and what
  manual `python main.py` runs actually use — confirmed 2026-08-22 (see
  SESSIONS.md) holding the real accumulated data (1,431,321
  `lca_disclosures` rows, 616+ `job_postings`, 10 `companies`).
  `localhost:5433` is `docker-compose`'s `db` service — a deliberately
  separate, smaller instance (see the Docker bullet above and README's
  "Run with Docker") that was holding only 115,695/45/1 rows respectively
  when checked. Don't conflate the two or assume either is stale/unused
  without checking row counts directly first.
- **Both Postgres instances now have pgvector 0.8.6 enabled — `docker-
  compose.yml`'s `db` (`pgvector/pgvector:pg18`, switched from
  `postgres:18`) and local system Postgres (5432, where the real data
  lives), as of 2026-08-22 (see SESSIONS.md for both entries). Still
  infra only — no vector columns, embeddings, or matching logic exist
  anywhere yet.** Enabled via Alembic migration `c2d25907fe8e`
  (`CREATE EXTENSION IF NOT EXISTS vector;`), confirmed applied on both
  (`alembic current` → `c2d25907fe8e (head)` on each) and verified via
  `\dx` (`vector 0.8.6`) plus a smoke-test `vector(3)` column/distance
  query on the Docker instance.
  **Local system Postgres needed pgvector compiled from source** against
  `/Library/PostgreSQL/18` (the EDB/PostgreSQL.org installer, not
  Homebrew) — three real obstacles hit and resolved along the way, not
  hypothetical: (1) Xcode Command Line Tools were registered
  (`xcode-select -p` returned a path) but not actually present — only
  caught by trying to compile something, not by `which cc`; fixed via
  `xcode-select --install`. (2) pgvector's default `-march=native` build
  flag isn't supported by Apple `clang` on a universal (`x86_64`+`arm64`)
  build, which is what this Postgres install's `pg_config` targets; fixed
  with `make PG_CONFIG=... OPTFLAGS=""`. (3) `CREATE EXTENSION vector`
  needs a real Postgres superuser (this pgvector version's `.control` has
  no `trusted = true`) — the app's `job_scraper` role correctly can't do
  it; the user ran it once as `postgres` (EDB's default superuser), after
  which `job_scraper`/Alembic can re-run the idempotent `CREATE EXTENSION
  IF NOT EXISTS` as a no-op indefinitely. Don't assume a future fresh
  Postgres install (a new dev machine, a rebuilt volume) has any of this
  done — re-check `\dx` and re-run this same sequence if not.
- **Scheduling is launchd, not cron, as of 2026-08-24 (see SESSIONS.md's
  "Migrate scraping schedule from cron to launchd" entry) — the original
  crontab entry (`0 3 * * *`) was confirmed to never fire reliably on
  this machine: `logs/cron.log` showed only 2 real runs ever, both at
  times that don't match 3am (10:39am and 9:45pm on 2026-08-22), both
  clearly manual/ad hoc, not cron-triggered. Root cause: cron does not
  run missed jobs when the Mac is asleep, and this laptop sleeps
  overnight with no wake schedule — 3am reliably never happened.**
  `~/Library/LaunchAgents/com.huntloop.scraper.plist` (a per-user
  LaunchAgent, gitignored-equivalent — it lives outside the repo, in
  home directory config, same as any other machine-local launchd job)
  runs the *same, unmodified* `scripts/run_orchestrator_cron.sh` wrapper
  (both its scraper + skills-matching stages — skills-matching wasn't
  touched in this step, it's being replaced by a continuous worker in a
  later step) via `StartCalendarInterval` (`Hour=3, Minute=0`), same
  time-of-day the crontab used. **The wrapper's own internals changed
  later (the scraper stage moved from `.venv` to Docker, 2026-08-24 —
  see the "daily scraper itself now runs via Docker" bullet further
  below) — the plist described here is still exactly what's installed,
  unchanged since this step.** **`man launchd.plist` confirms — not
  assumed — that unlike cron, launchd runs a missed
  `StartCalendarInterval` job the next time the machine wakes, coalescing
  multiple missed firings into one**, which is the actual reason this
  migration is expected to be reliable where cron wasn't; this still
  requires the machine to wake at some point in each 24h window (a Mac
  fully asleep for days would still not run it) — `pmset -g sched` shows
  this machine already has other apps' scheduled wake events registered,
  but no HuntLoop-specific `pmset` wake was configured in this step, since
  that requires `sudo` and is a separate, larger-blast-radius change
  (affects battery/other scheduled wakes) than what was asked; revisit
  only if launchd's wake-and-catch-up behavior alone proves insufficient
  in practice. The plist's `StandardOutPath`/`StandardErrorPath` both
  point at `logs/launchd.log` (new) — normally near-empty, since the
  wrapper script already redirects its own stdout/stderr internally into
  `logs/cron.log`; `logs/launchd.log` only catches failures *before* the
  wrapper's own redirection takes effect (exactly how a real failure was
  caught during setup — see below). `logs/cron.log`'s format and
  `logs/huntloop.log` are completely unchanged — only the trigger
  mechanism changed, not what runs or how it logs.
  **`logs/cron.log` is now date-rotated at the start of each run, as of
  2026-09-05 (see SESSIONS.md's "cron.log rotation stopgap" entry) — an
  explicit STOPGAP, not a logging-architecture decision.**
  `run_orchestrator_cron.sh`, before stage 1, gzips any non-empty
  previous `logs/cron.log` into `logs/archive/cron-<timestamp>.log.gz`
  and truncates `cron.log` for the fresh run (it had grown to ~4.85 GB —
  it captures every run's full stdout/stderr including stage 1's
  `--build` output, and nothing trimmed it). `logs/archive/` is
  gitignored. NO mid-run rotation — the script only emits a
  `WARNING` line if the just-rotated log exceeded `CRON_LOG_MAX_BYTES`
  (500 MB), as a "something is spamming the log" signal. `huntloop.log`'s
  separate `RotatingFileHandler` is untouched. Expected to be superseded
  once scraping moves to GitHub Actions (which captures its own workflow
  logs). **`logs/archive/` gained a retention cap 2026-09-06 (see
  SESSIONS.md's "cron.log rotation stopgap" entry, updated that day, and
  the 2026-09-06 log-verbosity investigation): right after the rotation
  block, `run_orchestrator_cron.sh` prunes `cron-*.log.gz` oldest-first
  until BOTH `CRON_LOG_ARCHIVE_KEEP` (14 files ≈ two weeks at the
  one-rotation-per-run cadence) AND `CRON_LOG_ARCHIVE_MAX_BYTES` (3 GiB
  total) hold — the file count is the normal bound, the size cap is the
  backstop if a single run's log ever balloons again. Plain bash-3.2
  while-read loops (no `mapfile`), only touches the `cron-*.log.gz`
  files this script creates.** **Verified end-to-end
  for real, not just "job loaded"**: `launchctl kickstart -p
  gui/<uid>/com.huntloop.scraper` force-fired the job immediately
  (evidence given `StartCalendarInterval` doesn't need to be awaited
  minute-by-minute to prove the job itself runs correctly under launchd —
  only the trigger differs from a real 3am firing, not the executed
  program/environment/logging); real `job_postings` row count went
  605→606 with `scraped_at` updated to the run's real timestamp, and
  stage 2 (skills-matching) processed real batches against the real Groq
  API, all visible in `logs/cron.log` with the exact same log format as
  every prior cron-triggered run. **Hit and fixed one real, non-obvious
  blocker along the way**: the first kickstart failed immediately with
  `Operation not permitted` (`shell-init: error retrieving current
  directory` / bash unable to even read the script) — macOS TCC privacy
  protection blocks processes spawned by launchd from accessing
  `~/Desktop` (and Documents/Downloads) by default, unlike an interactive
  Terminal session which already has that access; this repo happens to
  live under `~/Desktop/HuntLoop`. Fixed by granting Full Disk Access to
  `/bin/bash` (System Settings → Privacy & Security → Full Disk Access) —
  required on this machine specifically because the repo is under
  Desktop; not needed if a repo lived somewhere TCC doesn't gate. The old
  crontab entry (`crontab -l` had exactly this one line, nothing else) was
  removed via `crontab -r` only after the launchd version was confirmed
  working — both were never running simultaneously in production, only
  momentarily during this verification. This is still local-only
  automation; GitHub Actions scheduling against a hosted Postgres
  (Supabase/Neon) remains a deliberately separate, later deployment step —
  don't build it unprompted.
- **A boot/login catch-up job was added 2026-09-06 (see SESSIONS.md's
  "Boot-time catch-up for a missed scheduled run" entry).** The
  `launchd` `StartCalendarInterval` catch-up-on-wake behavior above
  covers a machine that was *asleep* at 3am, but NOT one that was fully
  *off / rebooted* through 3am — confirmed on 2026-09-06, when the
  machine cold-booted at 07:46 and `launchd` did not replay the 03:00
  firing (`com.huntloop.scraper` `runs = 0`, `logs/cron.log` untouched,
  the whole day's run silently skipped). Fix:
  **`~/Library/LaunchAgents/com.huntloop.scraper-catchup.plist`** — a
  SECOND, separate LaunchAgent with `RunAtLoad` and **no**
  `StartCalendarInterval` (so it never competes with the 3am schedule),
  reference copy tracked at
  `scripts/com.huntloop.scraper-catchup.plist`. It runs
  **`scripts/catchup_orchestrator_boot.sh`** shortly after every
  boot/login, which: (1) reads `logs/last_scheduled_run.txt` — a dated
  marker `run_orchestrator_cron.sh` now writes the moment it takes its
  lock — and no-ops if it is dated today (a 3am run, a sleep-wake
  catch-up, or an earlier login catch-up already happened); (2)
  otherwise, if the current local time is in `[00:00, 03:30]`, defers to
  the imminent 3am firing; (3) otherwise triggers one run of the *same*
  `scripts/run_orchestrator_cron.sh`. **The real anti-double-run
  guarantee is a lock, not the time window:** `run_orchestrator_cron.sh`
  now re-execs itself under `lockf(1)` (exclusive `flock(2)` on
  `logs/.orchestrator.lock`, `-t 0` → exit 75 if held → log one line +
  exit 0). Both LaunchAgents land in that script, so a catch-up run and
  a 3am run physically cannot execute the orchestrator concurrently —
  whichever gets the `flock` first runs, the other is a clean no-op.
  `flock(2)` locks are released by the kernel on process exit/kill/
  reboot, so there is never a stale lock file to clear. Stage 2's
  Postgres advisory lock (`pg_try_advisory_lock`, key 1751937901) is
  unchanged and still separate — it guards `backfill_skills_matching.py`
  specifically; the new `lockf` guard is the wrapper-level one that
  covers stage 1 (which previously relied *solely* on launchd's
  "don't start a job that's already running", which only applies to
  repeat firings of one job, not two different jobs invoking one
  script). Both jobs run `/bin/bash`, so the existing Full-Disk-Access
  grant covers the catch-up job too — no new TCC prompt.
  `logs/last_scheduled_run.txt`, `logs/.orchestrator.lock`,
  `logs/catchup.log`, `logs/launchd-catchup.log` are all runtime state
  under the gitignored `logs/`. **Tested** (see SESSIONS.md): decision
  logic (no-op when marker is today, defer inside the window, trigger
  otherwise) via direct runs with test-hook env overrides; the `lockf`
  guard via 4 concurrent invocations of a stubbed copy (exactly one ran,
  three logged the bail line); and both no-op and trigger paths under a
  real `launchctl bootstrap` with `RunAtLoad`. **Not exercised: an
  actual reboot** — the true first-real-reboot catch-up is still to be
  observed in the wild, same standing caveat as the Docker-autostart
  gap. The installed real plist was bootstrapped with a same-day marker
  seeded, so its first `RunAtLoad` was a verified no-op rather than an
  unattended catch-up scrape mid-task; the next boot/login with 3am
  missed is the first genuine trigger.
- **Resume ingestion exists (`resume_versions` table + `scripts/
  ingest_resume.py`), added 2026-08-22.** `data/resumes/` holds the
  actual PDF(s) and is gitignored + dockerignored (personal data, same
  reasoning as `data/raw/`) — never assume a PDF is present there; check
  before building anything that reads from it. Text extraction uses
  `pdfplumber` (not `pypdf`) for its layout-aware, `pdfminer.six`-based
  extraction — verified against a real resume: all sections extract in
  correct reading order with no jumbling, though bullet points come
  through as literal `(cid:127)` rather than `•` (a known pdfminer
  font-encoding limitation — now normalized away by
  `huntloop.text_cleaning.clean_text()` before embedding, see below).
  Each `scripts/ingest_resume.py` run inserts a new `resume_versions` row
  with an auto-incremented `version_number`, flips any previously-active
  row to `is_active=False` (never deletes it), and marks the new row
  active. **`extract_text()` now lives in `huntloop.resume_ingestion`
  (also home to `save_uploaded_pdf()`/`RESUMES_DIR`), added 2026-08-23 —
  `scripts/ingest_resume.py` imports it from there instead of defining
  its own copy, so it can't drift from the real API endpoint below.**
  **A real resume-management API now exists on top of this table
  (`huntloop.api.routers.resumes`, added 2026-08-23, see SESSIONS.md's
  "Resume management API endpoints" entry) — `GET /resumes` (version
  history with a short `text_preview`, not the full text), `POST
  /resumes/upload` (real PDF upload → extract → embed → insert active →
  deactivate the old active row), and `PATCH /resumes/{id}/activate`
  (reactivate an existing version, computing its embedding first if
  somehow missing; a no-op if it's already active).** Both
  activation-changing endpoints reset `matched_skills`/`missing_skills`
  to NULL on every `job_postings` row, so the existing daily cron
  (Step 5.5) naturally reprocesses everything against whichever resume
  is now active — **this reset must bind `sqlalchemy.null()`, not plain
  Python `None`, in the `update(JobPosting).values(...)` call.** Binding
  `None` on this `JSON` column stores the literal JSON scalar `null`,
  not a real SQL `NULL` (`matched_skills IS NULL` is `false`,
  `matched_skills::text` is `'null'`) — found live against real
  Postgres, not caught by the ORM-level `assert row.matched_skills is
  None` (which passes either way, since `json.loads('null') == None`
  too). That silently breaks `scripts/backfill_skills_matching.py`'s own
  `.filter(JobPosting.matched_skills.is_(None))` reprocessing query —
  those rows would never be picked up again. Don't revert this to plain
  `None`; `tests/test_api_resumes.py` asserts the same
  `.filter(...is_(None))` count directly, not just the ORM-level value,
  specifically to catch this class of bug again. **`GET /jobs`'s
  match-score query already resolves "the active resume" dynamically at
  query time** (`ResumeVersion.filter_by(is_active=True).first()`, fresh
  per request, no caching) — re-confirmed by reading that code again
  during this step and by a real test
  (`test_activating_a_different_resume_changes_live_match_scores`) that
  swaps the active version mid-test and asserts live scores change on
  the very next request. `huntloop.embeddings.embed_text()` is imported
  **lazily** inside these two endpoints (not at module level) since it
  needs torch, unavailable in this project's local dev venv (see
  below) — a module-level import would break importing the whole API
  locally, not just these two endpoints, since `huntloop.api.main`
  imports every router together. **The frontend now has a real
  `/resumes` page wired to this API, added 2026-08-24 (see SESSIONS.md's
  "Real resume management page" entry) — frontend-only, no backend
  changes.** `frontend/src/app/resumes/page.tsx`: a dropzone (click or
  drag-and-drop, client-side `.pdf`-only validation) driving
  `uploadResume()`, and a version-history list driving `activateResume()`
  per non-active row — both wired to `lib/api.ts`'s real
  `getResumes`/`uploadResume`/`activateResume`. `uploadResume()`
  deliberately bypasses the shared `apiFetch()` helper (which always
  sets `Content-Type: application/json`) since a `multipart/form-data`
  upload needs the browser to set its own boundary-bearing header. A
  successful upload or activation invalidates `["resumes"]`, `["jobs"]`,
  `["job"]`, and `["dashboard-stats"]` together — a resume swap changes
  every job's live score and resets its skills match, so all of those
  views need to stop showing stale data, not just the resumes list.
  `NavBar.tsx` gained a fourth tab, "Resume", alongside
  Dashboard/Jobs/Applications. The mockup's AI-resume-review column
  (missing keywords/phrasing suggestions/formatting notes) is
  deliberately **not** built — no backend for it exists yet; still a
  separate, not-yet-started phase.
- **Embedding-based match scoring exists, added 2026-08-22 (Step 3, see
  SESSIONS.md) — scoring mechanism only, no 70%-threshold wiring or
  LLM-suggestion logic yet (a Groq-based matched/missing skills-list
  does now exist as a separate piece — see the next bullet).**
  `huntloop.embeddings`
  wraps `sentence-transformers`' `all-MiniLM-L6-v2` (CPU-only, 384-dim -
  confirmed against the model's own published config, matches
  `EMBEDDING_DIM` in `db_models.py`); `huntloop.text_cleaning.clean_text()`
  strips HTML (job descriptions - checked real data: Greenhouse rows
  come back HTML-entity-escaped, Lever rows as raw HTML, one
  `html.unescape()` handles both) and `(cid:N)` pdfminer artifacts
  (resume text) before anything gets embedded. `resume_versions` and
  `job_postings` both have a nullable `embedding vector(384)` column
  (migration `08af7f0a020c`). **`huntloop.db_models.Vector` is a
  required subclass of `pgvector.sqlalchemy.Vector`, not just a style
  choice — it schema-qualifies DDL as `public.vector(n)`. Do not replace
  it with the bare `pgvector.sqlalchemy.Vector` or add another
  `vector`-typed column using anything else — see the conftest.py
  incident bullet above for exactly why.**
  **`job_postings.embedding` is computed at insert time by
  `JobDataPipeline._classify_and_embed` (2026-08-31), in the same single
  model call as `is_relevant` — so a new ATS source no longer needs a
  manual embedding pass after its first scrape.** Same graceful
  degradation as `is_relevant`: NULL when the pipeline runs torch-less
  (local `.venv`) or an isolated per-row failure.
  `scripts/backfill_embeddings.py` embeds the active resume (recomputed
  every run) and backfills `job_postings` in batches of 100 (only
  `embedding IS NULL` rows, safe to interrupt/resume) — still the right
  tool for bulk re-scrapes (batch-of-100 vs. the pipeline's one row at a
  time) and for cleaning up NULLs from torch-less runs, so it stays.
  **This project's
  local dev venv (macOS, Intel, Python 3.13) cannot run
  `sentence-transformers`/`torch` — confirmed by actually trying to
  install `torch` and finding no compatible wheel (PyPI's last
  macOS-x86_64 torch build, 2.2.2, tops out at Python 3.12).** Run
  `backfill_embeddings.py` inside the `app` Docker image instead (Linux,
  real `torch` wheels exist for cp313), pointed at the real local
  Postgres via `docker compose run --rm -e
  DATABASE_URL="...@host.docker.internal:5432/<db>" app python
  scripts/backfill_embeddings.py` — not the docker-compose `db` on 5433.
  Match *scores* are **computed at query time**, deliberately
  **not stored** — a stored score would need an invalidation mechanism
  (on every scrape/resume update) that doesn't exist; revisit only if
  live scoring ever becomes measurably slow. (The per-job *embedding* IS
  stored — it doesn't change unless the description does; only the
  score, which depends on the active resume, is left to query time.)
  **As of composite-match-score-v1 (2026-09-08, see SESSIONS.md + the
  reviewed "Composite Match Score" proposal), `match_score` is NOT raw
  cosine similarity — it is a calibrated blend of two signals** in
  `huntloop.match_scoring`: `EMBEDDING_WEIGHT (0.65) * clamp(sim /
  SCORE_CEILING, 0, 1) + SKILLS_WEIGHT (0.35) * min(ratio /
  SKILLS_CEILING, 1)` where `ratio = matched_skills / (matched_skills +
  missing_skills)`, `SCORE_CEILING = 0.6` (moved out of the frontend —
  calibration is now server-side), `SKILLS_CEILING = 0.66` (~p95 of the
  real ratio distribution). Weights/ceilings are the already-reviewed
  proposal values — don't retune without re-reviewing it. A posting
  gets that full blend ("full" basis) only when `matched_skills` and
  `missing_skills` are both real JSON arrays totalling ≥
  `MIN_SKILLS_DENOM` (3) entries; otherwise ("partial" basis — no active
  skills analysis, ~86% of postings and shrinking only ~1k/day)
  `match_score` is the calibrated embedding term alone. **`matched_skills
  = []` alongside a populated `missing_skills` is a real "matches
  nothing here" signal → full basis, skills term 0, score = `0.65 *
  emb_cal` (a genuine penalty)** — NOT the same as no-data; only `[]`/`[]`
  or a total < 3 drops to the fallback. Partial-basis scores are honest
  best estimates on the same 0-1 scale: they sort and `min_score`-filter
  interleaved with full-basis scores, never bottom-sorted or excluded
  for lacking skills data. The API exposes `score_basis`
  (`"full"`/`"partial"`/`null`) on `GET /jobs` + `GET /jobs/{id}` so the
  frontend can show a neutral accent-blue "score provisional — skills
  analysis pending" marker (a compact `*` on the ring in list/table, a
  spelled-out line under the job-detail ring, plus a list/table legend —
  `ScoreIndicator` `provisional` prop + `ProvisionalScoreNote`).
  `match_score_expr(None)` (no active resume) returns `cast(null() AS
  float)` — a bare untyped NULL literal 500s psycopg's Float processor.
  **Deferred/dropped, deliberately, per the proposal:**
  years-of-experience matching is **v1.1** (pending v1 measurement;
  resume-side YOE is unstructured text, job-side coverage ~40-55% and
  source-skewed — when built it rides the existing skills-matching batch
  call as a bounded penalty multiplier, gated on re-validating skills
  quality per provider); education matching is **dropped** (~40%
  job-side coverage and the resume satisfies nearly every stated
  requirement, so it can't re-rank — display-only if ever surfaced); the
  Groq/Gemini free-tier backlog-spend question is a separate decision,
  untouched.
  The original embedding-only scoring was verified end-to-end against the
  real resume + all 605 real job postings: healthy, non-degenerate score
  distribution (min 0.033, max 0.593, mean 0.372) and a by-eye-sane
  top/bottom-5 ranking (top 5 all Palantir "Software Engineer" roles;
  bottom 5 fraud-ops/creative/marketing roles) — see SESSIONS.md for the
  full numbers.
  **Embedding coverage backfilled to the full dataset 2026-08-30 (see
  SESSIONS.md) — was 605 / 30,373 rows (all the original ~9-company set),
  now 30,373 / 30,373.** Ran `scripts/backfill_embeddings.py` unchanged
  (batch 100, commit-per-batch, `embedding IS NULL` only) in the `app`
  Docker image. The resume-vs-job similarity score is now computable for
  every real job, not ~2%.
  **Full coverage again 2026-08-31 after the Workday scrape (see
  SESSIONS.md): 30,373 → 53,961 / 53,961.** Ran `backfill_embeddings.py`
  unchanged in Docker for the 23,588 new `workday_api` rows (which
  predated the insert-time wiring above). Spot-checked: Workday jobs now
  score and rank in the live UI (`jadeglobal` "Java Backend + AI Agent
  Developer" 0.665, `nxp` "Software DevOps Engineer – for Gen AI" 0.664,
  `jabil` 0.661 — top of "Best match" alongside the ATS-expansion set).
  Going forward the insert-time wiring keeps new rows covered; the
  backfill is only needed for a bulk historical gap like this one.
  **Audited the live match-score query/UI (`huntloop.api.routers.jobs`)
  at the new 30,373-row scale 2026-08-30 (see SESSIONS.md) — no code
  changed, still correct.** Scores now render for ATS-expansion companies
  in both list and detail views (verified: `pubmatic` 0.68, `vianttechnology`
  0.66, `sigmacomputing` ~0.63–0.65 — all now outranking the original
  set; old global max Palantir 0.593 is now rank #42). **Before this
  backfill, jobs with `embedding IS NULL` (98% of the table) were
  silently degraded, not broken**: `match_score` came back JSON `null`
  (frontend "not scored" pill), `nulls_last()` sorted them below every
  real-scored job, and `min_score` filters excluded them from results
  *and* the total count — no crash, no zero, no error. **No pgvector
  index (ivfflat/hnsw) on `job_postings.embedding` and deliberately not
  added**: the list query is a Seq Scan + top-N heapsort, ~66–122 ms SQL
  / ~120–245 ms full `GET /jobs` request — acceptable for a single-user
  local tool; an ANN index is approximate (would change which jobs rank
  where — forbidden by the "don't change scoring" constraint) and only
  helps a bare `ORDER BY <=> LIMIT`, not the `count()`/`min_score`/
  `company`-filtered call patterns. Revisit only past ~100k rows or if
  the API goes multi-user/remote.
- **Matched/missing skills-list via Groq exists (`huntloop.skills_matching`),
  added 2026-08-22. Phase 3's matching engine (embeddings + scoring +
  skills matching) is complete and self-sustaining as of 2026-08-22 —
  not "fully backfilled" (529/605 job_postings rows are still NULL as of
  this entry and that number only shrinks gradually), but no manual
  step is needed for it to keep shrinking or to keep up with newly-
  scraped jobs. See SESSIONS.md for full detail on both entries below.**
  `GROQ_API_KEY` required in `.env`, fails fast at import time of this
  module only (not `settings.py`). **Model is `openai/gpt-oss-20b`, not
  a Llama variant** — checked live against `/v1/models` before picking
  anything; no Llama 3.x chat models are active on Groq's free tier.
  Re-check `/v1/models` before assuming any model name still exists —
  the free-tier lineup already changed once during this project.
  `job_postings.matched_skills`/`missing_skills` (JSON, nullable,
  migration `0fdafe5d162e`) are precomputed and stored, not recomputed
  live. `match_skills()` (single job/call, Step 4) is untouched;
  `match_skills_batch()` (resume once + 5 job descriptions per call) is
  the real production path — batching is genuinely ~2.3x more
  token-efficient with no quality cost, measured before adopting it.
  **Three separate real Groq limits exist, all found only by actually
  running things at increasing scale, not by reading docs — don't
  assume any is the only one:** (1) 8000 TPM rolling per-minute cap;
  (2) an ~8000-token **hard cap on a single request**, independent of
  the rolling window (why batches are capped at size 5, not just
  token-estimated); (3) a **200,000 tokens-per-day (TPD) cap** —
  `match_skills_batch()` raises `DailyQuotaExhausted` specifically for
  this (every other failure still returns `None`), and
  `backfill_skills_matching.py` stops the whole run cleanly on that
  signal (confirmed working live: a real cron trigger hit a genuine TPD
  429 and stopped itself after processing 24 jobs, exit code 0 — not a
  crash, not a hang). **Skills-matching is now a stage of the daily
  orchestrator (`scripts/run_orchestrator_cron.sh`, same schedule as
  Step 5/7 — no separate schedule; that schedule is now launchd-driven,
  not cron-driven, see the Scheduling bullet above)** — stage 1 is the
  existing scraper (`main.py`), stage 2 processes `matched_skills IS
  NULL AND is_relevant IS TRUE` rows under the real daily budget, then
  stops itself when exhausted; both stages always run regardless of the
  other's outcome. **Selection order is resume `match_score` DESC as of
  2026-09-03 (see SESSIONS.md), not the original
  oldest-`scraped_at`-first** — postings that rank well against the
  active resume get skills-gap analysis before generic backlog. The
  ordering and the score expression are the shared
  `huntloop.match_scoring` (`match_score_expr` / `match_score_order_by`),
  the exact same definition `GET /jobs?sort=-score` uses (NULLS LAST,
  with a stable deterministic `id ASC` fallback when the active resume
  has no embedding); `backfill_skills_matching.py` and
  `huntloop.api.routers.jobs` both call that module so they can't drift.
  **`match_score_expr` returns the composite score as of
  composite-match-score-v1 (2026-09-08) — see the match-scoring bullet
  above. For the backfill this is a no-op change: it selects
  `matched_skills IS NULL` rows, which are all partial-basis, whose
  composite == the calibrated embedding term, monotonic in raw
  similarity — same relative order as before.**
  Pacing/`TokenPacer`, provider routing, batch building, and the
  advisory lock were untouched by that change. This is the only
  mechanism now — it both works down
  the backlog over time and keeps every future day's newly-scraped
  postings matched, with nothing to manually re-trigger ever again.
  **Sanity filter**: `MAX_PLAUSIBLE_MATCHED_SKILLS = 20` in
  `huntloop.skills_matching`, enforced inside `match_skills_batch()` —
  rejects (logs + leaves NULL for reprocessing) any job whose
  `matched_skills` exceeds 20 items, since a real failure mode surfaced
  where the model dumps the entire resume's skills section verbatim
  instead of genuinely matching (seen at 26, 33, 33, and 53 items across
  4 real occurrences so far, most recently caught live in production,
  not just in the original discovery). Threshold chosen from real data —
  every genuine result observed has been 0-10 items; 20 sits in the
  middle of a clean, wide gap between that and the lowest known anomaly
  (26) — validated against all 76 real stored results with zero false
  positives before being trusted. Don't loosen this threshold without
  re-checking the real data distribution first, and don't assume
  `matched_skills` values that predate 2026-08-22 in the DB are
  trustworthy without checking their length against it.
  **A local-Ollama replacement was evaluated 2026-08-29 and rejected
  (NO-GO) — see SESSIONS.md.** `huntloop.skills_matching` stays on Groq.
  `src/huntloop/skills_matching_ollama.py` is a complete, contract-
  identical Ollama-backend port kept **intentionally unwired** (nothing
  imports it) so the experiment is reproducible on better hardware;
  `scripts/validate_ollama_skills_match.py` is its validation harness
  (9 Step 4/5 sample jobs + the Duolingo soft-match / Palantir
  Deployment-Strategist known cases; writes gitignored
  `scratch_ollama_validation.json`). On this dev machine (Intel
  i5-8257U, 2 cores, 8 GB RAM, no GPU) every RAM-viable model
  (`qwen2.5:3b-instruct`, `llama3.2:3b`) failed quality — full-resume
  dumps, matched/missing inversion, prompt-echo, repetition spirals,
  and did NOT reproduce the soft-match nuance — and `qwen2.5:7b-instruct`
  timed out at 900s/job with <1 GB RAM free. Don't re-attempt an Ollama
  cutover here without new hardware (Apple Silicon ≥16 GB or a GPU box);
  the recommended path is staying on Groq and living with its 200K-TPD
  cap (the daily incremental volume, ~1–2 relevant jobs/day, fits one
  day's budget easily; the full backfill is a one-time multi-day cost
  the existing self-pacing script already handles).
  **Google Gemini's free tier is the WIRED fallback provider behind Groq
  as of 2026-08-29 (Step K) — see SESSIONS.md + full writeup in
  `huntloop-architecture-decisions.md`.** Why it became load-bearing:
  the ATS expansion (9→380 companies) pushed daily relevant-postings
  volume to ~120/day while Groq's real free-tier throughput at the wider
  set's ~9.5k-char JDs is only **~58 jobs/day** (long JDs collapse Groq's
  batch to ~1.7); Gemini's ~2,200/day (500 RPD × real batch 4.4) covers
  the gap. **Real current Gemini free-tier limits (from AI Studio —
  Google removed the static per-model table from the docs on 2026-08-18,
  limits are per-account now): 2.5-gen models cut to 20 RPD (was 1,000),
  `gemini-2.5-flash-lite` 404s; the only viable model is
  `gemini-3.5-flash-lite` at 15 RPM / 250K TPM / 500 RPD.** Quality
  (11-job side-by-side): equivalent to Groq on clear-cut jobs, better on
  2 SWE roles Groq whiffed, but more conservative on the Duolingo
  soft-match — hence Groq stays *primary*, Gemini is fallback-only.
  **Wiring:**
  - `huntloop.skills_matching_errors` — shared `DailyQuotaExhausted` /
    `ProviderResponseInvalid` (both backends raise the same classes).
  - `huntloop.skills_matching_router` — `match_skills_batch(resume, jds,
    state)` dispatching Groq→Gemini. `SKILLS_MATCHING_PROVIDERS` env
    (default `groq,gemini`; `groq` alone = pre-routing behaviour, no
    GEMINI_API_KEY needed). Two failover triggers: `DailyQuotaExhausted`
    marks a provider spent for the whole run; `ProviderResponseInvalid`
    (Groq `json_validate_failed` 400, ~18% of calls) fails over **just
    that batch**, provider stays primary. Run stops only on
    `AllProvidersExhausted`.
  - Per-provider batch sizing + pacing: each backend owns `MAX_BATCH_SIZE`
    / `MAX_BATCH_ESTIMATED_TOKENS` / `TARGET_TPM` / `MAX_RPM` (Groq
    5/7000/6000/30; Gemini 5/16000/200000/14 — 16000 computed from 250K
    TPM ÷ 15 RPM; `MAX_RPM=14` because Gemini's small batches otherwise
    run at ~30 req/min over its real 15 cap). `TokenPacer` enforces both
    a tokens/min and a requests/min bound; `backfill_skills_matching.py`
    chunks incrementally, re-reading `router.batch_limits(state)` (a
    4-tuple) each batch so the caps flip when Groq exhausts mid-run.
  - `backfill_skills_matching.py` query now filters `is_relevant IS TRUE`
    (29,921 → 12,972 NULL rows; 57% were irrelevant) and logs the
    relevant-backlog count each run as a capacity leading indicator.
  Verified with a real `--limit 250` run: 242 stored / 8 NULL, split
  groq 11 / gemini 231 (Groq's TPD was pre-spent → `DailyQuotaExhausted`
  fired after 11; 19 `json_validate_failed` per-batch failovers). Full
  12,972 backlog clears in ~5.6 days at steady state.
  `scripts/validate_gemini_skills_match.py` (side-by-side harness, writes
  gitignored `scratch_gemini_validation.json`) is unchanged.
  `GEMINI_API_KEY` is in `.env`.
  **A THIRD provider, Mistral (`huntloop.skills_matching_mistral`,
  `ministral-8b-latest`), was built + wired 2026-09-03 (see SESSIONS.md
  + `huntloop-architecture-decisions.md`) but is WIRED-BUT-NOT-RECOMMENDED
  and NOT in the default chain (unchanged by the 2026-09-04 groq_120b
  promotion below).** `mistral` is a known further stage only if
  explicitly appended (`groq_120b,groq,gemini,mistral`, needs
  `MISTRAL_API_KEY`).
  Its 11-job side-by-side validation against the Groq baseline came back
  POOR: full-résumé-dumps into `matched_skills` on 7/11 jobs (rejected by
  the `MAX_PLAUSIBLE_MATCHED_SKILLS = 20` backstop → left NULL), grounding
  inversion, ignores the short-phrase rule; ~4x slower than Groq. The
  free tier's flagship `mistral-small/-medium/-large` models are
  req-limited to 0 now (only the smaller `ministral-*` models are
  free-usable, and 8B is too small for this task — same conclusion as the
  2026-08-29 Ollama 3B/7B experiment). Kept wired purely as a last-resort
  capacity bucket if Groq AND Gemini are ever both walled. Don't add
  `mistral` to the default rotation without re-validating on a better
  (non-free-tier-gated) model. `scripts/validate_mistral_skills_match.py`
  is its harness (gitignored `scratch_mistral_validation.json`).
  `MISTRAL_API_KEY` is in `.env`.
  **A FOURTH stage, `groq_120b` (`huntloop.skills_matching_groq_120b`,
  `openai/gpt-oss-120b`), was built + wired 2026-09-04 (see SESSIONS.md +
  `huntloop-architecture-decisions.md`) - a SAME-ACCOUNT Groq capacity
  stage, not a new provider: reuses the existing `GROQ_API_KEY`, confirmed
  live to hold its own INDEPENDENT rate-limit bucket from the production
  `openai/gpt-oss-20b` model (1,000 requests/day, 8,000 tokens/min, both
  identical to 20b's own live numbers, and provably unaffected by 20b
  traffic - see the rate-limit reconciliation in SESSIONS.md for the full
  header-capture evidence). Its 11-job validation came back CLEAN: 0/11
  full-resume dumps (max 8 matched_skills, well under the 20 backstop),
  fixed the Palantir "Deployment Strategist" dump that every other
  backend (20b itself historically, Gemini, Mistral) has hit, correctly
  handled the Duolingo soft-match case, tight grounding/format.
  **PROMOTED to the default rotation 2026-09-04, ahead of `groq`** -
  `SKILLS_MATCHING_PROVIDERS` now defaults to `"groq_120b,groq,gemini"`
  (was `groq,gemini`): two independent, same-account Groq buckets tried
  bigger-model-first, Gemini still the fallback behind both. Needs no new
  env var (same `GROQ_API_KEY`). `scripts/validate_groq_120b_skills_match.py`
  is its harness (gitignored `scratch_groq_120b_validation.json`).
  **First unbounded (`--limit`-less) production run, 2026-08-30 (see
  SESSIONS.md) — measurement only, no code changed.** Backlog 12,682 →
  **11,057**; `job_postings` with a stored result 736 → 2,363 (733 of
  those `matched_skills = []`, a healthy 31%). Real observations that
  differ from the projections above: (1) **Groq contributed only ~9 jobs
  total** — its 200K TPD (still 200K, confirmed via a forced 429:
  "Limit 200000, Used 196649") was ~98% pre-consumed by the prior day's
  `--limit 250` run within the rolling-24h window. (2) **Gemini's
  effective throughput is ~1,000–1,300 successful jobs/day, not the
  ~2,200 projected** — sustained per-minute 429s ("You exceeded your
  current quota", classified transient → row left NULL, retried next
  run) shed 25–40% of calls; `MAX_RPM=14` vs Gemini's real 15 RPM hard
  cap has no margin. It still stops cleanly on the per-day (RPD ≈ 500)
  429 → `DailyQuotaExhausted` → `AllProvidersExhausted`, exit 0. (3)
  **The launchd daily orchestrator's stage-2 backfill and any manual
  `backfill_skills_matching.py` run once shared quota and raced the same
  NULL rows** (2026-08-30: 07:40 cron catch-up + 07:57 manual ran
  concurrently, ~700 rows processed twice). **FIXED 2026-08-30 — the
  script now takes a Postgres session-level advisory lock
  (`pg_try_advisory_lock`, key 1,751,937,901) in `main()`; a second
  concurrent invocation logs one WARNING and returns 0 without touching
  anything.** Run body moved to `_run_backfill()`; `_backfill_lock()`
  contextmanager; `tests/test_backfill_lock.py` (3 tests). Verified with
  a real deliberately-triggered overlap, not just code review (see
  SESSIONS.md).
  (4) **Gemini cold-start latency is server-side and sporadic, not a
  first-call warm-up** — ~1% of calls take 30–70s in short
  time-correlated bursts (seen simultaneously across two independent
  processes); the rest are 1–2.5s. (5) **Gemini quality on
  adjacent-but-not-a-fit roles**: ~1/8 of spot-checked Gemini rows put
  skills *named in the JD* into `matched_skills` without résumé grounding
  (e.g. "PCB layout" for an electronics role, "React"/"GraphQL" for a
  frontend role when the résumé has neither) — the inverse of the
  full-resume-dump failure, milder, and **not caught by
  `MAX_PLAUSIBLE_MATCHED_SKILLS = 20`** (these are 2–7 items). Groq is
  tighter but not immune. **PARTLY FIXED 2026-08-30 — both Gemini prompts
  (`_SYSTEM_PROMPT`, `_BATCH_SYSTEM_PROMPT`) gained an explicit
  résumé-grounding rule ("*the job posting mentions X*" ≠ "*the résumé
  shows X*"; only the second earns a `matched_skills` slot). Groq's
  prompt deliberately untouched.** A/B on 20 fresh jobs (same jobs, same
  model, OLD vs NEW prompt): rows with ≥1 ungrounded matched-skill
  45% → 25%, total ungrounded entries 19 → 9 (6 rows improved, 1
  regressed). Residual misses are on roles far from the résumé (TPM →
  "JIRA", HW verification → "SystemVerilog"). See SESSIONS.md 2026-08-30
  "Two backfill fixes".
- **REDESIGNED 2026-09-03 — `is_relevant` is now a title-only
  blue-collar denylist, NOT the hybrid keyword+embedding filter described
  in the rest of this bullet (see SESSIONS.md "Redesign `is_relevant` as
  a role-agnostic blue-collar denylist" + `huntloop-architecture-decisions.md`).**
  `is_relevant = NOT title_matches_denylist(title)` — 191-term
  manual/blue-collar denylist (driving, warehouse, production line,
  trades, janitorial, food service, retail floor, …), word-boundary,
  title only, no embedding/description. `sales`/`marketing`/`HR`/`legal`/
  `tax`/`accounting`/`partnerships`/`procurement` and the other
  previously hard-excluded business functions are **no longer blocked**;
  clinical/healthcare was never on the denylist and stays included. Bare
  `warehouse` is carved out when the title contains "data warehous".
  `classify_relevance(title, embedding_similarity=None)` keeps its old
  2-arg shape but ignores arg 2; `REFERENCE_TEXT`/`cosine_similarity`/
  `HARD_EXCLUDE_KEYWORDS`/`SOFT_EXCLUDE_KEYWORDS`/`EMBEDDING_SIMILARITY_THRESHOLD`/
  `SOFT_EXCLUDE_RESCUE_THRESHOLD` are retained as inert constants for
  back-compat imports only. Full recompute (`scripts/recompute_relevance.py`,
  new, title-only, runs in plain `.venv`) over all 94,060 rows:
  is_relevant True **35,897 → 90,869**, False **58,163 → 3,191** (171
  True→False, all blue-collar; 55,143 False→True). `job_postings.embedding`
  / `match_score` / the embedding pipeline were NOT touched. Known
  follow-up: the torch-less `pipelines._classify_and_embed` path still
  leaves `is_relevant` NULL early (daily scrape runs in Docker w/ torch;
  recompute/backfill scripts mop up NULLs). The rest of this bullet
  describes the superseded hybrid design and is kept for history. —
- **A hybrid keyword + embedding-similarity relevance pre-filter exists,
  added 2026-08-24 (see SESSIONS.md) — `job_postings.is_relevant`
  (nullable `Boolean`, migration `0900f3514ad2`), meant to flag whether
  a posting is a software-engineering/technical role at all before
  resume-matching or skills-analysis spend effort on it. Flags only —
  never deletes/filters rows out of `job_postings`.** Logic lives in
  `huntloop.relevance_filter`: `INCLUDE_KEYWORDS`/`EXCLUDE_KEYWORDS`
  matched against `job_title` (built by reading all 425 real distinct
  titles in the live dataset, not a generic guess — e.g. bare `"analyst"`
  and `"technical"` were deliberately left out after the real data showed
  them mostly attached to non-technical titles here, and `"finance"`/
  `"financial"` were tried as excludes and dropped after they'd have
  wrongly overridden the one real technical title containing "Finance",
  `Data Scientist, Finance`, to irrelevant), combined with cosine
  similarity between each job's `title+description` embedding
  (`huntloop.embeddings.embed_texts()`, same all-MiniLM-L6-v2 model as
  resume/job-match scoring) and one fixed `REFERENCE_TEXT` describing the
  *category* of technical work — deliberately not a resume, since this
  filter must behave the same regardless of which resume is active.
  **`EMBEDDING_SIMILARITY_THRESHOLD = 0.29`, chosen from real measured
  similarities** (`scripts/calibrate_relevance_threshold.py`, run once
  against the exact real known-relevant/known-irrelevant rows named in
  this task) — known-irrelevant real titles (Wealthfront Fraud
  Operations Specialist, Checkr Chief of Staff, Duolingo Creative
  Director) topped out at 0.2547; known-relevant real titles (3 real
  Palantir Software Engineer variants, Palantir Platform Engineer,
  Duolingo Senior Data Science Manager) started at 0.3240 — a clean,
  non-overlapping gap, 0.29 sitting at its midpoint. Combination:
  `is_relevant = (keyword_include OR embedding_similarity >= 0.29) AND
  NOT keyword_exclude` — keyword-exclude is an unconditional override
  (a title explicitly naming a non-technical function like legal/sales/
  marketing/tax/recruiting is a higher-confidence signal than a generic
  word like "engineer" appearing elsewhere in the same title, e.g.
  "Embedded Legal Engineer", "Marketing Engineer"), while include and
  embedding-similarity are OR'd since keywords alone would miss
  obliquely-worded technical titles and embedding similarity alone would
  need an unnecessarily conservative threshold on its own.
  **Exclude split into HARD and SOFT as of 2026-08-30 (see SESSIONS.md).**
  `HARD_EXCLUDE_KEYWORDS` (everything except the two below) is still the
  unconditional override just described — `sales` stays hard (241/253
  "Sales Engineer" rows correctly excluded; flagged as a future-look
  candidate, not changed). `SOFT_EXCLUDE_KEYWORDS = ["customer success",
  "solutions consultant"]` are excluded **only if the same category
  embedding similarity is also `< SOFT_EXCLUDE_RESCUE_THRESHOLD = 0.335`**
  — those two phrases straddle the technical/non-technical line in the
  real data (Rubrik "Customer Success Engineer" = support, sim 0.3232,
  stays excluded; Palantir "Forward Deployed Enablement Engineer –
  Customer Success" = builds tooling, sim 0.44, and Figma "Enterprise
  Solutions Consultant" = deep technical pre-sales, sim 0.3403, both
  rescued). `classify_relevance` checks hard first, then `soft_exclude ->
  (sim >= 0.335)`, then the normal `include OR sim >= 0.29`; the include
  keyword is deliberately NOT consulted for a soft-exclude title
  ("Customer Success Engineer" has "engineer" too and must stay excluded
  on a weak signal). Threshold calibrated from all 318 real
  soft-exclude-titled rows (`scripts/calibrate_soft_exclude_threshold.py`);
  0.335 sits in the widest real gap in the (0.3232, 0.3403] window.
  `scripts/reclassify_soft_excludes.py` (Docker, batched) re-ran those
  318 rows: 27 flipped to relevant (23 distinct — ~19 genuinely
  technical, ~4 residual "Manager"/"Program Manager"/"Ops Analyst" false
  positives accepted as MVP noise; the other 282 real CS-Manager /
  low-similarity SC roles correctly stay excluded). `EXCLUDE_KEYWORDS`
  kept as a back-compat alias (= HARD + SOFT); `tests/test_relevance_filter.py`
  added (26 pure-logic tests). **Deliberately
  does NOT reuse the existing `job_postings.embedding` column** — that
  one is computed from `job_description` alone for resume-match scoring
  (a separate, already-documented purpose); this filter computes its own
  `title+description` embedding on the fly per job during the backfill
  run instead of storing one, to avoid any risk of silently changing
  resume-match semantics. `scripts/backfill_relevance.py` (same
  batch-of-100/`IS NULL`/interrupt-safe pattern as `scripts/
  backfill_embeddings.py`) classified all 606 real rows in one pass (368
  relevant, 238 not, 0 left NULL) — verified against every known example
  named in the task via direct `psql` query, all correct. Two accepted
  MVP-level imprecisions found during spot-checking and left as-is
  (embedding-only false positives, no keyword involved): `"GRC Program
  Manager"` and `"Product Designer"` — not blockers, worth revisiting
  only if it turns out to matter in practice.
  **Wired into the live insert path as of 2026-08-24 (see SESSIONS.md's
  "Wire the relevance pre-filter into the live scrape pipeline" entry) —
  `JobDataPipeline.process_item()` (`src/huntloop/pipelines.py`) now
  calls `classify_relevance()` for every newly-inserted row, so
  `is_relevant` is populated at insert time, not left NULL for a later
  manual backfill.** `REFERENCE_TEXT`/`EMBEDDING_SIMILARITY_THRESHOLD`/
  `classify_relevance` were reused completely unchanged — nothing
  re-derived or re-tuned in this step. The reference-text embedding is
  cached once per `JobDataPipeline` instance (`self._reference_embedding`)
  — computed at most twice per full `main.py` run (one pipeline instance
  per spider/platform, confirmed via `main.py`'s `process.crawl()` call
  pattern), never once per row. `huntloop.relevance_filter.
  cosine_similarity()` is a small extracted helper (pure vector math,
  not tuned logic) now shared by the pipeline and both relevance
  scripts, replacing three separate copies.
  **A real, load-bearing environment constraint governs this wiring, and
  was resolved via an explicit user decision, not a silent choice**: the
  embedding half needs torch, but the actual daily-scheduled scraper
  (the `launchd` job from the prior step) runs `main.py` via this
  machine's local `.venv`, which — as established repeatedly elsewhere
  in this doc — cannot run torch at all here. `JobDataPipeline` therefore
  **degrades gracefully**: if the embedding model can't be used for this
  run — `sentence-transformers`/torch not importable, **or** the model
  itself failing to load (e.g. offline with no HuggingFace cache, as in
  CI) — `_get_reference_embedding()` logs one warning per spider run (not
  per row) and leaves `is_relevant`/`embedding` NULL for that run's
  inserts, rather than crashing the scrape; the same applies to an
  isolated per-row embedding failure, which leaves just that row's
  columns NULL rather than rolling back its whole insert.
  **`_get_reference_embedding()` catches any exception here, not only
  `ImportError` (fixed 2026-09-03, see SESSIONS.md — an offline model
  load raises `OSError`/`LocalEntryNotFoundError`, which the old
  `ImportError`-only catch let through, aborting the insert; this had
  also left `master`'s CI silently red for ~10 days).**
  **As of 2026-08-31 the same method (`_classify_and_embed`, renamed from
  `_classify_relevance`) also computes and stores
  `job_postings.embedding` — the resume-match embedding of the
  description — in the same batched model call, so the identical
  graceful-degradation rules apply to both columns and a new ATS source
  no longer needs a manual `backfill_embeddings.py` pass after its first
  scrape (see SESSIONS.md).**
  **The "real scheduled scrapes leave is_relevant NULL" gap noted when
  this was first wired in is now closed, as of 2026-08-24 (see the next
  bullet and SESSIONS.md) — the daily scraper itself now runs via
  Docker, so this graceful-degradation path is no longer expected to
  ever trigger for the daily scrape specifically.** It's left in place
  (not removed) as a genuine safety net — a real per-row embedding
  failure, a future environment without Docker, etc. Verified end-to-end
  for real via `docker compose run --rm app python main.py` against the
  real local Postgres before the Docker migration: 5 genuinely new rows
  inserted, all 5 had `is_relevant` populated immediately (confirmed via
  direct `psql` query); re-running `scripts/backfill_relevance.py`
  immediately after found **0 rows left to classify** — the insert-time
  path and the batch-backfill path fully agree. `pytest`: 72/72 passing,
  including one new assertion (`test_process_item_inserts_job_posting`)
  directly exercising the graceful-degradation path (this test env also
  has no torch). Don't touch skills-matching/Groq/queue work when
  extending this — that's a deliberately separate, later step.
- **The daily scraper itself now runs via Docker, not the local `.venv`,
  as of 2026-08-24 (see SESSIONS.md's "Move the daily scrape to run via
  Docker" entry) — closing the gap noted in the bullet above.**
  `scripts/run_orchestrator_cron.sh`'s stage 1 (the scraper) now runs
  `docker compose run --rm --build -e DATABASE_URL=... app python
  main.py` instead of `.venv/bin/python main.py` — the same `app` image
  every other embedding-dependent script in this project already needs.
  Stage 2 (skills-matching) is untouched, still `.venv/bin/python
  scripts/backfill_skills_matching.py` — it only talks to Groq over
  HTTP, no torch dependency, no reason to move it. The launchd plist
  itself (`~/Library/LaunchAgents/com.huntloop.scraper.plist`) is
  unchanged — it already only invoked the wrapper script, so all the
  real changes live in the wrapper.
  **Two real environment gaps were found and fixed, not assumed away:**
  (1) launchd's job environment does not inherit an interactive shell's
  `PATH` — confirmed directly via `launchctl print`, which showed this
  job's own default `PATH` as just `/usr/bin:/bin:/usr/sbin:/sbin`,
  missing `/usr/local/bin` where Docker Desktop's `docker` CLI is
  symlinked on this machine; without a fix, `docker` would never be
  found at all. Fixed by having the wrapper script `export
  PATH="/usr/local/bin:$PATH"` itself, rather than relying on the
  plist. (2) `DATABASE_URL` is overridden per-invocation (`sed
  's/localhost/host.docker.internal/'` against the real value in
  `.env`) since `docker-compose.yml`'s own default for `app` points at
  its own small `db` service, not the real local system Postgres where
  the actual scraped data lives (see the two-Postgres-instances note
  below) — same substitution pattern already used for every prior
  manual `docker compose run` against this Postgres.
  `--build` is passed on every scheduled run so a stale image can never
  silently run in production — this is now the ongoing daily path, not
  a one-off manual invocation, so staleness would otherwise be
  invisible.
  **A separate, real logging regression was found and fixed while
  verifying this**: `docker-compose.yml`'s `app` service had no volume
  mount for `logs/`, so stage 1's rotating `logs/huntloop.log` file
  handler was writing only inside the container's own ephemeral
  filesystem — discarded on `--rm`, with only the console-handler
  output that happened to flow through to `logs/cron.log` surviving.
  Fixed the same way `api`'s `./data/resumes` mount already solves the
  identical class of problem: `app` now has a `./logs:/app/logs`
  volume mount too. A real host/container UID mismatch (host `niramaykelkar`
  vs. the container's `huntloop` user, uid 999) was checked directly,
  not assumed safe — confirmed by an actual write-then-read-from-host
  test that Docker Desktop's macOS file-sharing layer doesn't enforce
  strict POSIX ownership on bind mounts here, so this isn't a problem
  on this machine; worth re-checking if this project ever runs on
  Linux, where bind-mount permissions are enforced strictly.
  **Docker-down behavior, researched and reported per this task's
  explicit ask, not silently fixed**: simulated an unreachable Docker
  daemon (bad `DOCKER_HOST`, since deliberately quitting the user's real
  Docker Desktop mid-session felt too disruptive) — `docker compose run`
  fails immediately with a clear, specific error to stderr ("failed to
  connect to the docker API ... check if the path is correct and if the
  daemon is running") and exit code 1, not a silent failure or hang.
  Since the wrapper redirects all stage output into `logs/cron.log`,
  this is fully visible after the fact, and the wrapper's existing
  exit-code bookkeeping correctly reflects the failure.
  **The "Docker Desktop doesn't start at login" gap flagged here is now
  closed, 2026-08-24 (see SESSIONS.md's "Enable Docker Desktop
  autostart-at-login" entry) — Docker Desktop's "Start Docker Desktop
  when you sign in" setting is now enabled on this machine.** Verified
  via the real, live-authoritative config file
  (`~/Library/Group Containers/group.com.docker/settings-store.json`'s
  `AutoStart` key — the neighboring `settings.json` is stale/vestigial
  on this install, its mtime frozen since 2022, not what this Docker
  Desktop version actually reads) read `true` immediately after the
  change and **stayed `true` after a real `docker desktop restart`**
  (confirmed via a genuine PID change on the Docker Desktop GUI process,
  not just re-reading the file) — real evidence the app's own startup
  logic preserves this preference, not just that a checkbox looked
  checked. Also checked the macOS-level login-item registration directly
  via `sfltool dumpbtm` (Apple's Background Task Management inspector,
  not the legacy AppleScript login-items list, which doesn't reflect
  this kind of registration at all) — found the `DockerHelper` login
  item already `enabled` at the OS level both before and after, revealing
  the real two-layer mechanism: `DockerHelper` is a small, always-
  registered login launcher that itself checks Docker Desktop's
  `AutoStart` preference at each login to decide whether to actually
  launch the app. **A genuine full OS reboot was not performed** (would
  have killed the working session; felt disproportionate given two
  independent strong pieces of evidence already in hand) — the true
  first-real-reboot confirmation is still open, noted explicitly as
  such rather than assumed. **This is a local-machine-only fix,
  deliberately without any accompanying local monitoring/retry
  infrastructure, and will be entirely superseded if/when the scraper
  moves to a cloud deployment** (a managed container scheduler, or a
  Linux host running Docker Engine under `systemd` instead of Docker
  Desktop) — don't build on top of this Docker-Desktop-specific
  mechanism (`settings-store.json`, `DockerHelper`, `sfltool`) as if
  it were permanent infrastructure.
  **Verified end-to-end for real**: `launchctl kickstart -p
  gui/<uid>/com.huntloop.scraper` (same method as the original launchd
  migration) fired a real run that built/ran the `app` image, inserted 4
  genuinely new `job_postings` rows, all with `is_relevant` populated
  immediately (confirmed via direct `psql` query, 0 NULLs before and
  after), and finished stage 1 with a real exit code 0 — visible in
  `logs/cron.log` under the updated `"stage 1/2: scraper orchestrator
  (main.py, via docker compose run)"` label, including the real Docker
  build output. Re-ran `scripts/backfill_relevance.py` immediately after
  and got **0 rows left to classify** again. `pytest`: 72/72 passing,
  unchanged (this step touched no application code, only the wrapper
  script and `docker-compose.yml`).
- **Feedback capture + triage pipeline, added 2026-10-02 (see
  SESSIONS.md) — a new, independent concern from everything above, not
  job scraping/matching related.** New `feedback` table (migration
  `b7e2a5c9f1d3`, `huntloop.db_models.Feedback` + the
  `FeedbackCategory`/`FeedbackTriageStatus`/`FeedbackStatus` enums).
  `user_id` is nullable with no FK — there's no users table yet; it
  exists only so a future auth migration can backfill it without a
  schema change.
  **`POST /feedback`** (public, no auth, `huntloop.api.routers.feedback`,
  mounted unconditionally in both demo and non-demo mode — unlike
  `drafting`/`resumes.unsafe_router`, it carries no BYOK key and isn't
  gated by `DEMO_MODE`) persists a row immediately
  (`triage_status=pending`, `status=open`, `is_public=false`) and
  returns **202** — it never calls an LLM inline, so a traffic spike
  costs DB rows, not LLM quota, and a provider outage can never fail a
  submission. `category`/`description` are validated manually in the
  router (not a Pydantic `Literal`/`Field`), so a bad value is a clear
  **400**, not FastAPI's generic 422 — same precedent as
  `drafting._require_api_key`. Rate limiting (5/hour, 20/day per IP) is
  a plain `COUNT(*) FROM feedback WHERE ip_hash = ... AND created_at >=
  ...` query against this same table — no new infrastructure (no Redis,
  no in-memory counter), since this is low-traffic; `ip_hash` is a
  SHA-256 hash of the submitter's IP, used for nothing else. `context`
  (JSON) is assembled server-side from the request (page path, active
  filters, recent client errors the frontend chose to attach) — never
  accepted as one opaque client-asserted blob.
  **`GET /feedback/public`** returns ONLY rows with `is_public=true`,
  and only `category`/`llm_summary`/`status`/`created_at` — **never
  `raw_text`**, by design: a public page rendering arbitrary
  unauthenticated user text would be an open publishing surface, so the
  only thing ever shown publicly is this project's own fixed-prompt LLM
  summary, never the submitter's own words.
  **`scripts/triage_feedback.py`** is the async triage pass — same
  standalone-script convention as `backfill_skills_matching.py`, but on
  its OWN short-interval schedule (`scripts/run_feedback_triage_cron.sh`
  + reference plist `scripts/com.huntloop.feedback-triage.plist`,
  `StartInterval=300`s — **not installed on this machine**, a standing
  recurring job spending real LLM quota is left as a machine-config
  decision; install/remove commands are in both files), separate from
  the once-daily `run_orchestrator_cron.sh`. It triages
  `triage_status=pending` rows up to `TRIAGE_DAILY_BUDGET` (env var,
  default 100, counted as `triage_status=done AND updated_at` falling
  today) — once hit, remaining pending rows are left exactly `pending`
  (never `skipped_budget`, which this script never sets itself) for a
  later run. Summarization is `huntloop.feedback_triage.
  summarize_feedback()` — Groq-primary/Gemini-fallback via the SAME
  `GROQ_API_KEY`/`GEMINI_API_KEY` env vars `huntloop.skills_matching*`
  use, but its own small module with its own prompt, **not** a new mode
  bolted onto `huntloop.skills_matching_router` (that router's
  prompt/JSON contract is hardcoded to resume-vs-job-description
  matching — a different task; no batching or per-day quota tracking
  here either, since this is low-volume). No advisory lock (unlike
  `backfill_skills_matching.py`) — a short-interval, low-volume, per-row
  script has much lower collision stakes than the once-daily multi-hour
  backfill the lock exists for.
  **`scripts/review_feedback.py`** (`list [--all]` / `set <id>
  [--status ...] [--public true|false]`) is the only way to change
  `status`/`is_public` today — a terminal tool against the real
  database, deliberately not a web admin panel (that comes later, after
  auth exists, as a protected route rather than a new
  unauthenticated-access problem).
  **Frontend**: `frontend/src/app/status/page.tsx` (public, read-only,
  grouped by status) and `frontend/src/components/FeedbackTrigger.tsx`
  (a corner trigger button + panel, mounted once in `layout.tsx` so it's
  reachable from every page — posts to `POST /feedback`, shows a
  "Thanks, got it." toast via the existing `useToast()`; no submission
  history shown inline). Both have no test coverage yet (same
  not-yet-covered gap the frontend test-suite bullet above already
  documents for pure-presentation components).
  **Metrics/observability**: `huntloop.feedback_metrics` (own
  `CollectorRegistry`, own Pushgateway job name `huntloop_feedback`,
  same never-raises-on-push-failure contract as every other
  `push_*_metrics()` in this project) pushes
  `huntloop_feedback_submitted_total{category}` once per submission
  (synchronously, inline in the request — this is a low-volume
  unauthenticated endpoint, not a batch job, so there's no "end of run"
  moment to push once at). A new provisioned Grafana dashboard,
  "HuntLoop Feedback Volume"
  (`observability/grafana/provisioning/dashboards/huntloop-feedback.json`,
  uid `huntloop-feedback`), 2 panels — volume by category over time, and
  a current-total bar chart — same local-only `observability` Compose
  profile as every other dashboard; no Prometheus scrape-config change
  needed (it already scrapes the one shared Pushgateway target,
  regardless of job name). Verified end-to-end against a real
  submission: Grafana's own datasource proxy returned the same value as
  a direct Prometheus query.
  **Verified for real** (see SESSIONS.md for the full list): migration
  applied + table confirmed via direct query; a real submission through
  the running API landed `pending`/`open`/not-public; 6 same-IP
  submissions in an hour correctly 429'd on the 6th; a real triage run
  populated `llm_summary` and flipped to `done`; the daily budget was
  exercised both ways (blocks at the cap, resumes once reset); marking a
  row public made it (and only it) appear on `GET /feedback/public` with
  no `raw_text` anywhere in the response; the real `/status` page and
  `FeedbackTrigger` panel were driven in an actual browser against a
  real running API + Next.js dev server. **One real snag hit along the
  way**: this dev machine already runs long-lived `huntloop-api`/
  `huntloop-frontend` Docker containers bound to
  `127.0.0.1:8000`/`127.0.0.1:3000` (pre-existing, pointed at the
  bundled `db` Compose service — see the FastAPI bullet's own
  `api`/`db` note above, now out of date in detail: as of whatever
  changed `docker-compose.yml`'s `api.DATABASE_URL` to the bundled `db`
  service, `api` is no longer pointed at `host.docker.internal`/the real
  local system Postgres by default) — `localhost` on this machine
  resolves IPv6 first, so a locally-run `uvicorn`/`next dev` on the same
  port numbers bound successfully on IPv6 while curl/the browser kept
  hitting the stale Docker containers on IPv4; verification was re-run
  on alternate ports/`127.0.0.1`-only + an explicit `CORS_ALLOWED_ORIGINS`
  to isolate from those containers rather than touching them.
  **Backend suite**: 499 passed; 3 pre-existing failures in
  `tests/test_backfill_lock.py`, unrelated to this work (a stray
  orphaned Postgres connection predating this session holds the
  skills-matching advisory lock on this dev machine — confirmed via
  `pg_locks`/`pg_stat_activity`, left alone rather than unilaterally
  terminated). `test_api_demo_mode.py`'s hardcoded route-count
  assertion was updated 12 → 14 (the two new feedback routes mount
  unconditionally in both demo and non-demo mode).

## Key architectural decisions (already made — don't re-litigate)

- **`job_url` is the canonical unique key** on `job_postings`, not `gh_job_id`.
  `gh_job_id` isn't reliably unique and won't generalize to non-Greenhouse
  sources later.
- **`Company.h1b_sponsorship` is intentionally kept** even though it's a bare
  boolean today. It will likely be superseded by real sponsorship data —
  `LcaDisclosure` (`lca_disclosures` table) now holds raw DOL LCA disclosure
  records with `employer_name_normalized` populated for every row (see
  `src/huntloop/matching/normalize.py`), `find_matching_employers()`
  (`src/huntloop/matching/fuzzy_match.py`) ranks candidate
  `employer_name_normalized` values against a raw company name, and
  `get_sponsorship_summary()` (`src/huntloop/matching/sponsorship.py`)
  applies that to a real `Company` row to answer "does this company
  sponsor, and how much." Don't "fix" `h1b_sponsorship` in the meantime by
  removing or redesigning it unprompted.
- **There is deliberately no FK from `companies` to `lca_disclosures`.**
  `get_sponsorship_summary()` computes the match at call time via
  `find_matching_employers()` instead of a stored link — a company can span
  multiple legal entities in the LCA data, and match confidence varies row
  to row, so a rigid one-to-one FK would be premature normalization. Don't
  add that FK unprompted; if it's ever needed, that's a deliberate future
  decision, not a "fix" for this being "incomplete."
- **`companies.matched_sponsor_employer_name` (nullable `String(255)`,
  migration `c3be4d9c3a36`, added 2026-08-23) is a denormalized cache of
  the single top-scoring match, not a substitute for the above FK
  decision or for `get_sponsorship_summary()`'s live aggregation.**
  Populated by `scripts/resolve_sponsor_matches.py`, which calls the
  existing `find_matching_employers()` unchanged for every `companies`
  row and stores `matches[0].employer_name_normalized` (or `NULL` if
  `find_matching_employers()` returns no match above threshold — never a
  forced low-confidence guess); `sponsor_name_overrides` entries (e.g.
  Kraken → `KRAKEN TECHNOLOGIES US`) still take precedence automatically,
  since `find_matching_employers()` itself checks that table first. Not
  automatically kept fresh — re-run the script after `sponsor_name_overrides`
  changes or new LCA data is ingested. `get_sponsorship_summary()` still
  aggregates across *every* `EmployerMatch` `find_matching_employers()`
  returns (a company can span multiple legal entities), not just this one
  cached top match — don't narrow it to use this column instead, that
  would silently drop legitimate multi-entity aggregation. This step is
  persistence only: no aggregate sponsor-summary query changes and no API
  endpoint wiring yet — both are deliberately deferred to a later step.
- **A second, distinct `get_sponsorship_summary(session, company)` now
  exists at `src/huntloop/api/sponsor_summary.py` (added 2026-08-23, see
  SESSIONS.md) — same function name as
  `huntloop.matching.sponsorship.get_sponsorship_summary()`, deliberately
  a different module, different behavior. Don't merge or confuse the
  two.** This one reads only `company.matched_sponsor_employer_name`
  (Step 8's persisted column, no live `find_matching_employers()` call —
  the API-facing version is required to be fast/deterministic per
  request) and returns `None` outright if unset. It returns exactly four
  fields, matched to what `GET /jobs/{id}` needs: LCAs filed in the
  employer's most recent `fiscal_year`, median wage, the single most
  frequent job title, and `case_status` of the single most recently
  received filing — not the older function's fuller
  fiscal-year-by-fiscal-year/multi-entity aggregate. **Median wage is
  computed over `WAGE_UNIT_OF_PAY = 'Year'` rows only — checked against
  real data before deciding, not assumed (see SESSIONS.md's wage-unit
  investigation): 99.86% of the 9 matched companies' LCA rows are
  `'Year'`; the small remainder includes two rows that are unmistakably
  annual salaries mislabeled `'Week'`/`'Month'` (same Phase 1-audit
  error pattern, reconfirmed on this table) and one plausible genuine
  `'Hour'` row. Don't remove this filter or try to annualize non-`'Year'`
  rows instead — that would re-trust a unit field already shown to be
  unreliable on exactly the rows where it matters most.** `latest_case_status`
  is deliberately NOT wage-unit-filtered — it reflects whatever the
  single most recently *received* filing actually says, unit notwithstanding.
  `GET /jobs/{id}` now returns `ats_platform` (trivial `Company`
  passthrough), `sponsor` (this summary, `null` if unresolved), and
  `salary_estimate` (`{amount, basis}`, `null` whenever `sponsor` is
  `null` or has no `median_wage` — `basis` is always the fixed string
  `"Estimated from DOL wage filings for this employer, not job-specific"`,
  so this can never be mistaken for a real posted salary). `GET /jobs`
  list rows gained one lightweight boolean, `has_sponsor_history`
  (`Company.matched_sponsor_employer_name is not None`, added to the
  existing join — no per-row aggregate query, for performance).
  **`JobSummary` rows also carry `salary_estimate` (`{amount, basis}`,
  same shape/meaning as `GET /jobs/{id}`'s) as of 2026-09-07 (see
  SESSIONS.md's "Make the job list scannable…" entry) — it reuses the
  `_salary_estimate_expr()` scalar subquery already built for the
  `salary_min`/`salary_max` filters, now also selected as a column;
  `JobDetail` inherits it rather than redeclaring it. The `JobCard` /
  `JobTable` list views surface it (labeled "Est." + basis tooltip),
  alongside `employment_type` and a shared `SponsorBadge` pill.** The
  frontend was NOT touched in this step — wiring this real data into the
  job detail page's sponsor sidebar (currently omitted, per the reskin
  step's known display gap) was the deliberately deferred next step.
  **That gap is now closed, 2026-08-23 (see SESSIONS.md's "Wire real
  sponsor summary/ATS/salary into the frontend" entry) — frontend-only,
  no backend/API code touched.** `frontend/src/types/api.ts`/`lib/theme.ts`
  gained the matching types (`SponsorSummary`/`SalaryEstimate`,
  `has_sponsor_history`) and a `formatWage()` helper.
  `JobDetailClient.tsx`'s sidebar gained `Salary est.`/`ATS` rows (with
  the mandatory disclaimer shown directly under them, not summarized) and
  a second "H-1B sponsorship" card ported 1:1 from the mockup's
  `sponsorCardStyle`/`sponsors`/`noSponsor` branches, including its exact
  fallback copy. `JobCard.tsx`/`JobTable.tsx` both gained the mockup's
  inline sponsor indicator next to location. Verified against the real
  running system for every real scraped company
  (checkr/duolingo/figma/palantir/wealthfront, all resolved sponsor
  matches) — the `sponsor === null` fallback render path itself was
  verified via a deliberate in-browser fetch-response override (no real
  company currently lacks a match, since Ashby/Workday spiders are
  unbuilt and kraken has 0 scraped postings), not against real
  unmatched-company data. `pytest` unaffected (58/58, unchanged).
- **`normalize_employer_name()` (`src/huntloop/matching/normalize.py`) is
  mechanical normalization only** — uppercase, strip periods/commas,
  collapse whitespace, drop a trailing legal-entity suffix
  (`INC`/`LLC`/`LLP`/`LP`/`CORP`/`CO`/`LTD`/`PLLC`/`PC`). It is not full
  entity resolution and deliberately does not merge companies beyond that
  (e.g. it won't collapse `LIMITED`-suffixed names or handle abbreviation/
  alias matching). Don't expand its suffix list or scope unprompted — see
  the false-positive-risk test in `tests/test_normalize.py` for why it
  stays conservative.
- **`find_matching_employers()` (`src/huntloop/matching/fuzzy_match.py`)
  checks `sponsor_name_overrides` before fuzzy matching, and short-circuits
  if a confirmed mapping exists.** Fuzzy matching uses `rapidfuzz`'s
  `token_set_ratio` (not `WRatio` — `WRatio`'s partial-ratio component
  scores the audit's flagged false positive, "INFOSYS" vs.
  "A&A INFOSYSTEMS", at 90, indistinguishable from a true match) at a
  default threshold of 88, chosen empirically against real
  `lca_disclosures` data (see SESSIONS.md for the concrete scores). It does
  not eliminate every collision between unrelated companies sharing a
  generic industry-suffix word (e.g. "... CONSULTANCY SERVICES",
  "... INFOSYSTEMS") — that residual ambiguity is what
  `sponsor_name_overrides` is for. `sponsor_name_overrides` has one real
  entry as of 2026-08-22 (see SESSIONS.md): `kraken` ->
  `KRAKEN TECHNOLOGIES US`, added because fuzzy matching also pulled in
  `RAKEN` (`Raken, Inc.`, an unrelated construction-software company) at
  90.9 — above the 88 threshold. The other 5 currently-scraped companies
  (`checkr`, `duolingo`, `figma`, `palantir`, `wealthfront`) each resolved
  to a single unambiguous 100.0-scoring match and needed no override.
- **Test isolation uses a throwaway Postgres schema per test session**, not
  `pytest-postgresql`. Reuses the existing local Postgres server rather than
  spinning up a separate instance. See `tests/conftest.py`.
- **`tests/conftest.py`'s isolated-schema `search_path` must NEVER include
  `public` — this caused a full production data wipe on 2026-08-22 (see
  SESSIONS.md for the full incident writeup).** Adding `,public` (to make
  the `vector` type, which lives in `public`, resolve for the new
  embedding columns) silently broke isolation: `Base.metadata.
  create_all()`'s own `has_table()` check resolves unqualified table
  names via search_path, found the *real* `public.companies`/
  `public.job_postings`/etc. before the fresh test schema had any tables
  of its own, and concluded they already existed — so it silently
  created nothing in the isolated schema, and every DB-touching test ran
  against real production data instead. `test_pipeline.py`'s teardown
  (`DELETE FROM` every table after each test) then wiped `companies`,
  `job_postings`, `job_sources`, `job_locations`, `job_skills`,
  `job_metadata`, `lca_disclosures` (1.43M rows), `sponsor_name_overrides`,
  and `resume_versions` for real. All data was restored (see SESSIONS.md
  for the exact recovery sequence and which parts were exact vs.
  necessarily inexact restores), and `alembic_version` was untouched
  throughout (`DELETE FROM` never touches it, and it isn't part of
  `Base.metadata`) — but this is exactly the failure mode to never
  reintroduce. If a future migration adds another type/extension that
  needs to resolve in DDL under this fixture, schema-qualify the type in
  its SQLAlchemy definition instead (see `huntloop.db_models.Vector`, a
  `pgvector.sqlalchemy.Vector` subclass whose `get_col_spec()` always
  emits `public.vector(n)` — confirmed via pgvector's own source that this
  only affects DDL, not value bind/result processing, so it's safe
  everywhere) — never by touching this fixture's search_path.
  **Standing check**: after touching `tests/conftest.py` or the isolated-
  schema mechanism, capture real production row counts before running the
  suite and confirm they're unchanged after — don't trust "tests green"
  alone.
- **`JobPosting.location` was dropped** in favor of the `job_locations` child
  table, which is the canonical one-to-many representation.
- **`alembic upgrade head` is the sole source of schema creation.** The
  pipeline and `test_db_insert.py` used to fall back to
  `Base.metadata.create_all()`, which could silently create tables outside
  Alembic's bookkeeping and caused a real `alembic_version` drift incident
  (see SESSIONS.md). That fallback is gone — don't re-add it. (Exception:
  `tests/conftest.py` still uses `create_all()`, but only to build tables in
  pytest's throwaway per-session schema, a separate test-isolation
  mechanism, not app schema creation.)
- **Scrapy's own logging is intentionally disabled** (`LOG_ENABLED = False`
  in `settings.py`) so `huntloop.logging_config.setup_logging()` is the only
  thing configuring the root logger — Scrapy's internal log lines (e.g.
  `scrapy.core.engine`) still show up, just formatted by our handlers
  instead of Scrapy's own. Don't re-enable `LOG_ENABLED` or set a Scrapy
  `LOG_LEVEL` — that would produce duplicate log lines (Scrapy's handler
  plus ours, both attached to root). `src/huntloop/test_db_insert.py` still
  has its own separate `logging.basicConfig()` + emoji-prefixed messages —
  intentionally left alone (it's a manual smoke-test script, not part of
  the shared-logging migration).
  **`main.py` raises the `scrapy` logger to `INFO` (one
  `logging.getLogger("scrapy").setLevel(logging.INFO)` line, right after
  the `CrawlerProcess(get_project_settings())` constructor — it MUST be
  after, since that constructor runs Scrapy's `configure_logging()`,
  which unconditionally `dictConfig`s the `scrapy` logger back to DEBUG
  regardless of `LOG_ENABLED = False`), added 2026-09-06.** Reason: the
  2026-09-06 log-verbosity investigation traced `cron.log`'s
  multi-GB-per-run growth to `scrapy.core.scraper` logging a full
  `pprint` dump of every scraped `JobPostingItem` — including the entire
  `job_description` HTML — at DEBUG, ~164 lines per posting × ~85k
  postings per run, i.e. ~14M lines / ~1 GB per scheduled run, ~99.98%
  of `cron.log`. **It was NEVER the skills-matching stage** (that
  stage's whole per-run output is a few thousand one-line records,
  <0.5 MB, capped by provider quotas). The `setLevel` line drops that
  per-item DEBUG dump and the per-request `Crawled (200)` DEBUG trace
  while keeping every Scrapy `WARNING`/`ERROR`, the end-of-crawl stats
  block, and `huntloop.pipelines`' own per-posting `INFO` lines
  (inserted / reposted / skipped). Verified on a real scoped Gem
  re-scrape: the `Scraped from` blocks disappear entirely, the
  per-posting pipeline `INFO` and a real spider `WARNING` still appear.
  The scoped `scripts/scrape_*.py` entrypoints do NOT carry this line
  (they're for ad hoc proving runs, where the DEBUG echo is useful) —
  only `main.py`, the scheduled path, does. Don't "fix" this by
  touching `settings.py`'s `LOG_ENABLED` or `logging_config.py` — the
  targeted one-liner is deliberate.

- **`detect_ats()` (`src/huntloop/ats_detection.py`) is static-fetch-first,
  render-as-fallback — it renders with Playwright only when the plain HTTP
  fetch matches nothing, never by default.** Rendering costs ~4-8s vs.
  <1-3s for a static hit (measured, see SESSIONS.md 2026-08-21) — an
  order of magnitude slower — so don't change this to render
  unconditionally "to be more thorough"; that trade-off is deliberate and
  documented. `playwright` in `requirements.txt` is a real, used
  dependency now (was previously unused) — the Chromium browser binary
  must be present locally (`playwright install chromium`; not yet wired
  into CI or documented in README.md, since nothing calls `detect_ats()`
  from the app yet). Even with rendering, `detect_ats()` only sees
  whatever URL it's given — it doesn't crawl a site to find the actual
  careers sub-page. `checkr.com/company/careers` (the bare marketing
  landing page) correctly stays `unknown` even after rendering, because
  that specific page never embeds the ATS board itself — it only links to
  `checkr.com/company/careers/open-careers`, which does, and which *does*
  now correctly resolve to Greenhouse via the render fallback. Workday
  tenant slugs (the `{tenant}.wd\d+.myworkdayjobs.com` subdomain) aren't
  derivable from a company name — they were found by manual probing
  during testing, not a lookup this function does or could do. Playwright
  failures (navigation timeout, browser launch failure, page crash) are
  all caught and degrade to `ats="unknown"` with `error` set — never an
  unhandled exception; a `wait_for_load_state("networkidle")` timeout
  specifically is treated as non-fatal (real sites often keep a
  tracking/analytics connection open indefinitely and never go truly
  idle even once their content has rendered).

- **`JobDataPipeline` (`src/huntloop/pipelines.py`) is genuinely
  source-agnostic — confirmed, not assumed, when the Lever spider was
  added (2026-08-21, see SESSIONS.md).** It keys off `item["name"]`
  (the spider's `self.name`, e.g. `"greenhouse_api"`/`"lever_api"`) and
  generic item fields, not anything Greenhouse-specific. The `gh_job_id`
  column name is a cosmetic wart, not a functional coupling — it's a
  plain string column that holds whatever `item["job_id"]` is for any
  source (a Greenhouse integer or a Lever UUID, both fine). Don't rename
  it unprompted; see `job_url` as the actual canonical unique key, above.
  A new spider for a new ATS platform should need zero pipeline changes,
  same as Lever did.
- **The `job_sources` duplication (`'Greenhouse'` vs. `'greenhouse_api'`)
  is root-caused and closed, not just cleaned up again — 2026-08-21, see
  SESSIONS.md.** Phase 0 Step 4 consolidated the duplicate once via a
  data-only migration, but `test_db_insert.py` kept hardcoding the
  literal `"Greenhouse"`, separate from `GreenhouseScraper.name`
  (`"greenhouse_api"`) that the real pipeline actually uses, so a manual
  smoke-test run silently recreated the duplicate. `test_db_insert.py`
  now imports and reuses `GreenhouseScraper.name` directly instead of a
  separate literal — it can't drift from the real convention again by
  construction. The live duplicate was re-consolidated (this time merging
  *into* `"greenhouse_api"`, the opposite direction from Phase 0, since
  that's now the name the fixed code will always produce) via
  `alembic/versions/3620e2fbbd47_consolidate_duplicate_greenhouse_job_.py`.
  A real regression test
  (`tests/test_db_insert_smoke_script.py`) runs the actual smoke-test
  script against pytest's isolated schema and asserts it reuses the
  pipeline's existing source row rather than creating a second one —
  confirmed to fail against the pre-fix code, not just pass against the
  fixed code. If a third spider is ever added, its own `self.name` will
  get its own distinct row the same way Lever's did automatically — no
  further pipeline or smoke-test changes needed.
- **`companies.ats_platform`/`ats_token`/`careers_url` (added 2026-08-21,
  see SESSIONS.md) are nullable and populated by running
  `scripts/detect_and_store_ats.py`** (a hardcoded 9-company curated
  list, `careers_url`-based) **and, since 2026-08-29,
  `scripts/detect_ats_for_sponsors.py`** (see below) - not automatically
  kept fresh, and not every company row has values (e.g. `OpenAI`, from
  `test_db_insert.py`'s smoke test, has all three `NULL`).
  **`scripts/detect_ats_for_sponsors.py` (2026-08-29, see SESSIONS.md)
  expanded `companies` from 9 detected rows to ~380** by taking the real
  universe of distinct `employer_name_normalized` values in
  `lca_disclosures` with >= 20 filings (8,491 employers; the full set is
  108,575 and infeasible to probe), deriving candidate board slugs from
  each name, and probing the Greenhouse
  (`boards-api.greenhouse.io/v1/boards/{slug}` + `/jobs`) and Lever
  (`api.lever.co/v0/postings/{slug}`) public APIs directly - **neither
  vendor publishes any reverse "list our customers" endpoint (confirmed
  against their own API docs)**, so slug-probing is the only option at
  scale. Result: **318 Greenhouse + 60 Lever** hits (4.5% match rate -
  an explicit LOWER BOUND, since a real board slug rarely equals a
  slugified legal name), committed as 371 new `companies` rows + 1
  updated (`brex`: `unknown`->`greenhouse`). `upsert_hits()` fills only
  NULL/`'unknown'` `ats_platform`, never overwrites a different
  successful platform. The matcher went through 3 probe passes to strip
  false positives (generic-fragment slugs like `general`/`us`/`charles`,
  Greenhouse demo tenants `linkedin`/`microsoftcorporation`, dictionary-
  word collisions `flex`/`aura`/`national`) - final gates: board-name
  fuzzy-similarity check, a dictionary-word stoplist + acronym rule,
  >= 3 postings, "test"/"demo" name rejection. Residual FP risk remains
  on generic 3-letter slugs at the low-filing tail (`rpa`, `pmg`,
  `grey`) - accepted as MVP noise. **Re-run
  `scripts/detect_ats_for_sponsors.py` after each new quarterly DOL LCA
  file is ingested** (`scripts/ingest_lca_disclosures.py`), NOT on a
  fixed calendar - a new quarter adds employers and pushes others past
  the 20-filing threshold; safe to re-run (only inserts new / fills
  NULL-or-unknown). `main.py` needs no changes - it already groups
  `companies` by `ats_platform`. No new spiders were built; `ashby`/
  `workday`/`neither` employers are still skipped.
  **White-labeled Greenhouse/Lever on a company's own domain
  investigated 2026-09-02 (see SESSIONS.md "Investigate white-labeled
  Greenhouse/Lever on custom domains" - investigation only, no code
  changed, no DB writes).** Real mechanism (confirmed on Ripple's live
  page, a Next.js app): the company's own frontend calls/embeds the same
  Greenhouse Boards API data server-side and republishes it under its own
  URLs (`gh_jid` query param still present as a fingerprint) - not an
  iframe, not a different backend. **The existing `boards-api.
  greenhouse.io/v1/boards/{slug}/jobs` endpoint is reachable identically
  regardless of the company's own domain once the slug is known** (proven
  on Ripple + Airbnb/Pinterest/Coinbase/Peloton, all white-labeled on
  custom domains, all found by literally guessing the slug) - **so
  white-labeling is purely a discovery problem, `GreenhouseScraper` needs
  no changes.** The real, separate cause Ripple specifically was missed:
  `slug_candidates()` never emits a bare first-word candidate for a
  2-word name whose second word isn't in `_TRAILING_NOISE` ("RIPPLE
  LABS" - "LABS" isn't recognized noise) - a known, deliberate
  conservatism (see the false-positive history in this bullet's own
  paragraph above), not anything white-labeling-specific. Tested against
  the real "neither" population (`scratch_neither_ats_probe.json`'s
  existing 400-employer sample): a plain random 20-employer sample found
  0 genuine hits (mostly staffing/hospitals/universities, as already
  documented); a targeted sample of the 44 "2-word, non-noise-second-word"
  employers in that same 400 found **2 confirmed genuine misses -
  `FAIRE WHOLESALE` -> `faire` and `HIGHNOTE PLATFORM` -> `highnote`**
  (both verified: real matching LCA job titles, not name-collision
  noise) - plus 2 more real false-positive-shaped hits confirming this
  approach isn't free of the same collision risk the existing stoplist/
  similarity gates already guard against. **Recommendation: worth a
  future carefully-gated follow-up (bare-first-word pass, run only for
  names the current generator skips, with the same or stronger
  corroboration gates already used elsewhere), not an urgent rebuild -
  real but modest prevalence, not a rare Ripple-only edge case.**
  **That follow-up is now BUILT, 2026-09-03 (see SESSIONS.md "Add a
  gated bare-first-word candidate for 2-word company names").**
  `scripts/detect_ats_for_sponsors.py` gained `bare_first_word_candidate()`
  - a new, separate function (`slug_candidates()` and the noise-list
  guard are untouched) that emits one bare first-word slug, tried ONLY as
  a fallback after every regular candidate misses, for an exactly-two-word
  name whose second word isn't in `_TRAILING_NOISE`. A hit via this kind
  is NEVER auto-stored (same collision-prone shape as the
  `first-word-only`/`acronym` kinds) - it is held for explicit human
  confirmation via `--confirmations FILE` (one slug per line, gitignored
  `confirmed_bareword_ats_slugs.txt`), mirroring the Ashby/iCIMS/Gem
  onboarding gates. Every `primary`-kind hit still auto-stores exactly as
  before. Full-population run: 88 bare-first-word hits, 42 auto-passed
  (human-confirmed), 46 held; 36 new `companies` rows onboarded via this
  kind (incl. **`faire` -> "Faire" and `highnote` -> "Highnote"**, both
  Greenhouse), scraped by the daily orchestrator with 0 NULL
  `is_relevant`/`embedding`. The confirmations file was built by checking
  each held board's real job titles against the DOL employer name,
  rejecting ~18 short/generic-word collisions (`mercury`, `fetch`,
  `relativity`, `public`, ...). `scripts/scrape_lever.py` added for
  parity with `scripts/scrape_greenhouse.py`. Re-running the full
  discovery is idempotent (`0 inserted, 0 updated`).
  **The 46 held bare-first-word candidates were manually reviewed
  one-by-one 2026-09-03 (see SESSIONS.md "Resolve the 46 held
  bare-first-word Greenhouse/Lever collisions") — no code/gate/discovery
  changes; evidence from each live board (Greenhouse Boards API name +
  jobs, Lever board page org name + postings) vs. the DOL sponsor, with
  DOL worksite/title cross-check. Result: 2 confirmed correct, 0
  still-ambiguous, 44 confirmed wrong (genuine collisions) — an even
  higher collision rate than the iCIMS 39, as the bare-word slug is the
  most collision-prone match kind.** The 2 correct (both Lever) were
  added to `confirmed_bareword_ats_slugs.txt` and stored via the
  UNCHANGED discovery `--commit --confirmations` path, then scraped:
  `bioagilytix` → BioAgilytix Labs (bioanalytical CRO), `finix` → Finix
  Payments (payments infra). The 44 wrong were slug collisions with
  unrelated companies — `mercury`→Mercury the banking fintech (vs
  Mercury Financial the card issuer), `relativity`→Relativity Space (vs
  Relativity ODA the e-discovery co), `pivotal`→Pivotal the eVTOL
  aircraft co (vs Pivotal Software), `clara`→Clara the LatAm cards
  fintech (vs Clara Analytics), `matic`→Matic Insurance (vs Matic
  Robots), `fetch`→Fetch Pet Insurance (vs Fetch Rewards),
  `goodwin`→a "Goodwin" aviation/bookkeeping board (vs Goodwin Procter
  LLP), `oliver`→OLIVER Agency (vs Oliver Wyman), `blue`→BlueCloud
  Services (vs Blue Yonder), `neon`→Neon Pagamentos (vs Neon IT),
  `pursuit`→the Pursuit nonprofit (vs Pursuit Software), `telligen`→the
  Iowa Telligen healthcare org (vs "Telligen Tech Inc." the IT staffer),
  plus many generic-word staffing-firm / preschool / physical-therapy
  collisions (`cornerstone`, `elite`, `excel`, `sunrise`, `wise`,
  `public`, `tia`, `sar`, `spencer`, `athena`, `techno`, `atek`, `lts`,
  `grand`, `source`, `syntax`, `mantra`, `brilliant`, `galaxy`,
  `benjamin`, `octagon`, `accrue`, `solutions`) — none stored. Full
  per-company table in SESSIONS.md.
  **Feasibility measured 2026-08-30 (see SESSIONS.md "Which ATS is most
  common among the 'neither' sponsors") to decide which spider to build
  next — measurement only, no spider, no DB writes.**
  `scripts/probe_neither_ats_platforms.py` probed a seeded random sample
  of 400 of the 8,113 GH/Lever-"neither" >=20-filing employers against
  Ashby/Workday/SmartRecruiters/iCIMS. Result (any-hit, lower bounds):
  **Workday 10.8% · iCIMS 4.5% · SmartRecruiters 4.0% · Ashby 2.2% ·
  undetected 79.8%** (the undetected mass is IT body-shops, hospitals,
  universities, research institutes, gov/school-district employers).
  **Workday is the clear next target.** Tenant detection IS feasible: POST
  `{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/__nosuchsite__/jobs`
  -> **404 = tenant+dc exist, 422 = not** (dc brute-forced over 8 `wd{N}`
  subdomains; site name not needed to confirm). BUT a Workday *spider*
  additionally needs the per-tenant **site segment**, which is NOT
  guessable (`adobe`=`external_experienced`, `salesforce`=
  `External_Career_Site`; a 10-name common list hit 1/6 real tenants) --
  onboarding must resolve each company's real careers URL once to capture
  `{tenant, dc, site}` and `companies.ats_token` must widen to a 3-tuple.
  Once known, the CXS `/jobs` POST paginates cleanly. SR is the cleanest
  public API but ids aren't name-derivable (`Ubisoft`->`Ubisoft2`) and
  only ~4%; Ashby is clean but too rare in this enterprise-skewed set;
  iCIMS has opaque subdomains and mostly no public JSON.
- **iCIMS discovery pass done 2026-09-01 (proof/discovery only — NO
  spider, NO DB writes; see SESSIONS.md "iCIMS discovery pass").
  Recommendation: QUALIFIED GO, but lower priority than SmartRecruiters
  / Ashby were, and a step grayer on ToS/risk than any existing HuntLoop
  integration.** Verified against real live traffic (Chrome network tab
  + honest-UA curl, 3–4s delays, robots respected, no blocking hit):
  - The premise that iCIMS scrapers hit an internal client-side JSON
    endpoint `api.icims.com/customers/{customerId}/search/portals/{portal}`
    is **false in practice.** That documented endpoint is genuinely
    auth-gated (`GET .../customers/6273/search/portals/jobs` no creds →
    **HTTP 401**, `errorCode 6`) and **no career portal calls it** —
    full network inspection of `careers-insmed`/`careers-herbalife`
    shows zero `api.icims.com` / `/api/jobs` / `intelliservices` / JSON
    XHR for job data. `customerId` is not in client-side source at all.
  - **What actually works:** parse the tenant's own public career-portal
    **server-rendered HTML** at
    `careers-{slug}.icims.com/jobs/search?ss=1&in_iframe=1`. Job rows:
    `<a class="iCIMS_Anchor" title="{id} - {Title}"
    href=".../jobs/{id}/{title-slug}/job">`. Pagination:
    `?pr={0-indexed page}&in_iframe=1` (~20–27/page, `<link rel="next">`,
    stop at "Page N of N" — verified paging Persistent Systems to 2
    pages / 29 jobs). Each job detail page carries a schema.org JSON-LD
    `JobPosting` block (clean per-job structured data). The `portal` id
    (17 default; also seen 69/96/82281) is visible in the
    `renderDynamicPortalCss` CSS request but **not needed** to scrape.
  - **Identifier resolution:** only the `careers-{slug}.icims.com`
    subdomain is needed — no customerId, no portalId. Naive
    `careers-{firstword/slug}` derivation resolved ~14/19 test companies
    (~74%); after skipping the ~30% of tenants whose robots.txt is
    `User-agent: * / Disallow: /` (uci, cdmsmith, mastec, sita in the
    sample), ~10/19 are actually crawlable. Realistic reachable coverage
    ≈ 2–3% of the "neither" set (4.5% prevalence − robots − bad-slug
    haircuts).
  - **Risk-profile difference from every prior ATS:** GH/Lever/Ashby/SR/
    Workday-CXS are all JSON endpoints their vendors intend for
    programmatic/public consumption. iCIMS' only documented programmatic
    API is auth-gated; the working approach here is HTML-scraping a page
    built for human browsers, and HTML shape varies across iCIMS
    platform releases (183 vs 187 seen in one 17-company sample) — more
    brittle, grayer. If built: an **HTML-parsing spider** (not an API
    client), robots-gated per tenant (`Disallow: /` → skip the company,
    recorded not guessed around), honest UA + conservative
    `DOWNLOAD_DELAY`, `<link rel="next">` pagination, JSON-LD as the
    per-job source, best-effort/lossy. **Never** use
    `api.icims.com/customers/...` (auth-gated → unauthorized without
    credentials). Onboarding gate mirrors Workday/SR/Ashby: board-name
    cross-check before storing (generic-slug risk, e.g. `careers-aurora`).
- **Gem job-board discovery PROVEN 2026-09-01 (proof/discovery only — no
  spider, no DB writes; see SESSIONS.md "Prove Gem job-board discovery").
  Recommendation: GO — the cleanest integration since Ashby, firmly at
  the Greenhouse/Lever/Ashby end of the risk spectrum, NOT the iCIMS
  end.** Confirmed by real browser network inspection (jobs.gem.com is a
  React SPA): job boards render from a **genuine public GraphQL API**,
  `POST https://jobs.gem.com/api/public/graphql/batch` (JSON array body,
  **no auth / no cookies** — verified by curl replay), where **`boardId`
  == the `jobs.gem.com/{slug}` vanity slug**:
  - list: `oatsExternalJobPostings(boardId:) { jobPostings { id extId
    title locations job{department employmentType} } }` — **every posting
    in one call, NO pagination** (verified fetch=70 / felix=114 exactly
    match the live "Open positions (N)"), **NO description**; plus
    `jobBoardExternal(vanityUrlPath:) { teamDisplayName pageTitle }` for
    the name cross-check.
  - detail (per job): `oatsExternalJobPosting(boardId:, extId:) {
    descriptionHtml firstPublishedTsSec compensationHtml jobPostSectionHtml{...} }`.
  - unknown slug -> HTTP 200 `jobBoardExternal: null`, `jobPostings: []`.
    "resolved" = `jobBoardExternal != null`; empty-but-real boards exist
    (Databricks) same as Ashby/SR.
  `scripts/discover_gem_job_board.py` (name -> ordered name-derived slug
  candidates, **hyphenated forms first** — Gem slugs are often hyphenated
  `the-boring-company`/`black-ore`/`myriad-technology`; `--slug` verifies
  a web-search-found slug; confidence via rapidfuzz name vs.
  `teamDisplayName`). Test set (25): of 19 companies with a live
  jobs.gem.com board, **15 resolved by slug-guess alone, 1 via the
  web-search `--slug` fallback** (Luma AI -> `lumalabs-ai`), 1 unresolved
  (Bohler); Tractian is a Gem-ATS customer but hosts its board at its own
  domain (genuine not-on-jobs.gem.com, not a miss); 7 non-Gem controls
  (Palantir/Checkr/Duolingo/Figma + LCA employers) all correctly
  returned `jobBoardExternal: null` — 0 false positives. No robots.txt
  published (404); no blocking/CAPTCHA/rate-limit hit. **Startup-skewed
  like Ashby — 0 current `companies` rows are Gem users**, so a Gem
  spider pays off only alongside a separate startup-sourcing path. When
  built: one list call + one detail call per job (Ashby/Workday shape),
  onboarding gated on the `teamDisplayName` cross-check + hold 0-job
  boards. `scratch_gem_discovery.json` (gitignored) holds the results.
  **Startup-sourcing groundwork for Gem done 2026-09-02
  (sourcing/verification only — no Gem discovery, no DB writes; see
  SESSIONS.md "Source LCA-verified Gem startup candidates") — a separate
  script and candidate population from the Ashby startup-sourcing pass,
  reusing the exact same method.** `scripts/discover_gem_startup_sponsors.py`
  checked 41 real web-search-sourced Gem-candidate names (the 13 already
  confirmed live plus 28 new ones, e.g. Retool, Jetty, Luma AI, Nuvo,
  Bohler, Paces, Letter AI, Scale AI) against the full `lca_disclosures`
  table; every fuzzy hit was individually spot-checked against real
  `job_title`/`worksite` rows, not accepted on score alone — 7 of 21 raw
  hits were confirmed false positives (short/generic-name collisions:
  Planned/Gem/Rivia/Agora/Constellation Institute/HASH/Veho Technologies
  all matched an unrelated real company). **2 real Sierra-pattern finds**,
  each fixed with a new `sponsor_name_overrides` row (table now has 4:
  kraken/sierra/modular/ntop) — `ntop` → `NTOPOLOGY` (real company,
  since rebranded; `token_set_ratio` 61.5, below threshold, found via a
  broader `ILIKE` scan) and `modular` → `MODULAR` (the exact-name match
  existed but the matcher's top pick was a wrong same-scoring tie,
  "ADVANCED MODULAR SYSTEMS" — a ranking artifact, not a threshold miss).
  **Final: 14/41 candidates carry real, spot-checked LCA sponsorship
  evidence** (Scale AI 144 filings, Retool 30, Felix Technologies 19,
  Modular 18, ntop 15, Luma AI 15, Linktree 7, Apartment List 6,
  Instrumental 5, Jetty 5, Nuvo 3, Paces 1, Bohler 1, Letter AI 1); 1
  (Function Health) flagged `needs_review`; 19 had no LCA match at all
  after the broader search, reported honestly rather than dropped.
  Nothing stored in `companies` — this is the input to a future Gem
  discovery-then-onboard pass, not yet run. `companies` row count and
  every `ats_platform` count confirmed unchanged, and the Ashby sourcing
  script/output verified byte-for-byte untouched (same MD5).
  `scratch_gem_startup_sponsor_candidates.json` (gitignored) holds the
  full results.
  **Gem spider BUILT + onboarded + first scrape 2026-09-02 (see
  SESSIONS.md "Build the Gem spider + gated onboarding + first
  scrape").** `GemScraper` (`src/huntloop/spiders/gem_spider.py`, name
  `gem_api`) confirmed during implementation that
  `POST https://jobs.gem.com/api/public/graphql/batch` is a real GraphQL
  *batch* endpoint — multiple operations posted together in one JSON
  array all execute and return in one HTTP round trip, in request order
  (proven live: a real 109-op array — 1 list + 108 details for a
  108-job board — one 200, all 108 resolved). So each company needs only
  TWO real requests regardless of job count: one `JobBoardList` call (the
  list response already carries title/locations/department/
  employmentType) then one batched `ExternalJobPosting` detail call
  covering every job at once (chunked at `MAX_DETAIL_BATCH=100` as a
  safety valve only). An unknown slug, empty board, malformed response,
  or per-job missing detail is logged + counted via `scrape_errors_total`
  and skipped, never a crash. `tests/test_gem_spider.py` (12 tests).
  Yields plain `JobPostingItem`s through the shared `JobDataPipeline`;
  `is_relevant` + `embedding` computed at insert like every other source,
  zero manual backfill. `companies.name` == `ats_token` == the
  lower-cased jobs.gem.com slug. Added to `main.py`'s
  `SPIDERS_BY_PLATFORM` (plain `companies=tokens` branch);
  `scripts/scrape_gem.py` is the scoped entrypoint.
  **Onboarding gate: `scripts/discover_and_store_gem.py`** reuses
  `discover_gem_job_board.py` unchanged over the 14 Gem-specific
  LCA-verified candidates (a separate list from Ashby's, sourced
  2026-09-02 — see above). Auto-store requires Gem confidence `high`;
  a real-but-empty board or a weaker match is held for a
  `--confirmations` file. **Real run: 9/14 resolved automatically by
  slug-guess; 3 more (Bohler, Instrumental, Luma AI) needed a
  human-supplied slug via `confirmed_gem_slugs.txt`** (a genuine trailing
  hyphen for the first two, a distinct marketing-brand slug for the
  third — each verified live before confirming, same
  `Name<TAB>slug`-forced-candidate mechanism Ashby used for
  Anysphere/`cursor`). **2 stayed correctly unresolved**: Jetty has a
  real `jobs.gem.com/jetty-careers` board, but it belongs to a different
  company ("Jetty Health", not the LCA-verified renters-insurance Jetty)
  — flagged `low-suspect` and deliberately not force-confirmed, since
  onboarding the wrong same-named company would be a real data-integrity
  error, not a discovery gap; Scale AI has no live `jobs.gem.com` board
  under any plausible slug (its real Gem relationship is the internal
  CRM/sourcing product, not necessarily the public job-board product).
  12 companies stored, then scraped via `docker compose run ... app
  python scripts/scrape_gem.py`: **495 new `job_postings` rows, 0 NULL
  `is_relevant`, 0 NULL `embedding`, no backfill** (bohler- 210, felix
  108, lumalabs-ai 50, retool 24, linktree 24, apartment-list 18, paces
  16, instrumental-inc- 12, modular 11, nuvo 11, ntop 6, letter-ai 5).
  One gracefully-handled duplicate-key event (Bohler's own list response
  genuinely lists one posting id twice — a Gem-side data quirk, caught
  by the pipeline's existing `job_url` unique-constraint dedup, not a
  spider bug). Live cross-checked (fresh calls, not reused from
  discovery): Modular 11/11, Retool 24/24, Nuvo 11/11 against the real
  API. **Noticed, not introduced, while verifying: `job_postings.
  department` is NULL for every row from every source project-wide**
  (`JobDataPipeline.process_item()` never assigns `item["department"]`
  to the row for any spider, and `employment_type` isn't even a column)
  — confirmed across all 7 sources before concluding this is pre-existing
  and out of this task's scope, not a Gem-specific defect.
  **Investigated and fixed 2026-09-02 (see SESSIONS.md "Investigate and
  fix job_postings.department NULL across all 7 sources") — the
  `employment_type` gap noted above is untouched/still open, department
  only.** Root cause confirmed against real live raw responses from every
  source: `JobDataPipeline.process_item()`'s `JobPosting(...)` insert
  simply never passed `department=item.get("department")` through, even
  though `LeverScraper`/`SmartRecruitersScraper`/`AshbyScraper`/
  `IcimsScraper`/`GemScraper` were already extracting a real value from
  real raw fields (Lever `categories.department`, SmartRecruiters
  `department.label`, Ashby `department`/`team`, iCIMS JSON-LD
  `occupationalCategory`, Gem `job.department.name`) — fixed by adding
  that one field to the insert; none of those 5 spiders needed changes.
  **Greenhouse had a second, independent bug**: its real department field
  is the structured `job["departments"][0]["name"]` (confirmed live on 5
  companies), not anything in the free-form `metadata` array the old code
  scanned for a "department"-named entry — that scan never matched real
  data (real metadata entries are things like "Career Site Category",
  "Job Family Group"). `greenhouse_spider.py` now reads `departments`
  first, keeping the metadata scan only as a fallback.
  **Workday is a genuine data-source limitation, not a bug** — its per-job
  CXS detail response has no department/job-family field (confirmed live;
  the list endpoint's `jobFamilyGroup` is an aggregate board-filter facet,
  not a per-posting value) — `department` stays `None` there by design,
  unchanged.
  **Backfill**: only Lever was feasible without a re-scrape — its stored
  `job_metadata.metadata_json` already contains the raw `categories` dict
  (double-JSON-encoded — decode twice). `scripts/backfill_department_lever.py`
  filled **3,299 of 3,715** `lever_api` rows from that stored data (the
  other 416 genuinely had no department in their original raw categories,
  left NULL). Greenhouse/SmartRecruiters/Ashby/iCIMS/Gem's stored
  `metadata_json` does not carry the raw department value (checked
  directly, not assumed) — backfilling those needs a fresh re-scrape,
  not performed in this task. Verified: `job_postings` total row count
  and `is_relevant`/`embedding` populated-counts unchanged before/after
  (83,720 each); only `department` counts changed, and only for
  `lever_api`. `tests/test_pipeline.py`/`tests/test_greenhouse_spider.py`
  (new) cover the fix; full suite 219/219 passing.
  **The other 5 sources' existing rows were backfilled 2026-09-02 (see
  SESSIONS.md "Backfill job_postings.department on existing rows via
  re-scrape") via a fresh re-scrape of every onboarded company.** First
  fixed a real gap this required: `JobDataPipeline.process_item()`'s
  repost-match path (matched by `gh_job_id`) previously just logged
  "Skipping reposted job" and touched nothing — now, on a repost, it
  fills `department` if the existing row's is `NULL` and the new item has
  a real value, and nothing else; every other column (`is_relevant`,
  `embedding`, ...) is still left completely alone on a repost, by
  design (narrow, additive fix only — see `tests/test_pipeline.py`'s
  `test_repost_backfills_null_department_only` /
  `test_repost_does_not_overwrite_existing_department`). Per-source
  before → after `department` NULL counts (all 5 started 100% NULL):
  Gem 495/495 → 2/496, Ashby 3336/3336 → 66/3401,
  iCIMS 4080/4080 → 478/5303, Greenhouse 27419/27419 → 2141/28588,
  SmartRecruiters 19165/19165 → 9926/20333. **SmartRecruiters needed a
  different mechanism, not the real spider re-scrape**: its own list
  endpoint already returns `department` inline per posting, so the
  per-job detail fetch `SmartRecruitersScraper` normally makes (needed
  only for a brand-new posting's description) was pure overhead for this
  backfill — a partial real re-scrape was on pace to take many more hours
  after 2 hours/27 companies, so it was stopped and replaced with
  `scripts/backfill_department_smartrecruiters.py` (list-only pagination,
  no detail requests, no torch dependency — runs in the local `.venv`),
  which finished the remaining 224 companies in ~23 minutes. `scripts/
  scrape_greenhouse.py` (new — the other 4 sources already had a scoped
  entrypoint) was added for parity. Whole-table verification after all 5:
  `job_postings` 83,720 → 87,346 (+3,626 genuinely new/reappeared
  postings, not duplicates — `count(*) == count(distinct job_url) ==
  count(distinct gh_job_id) == 87,346`); `is_relevant`/`embedding` both
  still 0 NULL across every row. Final whole-table `department` NULL
  rate: 38,539/87,346 (44.1%), almost entirely `workday_api`
  (25,510/25,510, 100% by design, untouched — see above); the remaining
  per-source NULLs above are real API/HTML gaps (some boards, e.g.
  SmartRecruiters' `deltaelectronics`, simply don't provide `department`
  for any of their postings) or closed/expired postings, not a fix
  failure. Full suite 221/221 passing (219 + 2 new pipeline tests). Not
  touched: Workday, Lever, `companies`/onboarding/discovery scripts.
- **`job_postings.employment_type` added end-to-end 2026-09-05 (see
  SESSIONS.md "Add employment_type support end-to-end") — same bug
  class as the department NULL issue above, plus a column that never
  existed in `db_models.py`/Alembic.** Investigation confirmed against
  real live data (raw API responses + DB `job_metadata` for all 7
  sources) that every spider was ALREADY extracting a raw
  employment-type signal into `item["employment_type"]` where its source
  exposes one — `JobDataPipeline.process_item()` just never persisted
  it. Per source, what's actually there:
  - **Workday** `jobPostingInfo.timeType` — clean, `"Full time"`/`"Part
    time"`/`""` only.
  - **Ashby** `employmentType` — clean enum (`FullTime`/`PartTime`/
    `Contract`/`Temporary`/`Intern`).
  - **Gem** `job.employmentType` — clean `SCREAMING_SNAKE_CASE` enum
    (`FULL_TIME`…), and it's on the list response, so no per-job detail
    fetch needed to read it.
  - **iCIMS** JSON-LD `employmentType` — schema.org enum, real per-tenant
    variation (`FULL_TIME` vs `OTHER` seen across tenants; `OTHER` is a
    valid value the tenant configured, not a gap).
  - **SmartRecruiters** `typeOfEmployment.label` — mostly clean, but
    company-selectable; present on the list endpoint too (not just
    detail).
  - **Lever** `categories.commitment` and **Greenhouse** a free-form
    `"Employment Type"`/`"LEGACY - Employment Type"` entry in the
    per-company `metadata` array — both genuinely messy per-company free
    text (`"Regular"`, `"Fulltime Employee"`, `"Full-Time: Experienced"`,
    `"正社員"`, `"Modified Full-Time"`, …), and Greenhouse only has it at
    all when a company configured that custom field (~20% do).
  **Normalization**: `huntloop.employment_type.normalize_employment_type()`
  maps any raw label to one of `Full-time`/`Part-time`/`Contract`/
  `Internship`/`Other`, or `None`. Done centrally in the pipeline (item
  carries the raw label, pipeline normalizes at insert), NOT per-spider.
  Key rule, per the task: a real-but-unrecognized/ambiguous label
  (`"Regular"`, `"Employee"`, iCIMS `"OTHER"`, non-English) normalizes
  to `"Other"` — NEVER guessed into a specific bucket; `None`/`""` (the
  source gave no signal at all) stays NULL. Regex is word-boundary-based
  (so `"International"` ≠ internship) with a `(?![a-zA-Z])` trailing
  guard (real Greenhouse data appends `_exempt`/`_non-exempt` with an
  underscore, which a bare `\b` misses); Contract/Internship signals
  beat Full-time/Part-time when a label names both (`"Temporary
  Full-Time"` → Contract). `tests/test_employment_type.py` (6 tests) +
  4 new `tests/test_pipeline.py` tests.
  **Migration** `f3a7c9d21b44` — during which a real pre-existing schema
  drift was found: this dev machine's `job_postings` ALREADY had an
  untracked `employment_type VARCHAR(100)` column (a pre-Alembic
  `create_all()` leftover, always empty — 0/96,409 rows, no code ever
  wrote to it), so the migration is guarded (`inspect()` — narrows the
  existing column to `VARCHAR(50)` if present, adds it fresh on a clean
  DB like CI) exactly the way `37f5b1de06fe` guarded its drifted
  `location` column.
  **API**: `GET /jobs?employment_type=…` + `GET /jobs/employment-types`
  (see the FastAPI bullet above). **Frontend**: `JobFilters.tsx`
  employment-type `<select>` (see the `JobFilters.tsx` bullet above).
  **Backfill** — 5 one-off scripts under `scripts/backfill_employment_type_*`,
  mechanism per source's real data shape:
  - `…_from_metadata.py` — Greenhouse/Lever/Workday, purely from
    already-stored `job_metadata.metadata_json` (double-decode), ZERO
    network. Greenhouse's full raw `metadata` array, Lever's whole
    `categories` dict, and Workday's `timeType` are all already stored —
    unlike the department backfill, where only Lever was.
  - `…_smartrecruiters.py` — list-only re-fetch (label is inline on the
    list endpoint), mirrors `backfill_department_smartrecruiters.py`.
  - `…_ashby.py` / `…_gem.py` — one API/GraphQL-list call per company.
  - `…_icims.py` — the one source needing a per-job re-fetch (JSON-LD is
    only on each detail page); re-fetches each existing row's stored
    `job_url` directly rather than re-walking listing pages, tenants run
    concurrently (different hosts) with per-host politeness + robots
    re-check.
  **Real before → after `employment_type` NULL, live query** (before:
  0 populated across all 7 sources — the column existed but nothing ever
  wrote it):
  - workday_api: 0 → 28,403 / 28,773 filled (370 NULL = genuinely-empty
    `timeType`)
  - lever_api: 0 → 3,923 / 4,056 (96.7%)
  - smartrecruiters_api: 0 → 18,401 / 21,322 (86.3%)
  - ashby_api: 0 → 3,357 / 3,515 (95.5%)
  - icims_portal: 0 → 5,726 / 6,374 (89.8%; the 648 NULL are mostly
    HTTP 410 expired postings + a few with no JSON-LD `employmentType`)
  - gem_api: 0 → 498 / 521 (95.6%)
  - greenhouse_api: 0 → 6,326 / 32,319 (19.6% — most Greenhouse
    companies never configure an "Employment Type" custom field; this is
    a true source-data ceiling, not a backfill miss). **Widened
    2026-09-05 (see SESSIONS.md "Widen the Greenhouse employment_type
    backfill to adjacent metadata fields") — 19.5% → 27.6% for
    Greenhouse (+2,613 rows, +8.0pp).**
    `huntloop.employment_type.greenhouse_employment_type()` (new shared
    helper, used by both `greenhouse_spider.py` and
    `backfill_employment_type_from_metadata.py`) now checks a curated set
    of adjacent metadata field names when the literal "Employment Type"
    field is absent: **`Time Type` / `Full-time/ Part-time` /
    `Full-Time/Part-Time Status` / `Employment Status` / `Work Type` /
    `WORKER_CATEGORY`** — folded in because their real live values are
    genuinely Full/Part-time/Contract/Internship. **Investigated and
    REJECTED: `Worker Type`** (real values `Employee` 414 / `Contractor`
    3 — a legal worker classification, not hours/duration),
    **`Pay Rate Type`** (`Salary` 157 / `Hourly` 1 — compensation
    basis), **`Employee Type`** (`Regular` 534 — a job-category value),
    **`Job Type`** (`Standard`/`Pipeline`/`Regular` — job category; also
    `(PT)` in role names false-triggers Part-time). The literal
    "Employment Type" field keeps full normalization (unrecognized →
    "Other"); the adjacent fallback fields contribute a value ONLY when
    it resolves to a specific bucket — an adjacent field normalizing to
    "Other" is treated as "not really an employment-type field here" and
    ignored (row stays NULL), never stored. Backfill fills only
    currently-NULL rows, never overwrites a value from the real
    "Employment Type" field.
  - **whole table: 0 → 66,634 / 96,910 (68.8%)**. The scheduled daily
    scrape fired mid-backfill (2026-09-05) and its ~480 new rows all got
    `employment_type` populated at insert via the fixed pipeline, with
    no manual step — the real end-to-end confirmation. Distinct stored
    values: Full-time 53,438 · Other 5,580 · Part-time 4,501 · Contract
    2,510 · Internship 605.
- **`job_postings.department_category` added end-to-end 2026-09-07 (see
  SESSIONS.md "Canonical department categorization") — an ADDITIVE
  canonical-category layer over the raw free-text `department`; the raw
  value is kept unchanged.** Investigation of the real live data: 4,821
  distinct non-NULL `department` values across 55,621 postings (43.8%
  of rows NULL, ~all `workday_api` by source design; 0 empty-string) —
  far cleaner than `location` (~15.6k distinct) but still messy (clean
  heads, company-specific tails with requisition codes, non-English
  labels, industry-vertical labels naming no function). **Taxonomy: 18
  canonical categories + "Other"** (Engineering, Data & Analytics,
  Product, Design, IT, Sales, Marketing, Customer Support, Operations,
  Finance & Accounting, Legal & Compliance, People & HR, Healthcare &
  Clinical, Research & Science, Manufacturing & Production, Construction
  & Skilled Trades, Consulting & Professional Services, Executive &
  General Management, Other) — deliberately broader than a generic
  tech-company list because the ~700-employer set spans hospitals,
  universities, manufacturers, and construction consultancies. "Other"
  = a real department string naming no function we categorize; NOT the
  same as NULL (no raw department at all).
  `huntloop.department_categorization`: `rule_based_category(raw)` is
  pure/deterministic (ordered keyword regex, first-match-wins,
  specific-before-broad — "Sales Engineer" → Sales, "Data Engineering"
  → Data & Analytics); `categorize_values()` runs rules first then an
  LLM pass over the residual distinct values reusing the
  **skills-matching provider chain** (Groq gpt-oss-120b → gpt-oss-20b →
  Gemini). Cost is bounded by the ~4.8k DISTINCT values, not the ~99k
  rows. A value the LLM saw but couldn't place → "Other"; a value no
  provider could answer (daily quota spent) is left NULL for a later
  run — same drain-over-days model as skills-matching. Migration
  `a1b2c3d4e5f6` (nullable `VARCHAR(50)`).
  **Auto-computed at insert** by `JobDataPipeline` (rule-based only on
  the hot path — no network), and on a repost that backfills a
  previously-NULL raw department; same pattern as
  `is_relevant`/`embedding`/`employment_type`.
  `scripts/backfill_department_category.py` (rule + LLM, one
  VALUES-joined UPDATE per 500-value chunk) is wired as **stage 3/3 of
  `scripts/run_orchestrator_cron.sh`** (runs via `.venv`, HTTP only, no
  torch), so the LLM tail keeps draining and new data never silently
  regresses to permanently-NULL.
  **API:** `GET /jobs/departments` now returns the canonical CATEGORIES
  present in the data (alphabetical as of 2026-09-07 — see the
  department/location-filter entry above; was most-common-first), not
  the ~4,800 raw strings;
  `GET /jobs?department=` filters on `department_category`
  (`__unspecified__` = no category); `GET /jobs` / `GET /jobs/{id}`
  expose both `department` (raw, for transparency) and
  `department_category`. **Frontend:** the department `<select>` shows
  the canonical categories; the job detail sidebar shows the category
  with the raw string beneath it when they differ.
  **Real before → after (live query):** `department_category` 0 →
  45,628 / 55,621 non-NULL-department postings (**82.0%**) — rules
  wrote 43,726, the LLM pass ~1,900 more distinct-value mappings before
  Groq's and Gemini's free-tier daily quotas (already spent by the
  day's scheduled skills-matching run) were exhausted; the remaining
  ~10,100 postings drain via stage 3 over subsequent daily runs. All 18
  categories populated; Engineering (9,779) and Sales (7,081) lead,
  Healthcare & Clinical (3,829) and Construction & Skilled Trades
  (3,505) large due to the hospital/consultancy employers.
- **iCIMS spider BUILT + onboarded + first scrape 2026-09-01 (see
  SESSIONS.md "Build the iCIMS spider + gated onboarding + first
  scrape"). Recommendation was GO — but a step grayer on ToS/risk than
  any other spider here, since it HTML-scrapes a human-facing page rather
  than a vendor-published feed.** `IcimsScraper`
  (`src/huntloop/spiders/icims_spider.py`, name `icims_portal`) parses
  each tenant's server-rendered portal HTML at
  `careers-{slug}.icims.com/jobs/search?pr={0-indexed page}&in_iframe=1`
  then one detail page per job for the schema.org JSON-LD `JobPosting`.
  Pure parsing lives in `huntloop.icims_portal` (unit-tested,
  `tests/test_icims_portal.py` + `tests/test_icims_spider.py`, +21
  tests). **Honest non-browser UA, `DOWNLOAD_DELAY=2`, one request/host,
  AutoThrottle, `ROBOTSTXT_OBEY` left ON. The spider fetches each
  tenant's robots.txt FIRST (following redirects to the authoritative
  host) and skips the whole tenant if it disallows `/jobs/search` — a
  disallow is NEVER bypassed.** JSON-LD is the primary field source;
  visible-HTML is a per-field fallback, logged + counted, run summary in
  `closed()`. `MAX_PAGES=60` cap. Yields plain `JobPostingItem`s through
  the shared `JobDataPipeline` — `is_relevant` + `embedding` at insert,
  zero iCIMS-specific wiring, zero manual backfill (run via the `app`
  Docker image for torch, same as every embedding-dependent path).
  Added to `main.py`'s `SPIDERS_BY_PLATFORM` (plain `companies=tokens`
  branch); `scripts/scrape_icims.py` is the scoped entrypoint. **NEVER
  use `api.icims.com/customers/...`** — auth-gated, unauthorized without
  credentials.
  **Onboarding gate: `scripts/discover_and_store_icims.py`** (reuses
  `discover_icims_job_board.py` unchanged) over the >= 20-filing DOL
  sponsors not already in `companies`/GH-Lever. `auto` = high confidence
  via a strong candidate + robots-permitted + >= 1 live job; `held` (→
  `--confirmations` file) = weak candidate / medium / low-suspect;
  **`robots.txt Disallow: /` is a HARD exclude — never stored, not even
  via `--confirmations`.** Re-run cadence: after each quarterly DOL LCA
  ingest.
  **Real run (`--limit 2000` → 1,978 sponsors): 94 resolved to a live
  portal; 16 auto-passed (13 distinct slugs); 7 held-then-confirmed via a
  live board `<title>`-org + sample-title cross-check (devereux, ohsu,
  geosyntec, usu, healthedge, bronxcare, sas — `sas` = SAS Institute,
  Samsung Austin / SG Americas correctly stay held for that slug); 70
  hard-excluded for `Disallow: /` (Uber, DocuSign, Emory, Harvard,
  Indeed, ASU, + wrong-slug collisions); 39 still held (generic-slug
  collisions `aa`/`boston`/`nyu`/`quest` — left for a human).** 20
  `companies` rows stored, then scraped: **4,080 `job_postings` rows, 0
  NULL `is_relevant`, 0 NULL `embedding`, no backfill; `companies`
  668→688, `job_postings` 79,145→83,225.** JSON-LD was the primary
  source for 100% of jobs (whole-job HTML fallback: 0); per-field HTML
  fallback fired only for `locations` (~9.6%, JSON-LD `jobLocation`
  absent on some remote roles). **`primehealthcare` deliberately cut
  short at 1,960 of ~3,000 rows** (a hospital group, mostly clinical
  roles that all filter `is_relevant=false`; SIGTERM → graceful Scrapy
  shutdown, pending items committed) — the other 19 boards ran to
  completion. Live-verified 5 postings against their real pages.
  Follow-ups: a fuller onboarding
  sweep past the top-1,978 employers; re-scrape primehealthcare to
  completion only if its clinical roles ever matter.
  **The 39 still-held generic-slug boards were manually reviewed
  one-by-one 2026-09-03 (see SESSIONS.md "Resolve the 39 held iCIMS
  generic-slug collisions") — no code/gate/discovery changes, evidence
  from each live `careers-{slug}.icims.com` board (its own `<title>` org
  name, real posted job titles/locations, JSON-LD `hiringOrganization`)
  vs. the DOL sponsor, with web search + DOL worksite/title cross-check
  where the slug was genuinely ambiguous. Result: 4 confirmed correct,
  1 still-ambiguous, 34 confirmed wrong (genuine collisions).** The 4
  correct were added to `confirmed_icims_slugs.txt` and stored via the
  UNCHANGED `discover_and_store_icims.py --from-report --commit
  --confirmations` path, then scraped: `lw` → Latham & Watkins LLP,
  `here` → HERE North America, `eastwestbank` → East West Bank, `nyu` →
  New York University (639 postings, 0 NULL `is_relevant`/`embedding`,
  no backfill; JSON-LD primary for all). Still-ambiguous: **NYU
  GROSSMAN SCHOOL OF MEDICINE** — shares the `nyu` board but Grossman /
  NYU Langone Health run their own sponsored-role hiring at
  jobs.nyulangone.org, so the board can't be confirmed to carry this
  filer's postings (same university family, not a clean collision). The
  34 wrong were slug collisions with unrelated companies —
  `aa`→Envoy Air, `boston`→City of Boston, `quest`→Quest Software,
  `sas`→SAS Institute (for Samsung Austin / SG Americas), `mmc`→M.C.
  Dean, `sri`→SRI International (for Scripps Research / SRI Tech),
  `aurora`→Aurora Staffing, `adventisthealth`→a single AdventHealth
  regional JV, `vanguard`→Deerfield Management, `ccf`→Community Choice
  Financial, `citynational`→City National Bank of Florida (the DOL
  filer is the RBC/Los Angeles bank), `up`→The Michaels Organization,
  `reliable`→Sun Auto Tire, `ars`→American Residential Services,
  `bbd`→New York Blood Center, `pst`→Planned Systems International,
  `mdi`→Alex Lee, `usaa`→Affinius Capital, `express`→Express the
  apparel retailer (not Express Scripts), `bridgewater`→a nursing
  facility (not the hedge fund), `horizon`→Springs Window Fashions —
  none stored. `companies` 737→741; full per-company table in
  SESSIONS.md.
  **Discovery mechanism proven end-to-end 2026-08-30 on 5 real
  unambiguous tenants (see SESSIONS.md "Prove Workday {tenant, dc, site}
  discovery"), then the spider was BUILT 2026-08-30 (see the Workday
  spider bullet below and SESSIONS.md "Build the Workday spider").**
  `scripts/discover_workday_triple.py <tenant>` (one tenant per run, proof
  tool, not a batch runner): name-slug -> dc via the 404/422 probe over 8
  `wd{N}` -> site via robots.txt (authoritative) then an 18-name curated
  `_SITE_CANDIDATES` fallback list (CXS
  200/404, case/separator-insensitive for standard names). Resolved 5/5
  fully automatically incl. Salesforce's custom `External_Career_Site`,
  **no web-search fallback needed**. Real triples:
  `nxp/wd3/careers`, `regeneron/wd1/careers`, `organon/wd5/searchjobs`,
  `cdw/wd5/careers`, `salesforce/wd12/External_Career_Site` — each
  returned real offset-paginated `jobPostings` (0 page overlap); NXP
  cross-checked against the live rendered board (763 jobs, identical first
  posting). **No existing careers-URL source for the neither-set** —
  `companies.careers_url` is populated for only 9/380 rows and
  `lca_disclosures` has no URL column — but the proof shows the triple is
  discoverable from the tenant slug alone, so a careers URL isn't
  required. Residual manual work (~10-20%): disambiguating generic-slug
  tenants (the `red`/`western` problem) and the rare tenant whose site
  name isn't in the list (needs a `site:myworkdayjobs.com` search).
  Storage decision deferred to the spider step: `companies.ats_token` is
  a bare string today and Workday needs `{tenant, dc, site}` (overload it,
  add columns, or parse the full `careers_url` like `adobe`'s row).
  **Verified with a
  real `docker compose run app python main.py`: `job_postings` 649 ->
  30,363 (+29,714), every new row's `is_relevant` populated at insert
  time (0 NULL at 30k scale).** One pre-existing bug surfaced at this
  scale (not fixed - 0.12% of rows, gracefully handled): a long
  semicolon-joined multi-location string overflows
  `job_locations.location_name` `varchar(255)`; the pipeline catches
  the `DataError` and skips that item. Company rows use the same lowercase-token naming convention as
  spider-created rows (`"checkr"`, not `"Checkr"`) specifically to avoid
  repeating the `job_sources` naming-drift bug above for `companies`.
  **`upsert_company_ats()` will not overwrite an existing, previously-
  successful `ats_platform`/`ats_token` value when the current
  `detect_ats()` result has `error` set** (2026-08-21 follow-up fix, see
  SESSIONS.md - a real transient `ReadTimeout` was observed silently
  blanking a correct `checkr` detection back to `unknown`/`NULL` before
  this) - it logs a `"detection attempt failed"` warning and leaves the
  stored value alone instead. A genuine error-free "checked, nothing
  matched" result still overwrites to `"unknown"` normally; a company
  with no prior successful value still stores `unknown`/`NULL` on error,
  since there's nothing to protect. Don't revert this to unconditional
  overwrite - `tests/test_detect_and_store_ats.py` would catch it (proven
  to fail against the old behavior).
- **`GreenhouseScraper`/`LeverScraper` both accept a `companies`
  constructor/spider argument, consistently (2026-08-21, see
  SESSIONS.md).** `__init__(self, companies=None, *args, **kwargs)` on
  both: `None` falls back to the original single-company default
  (`['checkr']`/`['wealthfront']`) for backward compat; a string is
  split on commas (the shape `-a companies=...` would arrive as, if
  `scrapy crawl` were ever wired up); a list/tuple is used directly (what
  `main.py`'s orchestrator passes). Keep any future spider's constructor
  consistent with this shape rather than inventing a different one.
- **`main.py` is the multi-ATS orchestrator, not just "the Greenhouse
  entrypoint" anymore (2026-08-21, see SESSIONS.md).**
  `get_companies_by_platform()` queries `companies` for non-NULL
  `ats_platform` rows (grouped by platform) plus a separate NULL-platform
  query (logged, not silently excluded). `run_multi_ats_scrape()` routes
  each platform through `SPIDERS_BY_PLATFORM` (`{"greenhouse":
  GreenhouseScraper, "lever": LeverScraper, "workday": WorkdayScraper}`) -
  one `process.crawl()` call per platform with its full token list, not
  one call per company. A platform missing from that dict (`"ashby"` and
  the literal string `"unknown"` hit the same lookup-miss path - no
  special-casing needed) gets a `logger.warning()` and is skipped, never
  a crash or a silent drop. When adding a new spider, add its platform
  string as a key here - that's the only wiring required (Workday needed
  one extra branch, below, because it also passes `careers_urls`).
- **Workday spider (`WorkdayScraper`, `src/huntloop/spiders/workday_spider.py`,
  name `workday_api`, added 2026-08-30 - see SESSIONS.md "Build the
  Workday spider").** Scrapes a company's Workday board via its public
  CXS JSON API (the same endpoint the rendered board's own JS calls):
  `POST {tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`
  (offset pagination, `{"limit":20,"offset":N,"appliedFacets":{}}`, stop
  when `offset >= total`; `total` only reliable on the offset-0 response
  so all pages are fanned out from there) then one
  `GET {cxs}{externalPath}` **per job** for the description + absolute
  date. **Storage: the 3-part `{tenant, dc, site}` identifier lives in
  the existing `companies.careers_url`** as the full
  `https://{tenant}.{dc}.myworkdayjobs.com/en-US/{site}` URL (exactly the
  format `adobe`'s pre-existing row already used); `ats_token` stays the
  bare tenant slug like every other platform. `huntloop.workday_url`
  (`parse_workday_careers_url` / `build_cxs_base` / `normalize_workday_date`)
  holds the pure, unit-tested parsing. No migration - `careers_url`
  already existed and is semantically exactly right; a bare-string
  `ats_token` or new sparse columns would both be worse. `main.py`
  branches for `platform == "workday"` to also pass
  `careers_urls={name: careers_url}` to the spider; a company routed
  there with no `careers_url` (the ambiguous cases below) is skipped with
  a warning, never guessed. **`date_posted`: the CXS list `postedOn` is
  RELATIVE TEXT ("Posted 3 Days Ago" / "Posted Today" / "Posted 30+ Days
  Ago" - verified against ~1,400 real postings, no hours/weeks/months
  variants). The per-job detail endpoint's `jobPostingInfo.startDate` is
  an absolute `YYYY-MM-DD` and is used as the source of truth (~100% of
  real rows); `normalize_workday_date()` parses the relative text only as
  a fallback, flooring "30+" to 30 days.** Relevance classification AND
  the resume-match embedding are both automatic - the spider yields
  plain `JobPostingItem`s through the same source-agnostic
  `JobDataPipeline`, whose `_classify_and_embed` populates
  `is_relevant` + `embedding` on every insert (needs torch -> run via
  the `app` Docker image, same as the daily scraper). The initial
  23,588-row Workday scrape predated the embedding wiring and needed a
  one-off `backfill_embeddings.py` pass (2026-08-31, see SESSIONS.md);
  future Workday scrapes don't.
- **Workday discovery: `scripts/discover_and_store_workday.py`** turns
  the confirmed-Workday hits from `scratch_neither_ats_probe.json` into
  `companies` rows. Per hit: parse `{slug, dc}`, then resolve `site` via
  **the tenant's `robots.txt`** (it lists every published board as
  `Allow: /{Site}/` + `Sitemap: .../{Site}/siteMap.xml` - authoritative,
  guess-free; `scripts/discover_workday_triple.py`'s `site_from_robots()`,
  picking the non-secondary board with the most postings), falling back
  to an 18-name candidate list only when robots is blocked/empty.
  Ambiguity handling (task: "flag, don't guess"): a hit on a generic
  one-word slug (`red`, `western`, `tera`, ...) is **not stored** - it
  goes to the report's `needs_review` list, since a Workday tenant by
  that name existing doesn't prove it's this employer's. The board's
  `hiringOrganization` legal name is an informational cross-check only,
  never a gate (too noisy: `"621 Salesforce.com India Private Limited"`
  vs `SALESFORCE`). `scripts/scrape_workday.py [names...]` is a
  Workday-only entrypoint (reads `companies`, same pipeline) for proving
  / re-running without a full `main.py` crawl.
  **The 6 `needs_review` companies from that pass were investigated
  2026-08-31 (see SESSIONS.md "Resolve the 6 Workday `needs_review`
  companies") — 1 resolved, 5 stay excluded:**
  - **`harman` — RESOLVED, stored, scraped.** Site `HARMAN` (wd3), found
    via an expanded site-candidate probe (robots.txt was empty). Verified
    to the original-5 standard: CXS total 556 == live rendered board,
    identical first posting, `hiringOrganization` "Harman Becker
    Automotive Systems" is an exact match to the DOL employer (Samsung
    subsidiary). 556 rows scraped, 0 NULL `is_relevant`/`date_posted`/
    `embedding`. `careers_url` =
    `https://harman.wd3.myworkdayjobs.com/en-US/HARMAN`.
  - **`red`, `western`, `tera` — DISCONFIRMED, stay excluded.** The
    generic-slug tenant belongs to a *different* company: `red.wd1` =
    Virgin Voyages / "V Cruises US, LLC" (not "RED HIBBERT GROUP");
    `western.wd1` = Western Colorado University (not "WESTERN WASHINGTON
    UNIVERSITY"); `tera.wd3` = Teranet Inc, Canada (not "TERA CLOUDX").
    Confirmed by live `hiringOrganization` + posting text.
  - **`daiichisankyo`, `wholefoods` — PERMANENTLY DROPPED, not stored,
    not on any retry list (final retry 2026-09-04, see SESSIONS.md
    "Final retry of Daiichi Sankyo and Whole Foods Workday onboarding").**
    Two prior retries (2026-09-01, 2026-09-04) both re-confirmed the
    `{tenant, dc, site}` identifiers are still correct (`daiichisankyo/
    wd1/DSI`, `wholefoods/wd5/wholefoods`) and both found the exact same
    persistent Workday-side outage: `daiichisankyo` CXS `/jobs` → `403
    S22 "permission denied"` (reproduced twice in the same session,
    2026-09-04) and `/en-US/DSI` → 302 to
    `www.myworkday.com/wday/drs/outage?t=daiichisankyo&s=dsi`;
    `wholefoods` CXS `/jobs` → persistent `502` (reproduced 3x
    consecutively, 2026-09-04) — the site shell itself now loads (200,
    real Whole Foods branding) but that's cosmetic; the actual jobs API
    it depends on is still down, and a `maintenancePageUrl` config value
    embedded in that shell's own JS confirms Workday itself still
    considers this tenant in outage. This is a confirmed persistent
    platform-side outage, not a HuntLoop detection or discovery
    limitation — the identifiers are right, the mechanism works (see
    `harman`, above), Workday's own infrastructure for these two
    specific tenants is what's down. Dropped for good after this second
    confirmation rather than left flagged for indefinite future retry;
    don't re-attempt these two without a new, explicit signal that
    Workday's outage for them has actually lifted (not just "it's been a
    while").

- **SmartRecruiters spider BUILT + onboarded 2026-09-01 (see SESSIONS.md
  "Build the SmartRecruiters spider").** `SmartRecruitersScraper`
  (`src/huntloop/spiders/smartrecruiters_spider.py`, name
  `smartrecruiters_api`) pages `GET /v1/companies/{companyId}/postings`
  (`limit=100` + `offset`, stop at `totalFound`) then one
  `GET .../postings/{id}` per posting for the description (the list
  response has none). Yields plain `JobPostingItem`s through the same
  source-agnostic `JobDataPipeline` — `is_relevant` + `embedding` are
  computed at insert like every other source, zero SmartRecruiters-
  specific wiring, zero manual backfill. `companies.name` == `ats_token`
  == the lower-cased companyId (SR lookup is case-insensitive, verified);
  no `careers_url` / 3-tuple needed (unlike Workday). Added to
  `main.py`'s `SPIDERS_BY_PLATFORM`, so the daily orchestrator picks it
  up automatically; `scripts/scrape_smartrecruiters.py` is the scoped
  entrypoint. **`custom_settings` sets `ROBOTSTXT_OBEY: False` for this
  spider only** — `api.smartrecruiters.com/robots.txt` is `Disallow: /`
  for `*`, but the Posting API is SR's documented public read feed (the
  same one LinkedIn/Indeed/Google Jobs consume); the project-wide
  `ROBOTSTXT_OBEY=True` is unchanged for everything else. An empty /
  erroring board is skipped with a logged reason + a `scrape_errors`
  metric, never a crash; a stale-but-real board is scraped as-is
  (staleness is a downstream relevance concern).
  **Onboarding gate: `scripts/discover_and_store_smartrecruiters.py`**
  reuses `discover_smartrecruiters_id.py` unchanged and enforces a REAL
  gate before writing a `companies` row: auto-store ONLY a resolver
  `confidence == "high"` reached via a non-collision candidate
  (`full-slug` / `core-slug`); a `name+2` / `name+Inc` collision-suffix
  win, or `first-word-only` / `acronym`, or `medium`/`low` confidence,
  is held and stored only if its id is listed in a `--confirmations`
  file (explicit human approval). `--from-report` re-buckets/commits
  from the saved scan JSON without re-running the ~3h network scan.
  Re-run cadence: after each quarterly DOL LCA ingest, like
  `detect_ats_for_sponsors.py`.
  **The earlier proof step (2026-08-31, kept below for context):** the
  postings API
  (`GET api.smartrecruiters.com/v1/companies/{companyId}/postings`) 200s
  with `totalFound: 0` for an unknown id, so a live call returning
  `totalFound > 0` is the only "this id is real" signal.
  `scripts/discover_smartrecruiters_id.py` (given a company name, tries
  ordered name-derived candidates — full slug, core slug minus legal
  suffixes, hyphenated, CamelCase, first-word, acronym, common
  SR-collision suffixes like `2`/`1`, each in lower/Capitalised/CamelCase
  since **companyIds are effectively case-varied** — `BoschGroup`,
  `ubisoft2`; `--id` verifies a web-search-found id the same way) plus a
  cross-check (`rapidfuzz` similarity of the queried name vs. the board's
  own `company.name`, a `test job`/`dummy` sandbox-title check, and a
  loose-guess flag for first-word/acronym wins) that flags
  low/medium-confidence matches for a human glance.
  `scripts/check_smartrecruiters_pagination.py` pages a resolved id end
  to end (`limit=100` + `offset`) and asserts unique-ids == `totalFound`
  with zero cross-page overlap. **Finding on a real DOL-sponsor test set:
  automatic slug-guessing resolved a live companyId for nearly all of
  them, but a couple pointed at a same-name different company or a
  sandbox tenant (both caught by the cross-check), and a meaningful
  share of "SmartRecruiters companies" — including several well-known
  brands — now have empty or stale parent boards because their real
  hiring moved to another ATS.** Recommendation recorded in SESSIONS.md:
  **GO** — discovery is at least as automatable as Workday's was and the
  API is the cleanest of any platform, provided the companyId is only
  stored after the name-similarity / not-a-sandbox cross-check passes (or
  a human confirms), mirroring the Workday `{tenant,dc,site}` onboarding
  gate; SR's ~4% prevalence in the "neither" set means build it but don't
  over-invest.
  **Real onboarding run (2026-09-01, full 8,113-employer "neither"
  population):** slug-guessing resolved a live board for 715; the gate
  auto-passed 227 (high confidence via a real base slug), held 488 for
  review (332 of them resolver-"high" but only matched via a `name+2`
  collision suffix — overwhelmingly abandoned free-trial tenants like
  `apple2` / `tesla1` / `infosys2`, all 2015-2017 data), and 5 were
  explicitly human-confirmed. 224 `companies` rows stored. One residual
  gate limitation: a squatter tenant whose board name matches the
  employer (`citibankna`, 2015-era Indonesian spam postings) passed on a
  full-slug high-confidence match — its junk postings are neutralised
  downstream by the relevance filter (`is_relevant=False`); a future
  onboarding-gate freshness signal would catch it.
- **Ashby discovery PROVEN 2026-09-01 (proof/discovery step only — no
  spider, no DB writes; see SESSIONS.md "Prove Ashby job-board
  discovery"). Recommendation: GO.** `scripts/discover_ashby_job_board.py`
  (given a company name, tries ordered name-derived slug candidates —
  full slug, core slug minus legal suffixes, hyphenated, first-word,
  acronym, `hq`/`careers`/`1`/`2` variants — and live-checks each against
  `GET https://api.ashbyhq.com/posting-api/job-board/{slug}`;
  `--slug` verifies a web-search-found slug the same way). Real response
  shapes: **unknown slug → HTTP 404 plain "Not Found"; real board → 200
  `{"jobs":[...],"apiVersion":...}`; real-but-nothing-listed board → 200
  with `jobs: []`.** So "resolved" = 200 with a NON-EMPTY jobs array;
  200-but-empty is reported separately as "found, unverifiable". **No
  pagination** — one response returns every listed job (verified on
  boards up to 768 jobs; only keys are `jobs`/`apiVersion`, no
  cursor/offset/nextToken). Each job already carries `descriptionHtml` +
  `descriptionPlain` + `jobUrl` (`jobs.ashbyhq.com/{slug}/{id}`), so —
  unlike Workday/SmartRecruiters — a spider needs **no per-job detail
  fetch**. Confidence signal (same spirit as the SR board-name
  cross-check): the API response has no org-name field, so the script
  fetches the public board page `jobs.ashbyhq.com/{slug}` and fuzzy-
  compares its `<title>`/`og:title` ("<Org> Jobs") to the queried name;
  a generic/loose-guess slug with a weak name match, an empty board, or a
  page that yields no org name is flagged for a human glance. **Test set
  (27): 22 web-search-sourced likely-Ashby employers (startup-weighted —
  the DOL-sponsor `companies` table has just 1 `ashby` row, and 0 rows
  currently have no ATS platform, so set (a) was a false-positive check
  against 5 known non-Ashby DB companies instead — all 5 correctly
  UNRESOLVED).** Of 19 confirmed-live Ashby boards in the set, **18
  resolved by slug-guess alone, 1 (Anysphere → `cursor`) needed the
  web-search/`--slug` fallback, 0 failed**; 3 more resolved to real but
  currently-empty boards (Airtable/Mercury/Fractile — the same
  empty/stale-board ambiguity SR has). 3 aggregator-listed "Ashby"
  companies (GetYourGuide, Opendoor, Clay) did not resolve —
  GetYourGuide confirmed migrated to Greenhouse, i.e. genuine
  not-on-Ashby, not a discovery miss; **third-party "companies using
  Ashby" lists are stale and must be live-verified.** Completeness
  cross-check: Linear's live board shows "Open Positions (28)", exactly
  matching the API's 28 unique job ids and titles. **Hypothesis "Ashby
  is more common among real target employers than the DOL sample
  suggested" — holds in general (Ashby is clearly ubiquitous among the
  smaller/startup tech employers DOL sponsor data underrepresents), but
  NOT for the current `companies` table**: none of the resolved Ashby
  users are in it, and the mid/large DOL companies tested are not on
  Ashby — so an Ashby spider pays off only alongside a separate
  startup-company sourcing path, not against today's company set.
  Onboarding, when built, must gate on the name-similarity /
  not-an-empty-board cross-check (or a human confirm), mirroring the
  Workday/SR gates. `scratch_ashby_discovery.json` (gitignored) holds the
  full test-set results.
- **Startup-sponsor sourcing groundwork for Ashby done 2026-09-01
  (sourcing/verification only — no Ashby discovery, no DB writes; see
  SESSIONS.md "Source LCA-verified startup candidates for a future Ashby
  pass").** The `companies` table's >= 20-filing floor structurally
  excludes the smaller employers Ashby skews toward, so
  `scripts/discover_startup_sponsors.py` assembles 53 web-search-sourced
  likely-Ashby startups (each with a real `source` recorded inline) and
  checks every one against the **full** `lca_disclosures` table (no
  >= 20 filter) via `find_matching_employers()` unchanged (token_set_ratio,
  threshold 88, overrides first). **The "real sponsorship evidence
  required" principle is NOT relaxed — a startup with 0 LCA filings is
  never eligible.** Raw fuzzy hit rate (44/53) is misleading: a
  spot-check of every questionable match against real
  `job_title`/`worksite` rows found **11 confirmed false positives** —
  short common-word company names (Linear, Mercury, Clerk, Lemonade,
  Lime, Clay, Harvey, Watershed, Immunic Therapeutics, Clera, Homebase)
  each colliding with an unrelated wrong-sector real employer at score
  >= 88 (the residual ambiguity `fuzzy_match.py`'s own threshold comment
  describes). Those verdicts are encoded in the script
  (`_CONFIRMED_FALSE_POSITIVE` / `_NEEDS_MANUAL_REVIEW`) — none were
  added to `sponsor_name_overrides` (that table is for confirmed
  *correct* mappings, not exclusions). **Final: 31/53 carry real,
  spot-checked LCA evidence — 22 of them with 1-15 filings, i.e. genuine
  sponsors the current cutoff excludes, confirming the gap.** 9 had no
  LCA match at all (reported, not dropped: PostHog, Payabli, Deliveroo,
  Zapier, Rentman, Jiga, Superbolt, Firecrawl, AgentMail — mix of non-US
  HQ, very young, or a genuine matcher miss worth manual review); 2
  (Sierra, Basis AI) flagged needs-review. **Nothing stored — the 31
  verified names are the input to a future Ashby-discovery-then-onboard
  pass, not yet run.** `scratch_startup_sponsor_candidates.json`
  (gitignored) holds the full results.
  **Follow-up 2026-09-01 — all 31 matches now individually spot-checked
  (verification only, no DB writes).** The 14 accepted on distinctive-name
  grounds in the sourcing pass (PLAID, UIPATH, RETOOL, AIRWALLEX US,
  REPLIT, SUBSTACK, VERCEL, DEEL, OPENAI, DOCKER, ELEVEN LABS, AGAVE TECH,
  SUPABASE, ESSENTIAL AI LABS) were each checked against real
  `job_title`/`worksite` rows — **all 14 confirmed, zero new false
  positives**, so the verified count stays **31** (unchanged). **Zapier
  and PostHog ("no LCA match") were investigated directly:** a full-table
  scan of `employer_name`/`employer_name_normalized`/`trade_name_dba` for
  any `ZAPIER`/`POSTHOG` variant returned nothing, and a low-threshold
  fuzzy pass found only unrelated companies (nearest: `ZPAPER` 83,
  `SHOP PO` 71) — a genuine true negative (both are fully-remote/
  distributed employers that don't sponsor US visas), not a matcher miss.
  Fuzzy-matching logic (token_set_ratio / threshold 88 /
  `sponsor_name_overrides`) was not modified.
- **Ashby spider BUILT + onboarded 2026-09-01 (see SESSIONS.md "Build the
  Ashby spider + gated onboarding").** `AshbyScraper`
  (`src/huntloop/spiders/ashby_spider.py`, name `ashby_api`) — one
  `GET api.ashbyhq.com/posting-api/job-board/{jobBoardName}` per company,
  **no pagination, no per-job detail fetch** (`descriptionHtml` +
  `jobUrl` are inline — the simplest of any platform). Yields plain
  `JobPostingItem`s through the shared `JobDataPipeline`; `is_relevant` +
  `embedding` computed at insert like every other source, zero manual
  backfill. `date_posted` = the inline `publishedAt` ISO timestamp.
  404 / empty-`jobs` boards are skipped with a logged reason + a
  `scrape_errors` metric, never a crash. `custom_settings` sets
  `ROBOTSTXT_OBEY: False` for this spider only (API client, same
  carve-out reasoning as the SmartRecruiters spider). `companies.name` ==
  `ats_token` == the lower-cased jobBoardName slug; `careers_url` =
  `https://jobs.ashbyhq.com/{slug}` (parity with the pre-existing `ramp`
  row — not needed to scrape). Added to `main.py`'s
  `SPIDERS_BY_PLATFORM` (falls in the plain `companies=tokens` branch);
  `scripts/scrape_ashby.py` is the scoped entrypoint.
  **Onboarding gate: `scripts/discover_and_store_ashby.py`** reuses
  `discover_ashby_job_board.py` unchanged over the 33-company
  LCA-verified startup list. Auto-store requires Ashby confidence `high`
  **AND** LCA verdict `verified` **AND** a strong slug candidate
  (`_NON_AUTO_KINDS = {suffix-variant, first-word-only, acronym}` never
  auto-passes — mirrors the SmartRecruiters gate). Held: 0-job
  (`found-unverifiable`) boards, weak-candidate wins, and — critically —
  **any `needs_review` LCA verdict (Sierra, Basis AI) is held regardless
  of a strong Ashby match; an ATS hit is not sponsorship evidence.**
  Held rows store only via a `--confirmations` file (gitignored
  `confirmed_*.txt`). **Real run: 31/33 resolved; 23 auto-passed, 3
  held-then-human-confirmed after a live board cross-check (`hex`,
  `ironcladhq`, `distyl` — all reached via a weak candidate kind), 8
  held total (incl. Sierra/Basis AI and 3 empty boards: deel / vercel /
  essentialai), 2 unresolved (Retool — no live Ashby board; Anysphere —
  real board is `cursor`, not name-derivable).** 25 `companies` rows
  stored (+ pre-existing `ramp`), then scraped: **3,008 `job_postings`
  rows across 26 companies, 0 NULL `is_relevant`, 0 NULL `embedding`, no
  backfill.** Live cross-checks (real browser): Semgrep board "Open
  Positions (10)" == 10 rows, Hex "(29)" == 29, Notion "(132)" == 132.
  **Follow-up 2026-09-01 — Anysphere onboarded, closing the
  confirmations-file gap (see SESSIONS.md "Onboard Anysphere").**
  Anysphere's board is `cursor` (not name-derivable), so
  `confirmed_ashby_slugs.txt` now also accepts a `Name<TAB>slug` line that
  `read_confirmations()` turns into a forced candidate for the UNCHANGED
  resolver (`evaluate()` passes it to the resolver's existing
  `forced_slug` arg) — the gate's confidence rules are untouched, `cursor`
  still resolves `low-suspect` and is admitted only via the confirmations
  file. Stored (`name`/`ats_token` = `cursor`) + scraped: **119 rows, 0
  NULL is_relevant/embedding**. Live cross-check: `jobs.ashbyhq.com/cursor`
  404s (Cursor keeps the hosted board unlisted, embeds it on
  cursor.com/careers) but cursor.com/careers renders the same `cursor`
  jobBoardName data — 119 listings, exact title matches.
  **Follow-up 2026-09-01 — the two `needs_review` LCA matches resolved
  (see SESSIONS.md "Resolve the two needs_review LCA matches").**
  - **Sierra:** the fuzzy hit "Blue Sierra, Inc." (2 filings, generic SWE
    role) is a FALSE POSITIVE, but the real Sierra AI is a genuine sponsor
    filing as **"Sierra Technologies, Inc." — 14 certified filings**
    ("Agent Engineer" ×3, "Research Engineer", etc.; SF+NYC; $150K–310K).
    The matcher missed it (`token_set_ratio("SIERRA","SIERRA
    TECHNOLOGIES")` < 88). Fixed with a `sponsor_name_overrides` row
    (`sierra` → `SIERRA TECHNOLOGIES`) — the designated manual-correction
    mechanism, same as the pre-existing `kraken` entry; no logic changed.
    Sierra → `verified`, auto-passed the gate, **stored + scraped: 209
    rows, 0 NULL is_relevant/embedding**. Live cross-check:
    `jobs.ashbyhq.com/sierra` "Open Positions (209)", board is
    unmistakably Sierra AI ("Agent Engineering" dept, "Software Engineer,
    Agent", "Executive Assistant, Office of the Co-Founders").
  - **Basis AI:** the fuzzy hit "Basis LLC" (2 filings, "Strategic
    Business Design Manager", NYC, $122k) is a CONFIRMED FALSE POSITIVE —
    an "LLC" (Basis raised $100M and is a C-corp), not a role Basis hires,
    and **no "Basis AI/Technologies/Platform/Inc" exists anywhere in
    `lca_disclosures`**. No sponsorship evidence → **not stored, stays
    excluded** (`discover_startup_sponsors.py` verdict `false_positive`).
  `sponsor_name_overrides` now has 2 rows (`kraken`, `sierra`). 28 `ashby`
  companies; only Retool (no live Ashby board) and Basis AI (false
  positive) remain out of the 33.

## How to run things

See README.md for full detail (setup, running the scraper, migrations, tests).
Short version: `python main.py` (scraper), `pytest` (tests), `alembic upgrade
head` (migrations).

## Current phase / what's next

Phase 0 (repo hygiene / foundations) is done: env config extraction, Alembic
setup, schema-drift reconciliation, small bug fixes, pytest scaffold,
README, Docker (app + Postgres via docker-compose), and a minimal CI
workflow (migrations + pytest against a real Postgres service on every
push/PR).

Phase 1 (sponsorship-matching MVP) core is now done, as of 2026-08-20:
DOL LCA disclosure files were audited and ingested (11 fiscal-year/quarter
files, 1,431,321 rows in `lca_disclosures`, via
`scripts/ingest_lca_disclosures.py`); every row has a mechanically
normalized `employer_name_normalized` (`normalize_employer_name()`, 108,575
distinct values vs. 129,295 distinct raw `employer_name` values); fuzzy
matching (`find_matching_employers()`, `rapidfuzz`-based, with a
`sponsor_name_overrides` manual-correction escape hatch) resolves a raw
company name to likely `employer_name_normalized` candidates; and
`get_sponsorship_summary()` applies that to a real `Company` row and
returns an aggregated sponsorship picture (total approved LCAs by fiscal
year, distinct job titles, distinct worksite states). Verified end-to-end
against both currently-scraped companies at the time (`checkr`: 48
approved LCAs 2021-2025; `duolingo`: 95 approved LCAs 2021-2025) — both
matches manually confirmed correct, no override needed. Extended
2026-08-22 (see SESSIONS.md) to the 4 companies scraped since Phase 2 —
`figma` (107 approved LCAs), `palantir` (241), `wealthfront` (43) all
resolved to a single unambiguous match each; `kraken` (1 approved LCA)
needed `sponsor_name_overrides`' first real entry to exclude an unrelated
company (`Raken, Inc.`) that fuzzy-matched above threshold. Shared logging
(`src/huntloop/logging_config.py`, `LOG_LEVEL`-controlled, console +
rotating file) is also in place across the scraper, pipeline, and scripts.
See SESSIONS.md for the full log.

A security audit (2026-08-21, see SESSIONS.md) found the repo clean on
`pip-audit`/`bandit`/`gitleaks` (including full git history — the
pre-Phase-0 hardcoded Postgres password never actually entered git
history). The two medium-severity findings (Docker running as root,
`data/raw/`/`logs/` missing from `.dockerignore`) are fixed — see the
Docker bullet above.

**A second, more thorough security-fix pass shipped 2026-09-15/16 (see
SESSIONS.md and the git log — "Bump starlette, urllib3, cryptography,
lxml, soupsieve for known CVEs", "Bump Next.js and pdfminer.six to fix
two confirmed-live-reachable RCE CVEs", "Close plaintext Caddy bypass
ports and fix resume-upload DoS", "Fix rate-limiter IP detection
collapsing all requests behind Caddy", "Untrack .idea/") — several items
this section previously listed as "left open by deliberate choice" are
now closed, not still-open follow-ups:**
- **`.idea/` is no longer tracked in git** — `git rm --cached` removed
  the 5 leftover files (committed before the `.gitignore` rule existed);
  local IDE state on disk is untouched. The "tracked despite being
  gitignored" gap described below no longer exists.
- **`requirements.txt` pinning improved materially, though it is still
  not fully pinned**: 8 of 30 direct deps now carry an exact `==` pin
  (`scrapy`, `alembic`, `pytest`, `prometheus-client`, `pdfplumber`,
  `sentence-transformers`, `pgvector`, `groq`), and `fastapi`/`starlette`/
  `urllib3`/`cryptography`/`lxml`/`soupsieve` (previously fully
  unpinned, sitting on whatever version happened to already be
  resolving) now carry explicit `>=` security floors — `cryptography` is
  deliberately capped `<47` since scrapy's own `pyOpenSSL` dependency
  pins it there (confirmed via a real `pip check` conflict), leaving a
  known, flagged residual gap on CVEs that only have a 47+/48+/50+ fix.
  The remaining ~16 direct deps are still fully unpinned — "mostly
  unpinned" is no longer accurate framing, but "fully pinned" isn't
  either.
- **Next.js and pdfminer.six were bumped for two confirmed-live-reachable
  RCE CVEs** (`14bd844`) — Next.js 16.3.2 → 16.3.5 (an unauthenticated
  RCE in the Image Optimization API, confirmed reachable on this app's
  own frontend container even though it never imports `next/image`
  itself) and pdfminer.six 20250506 → 20251230 (via a pdfplumber bump,
  since pdfplumber hard-pins pdfminer.six with an exact `==`).
- **A real rate-limiter bug behind the new Caddy proxy was found and
  fixed** (`64d09db`) — every request proxied through Caddy was
  resolving to Caddy's own container IP for `request.client.host`,
  collapsing every real visitor into one shared per-IP rate-limit
  bucket; fixed by pinning `caddy` to a static compose-network address
  and wiring uvicorn's `ProxyHeadersMiddleware` to trust only that one
  peer (`TRUSTED_PROXY_IPS`).
- **The `api`/`frontend` host ports are now bound to `127.0.0.1` only**
  (`2d6fe1a`), not every interface — previously reachable from the
  LAN/internet even with Caddy in front, since Caddy fronting a service
  doesn't stop that service's own directly-published port from also
  being open to the world.
- A resume-upload DoS (unbounded body size before FastAPI's multipart
  parser buffers it) was fixed with a hard 15MB ASGI-layer cap
  (`MaxUploadSizeMiddleware`), and per-IP rate limiting was added to the
  resume-upload/activate endpoints (`2d6fe1a`).

**Still open by deliberate choice, not oversight**: there's still no
automated `pip-audit`/`bandit` step in CI (confirmed by re-checking
`.github/workflows/ci.yml` during this pass — no such step exists), and
`requirements.txt` is still partially unpinned as described above. Don't
"fix" these unprompted — they're tracked follow-ups, not bugs.

A standalone ATS-detection function (`detect_ats()`,
`src/huntloop/ats_detection.py`, 2026-08-21, see SESSIONS.md) also exists
now: given a company's careers URL, it identifies Greenhouse/Lever/
Ashby/Workday/SmartRecruiters from static HTML first, falling back to a
Playwright-rendered fetch (added same day, see SESSIONS.md) only when the
static fetch matches nothing, or `unknown` if neither does. Manually
verified against the same 13 real, live URLs from the first pass plus the
`checkr.com/company/careers/open-careers` case the render fallback was
built to fix — 12/13 now correctly detected (up from 11/13 static-only);
the 13th (`checkr.com/company/careers`, the bare landing page) correctly
stays `unknown` even after rendering, because that exact page never
embeds the ATS board itself (see the architectural decisions above for
why that's not a bug). Not wired into the scraper, `company_tokens`, or
any pipeline — detection only, standalone.

**Phase 2 (multi-ATS scraping) core is now done, as of 2026-08-21** — the
full loop from detection to real scraped data works end-to-end:

- `LeverScraper` (`src/huntloop/spiders/lever_spider.py`) exists
  alongside `GreenhouseScraper`, mirroring its structure and
  `custom_settings` exactly. `JobDataPipeline` needed zero changes for
  it — confirmed genuinely source-agnostic (see the architectural
  decisions above). The `job_sources` naming duplication this first
  surfaced (`'Greenhouse'` vs. `'greenhouse_api'`) was root-caused and
  closed the same day — `job_sources` now has exactly one row per real
  source.
- `companies.ats_platform`/`ats_token`/`careers_url` columns exist,
  populated via `scripts/detect_and_store_ats.py` for a 9-company
  curated list spanning all 5 platforms `detect_ats()` recognizes (2
  never tested against it before that script — both verified correct
  independently). A same-day follow-up fixed a transient-failure
  overwrite bug this surfaced (see the architectural decisions above).
- Both spiders now accept a `companies` list instead of one hardcoded
  token, and `main.py` is a real orchestrator: it queries
  `companies.ats_platform`, routes `greenhouse`/`lever` companies to one
  `process.crawl()` call each with their full token list, and skips
  `ashby`/`workday`/`unknown`/NULL companies with a clear log message
  (see the architectural decisions above). Verified end-to-end for real
  (`python main.py`, no mocking): `job_postings` 136 -> 616 (+480) across
  6 companies with implemented spiders (`checkr`/`duolingo`/`figma` via
  Greenhouse, `kraken`/`palantir`/`wealthfront` via Lever); `ramp`
  (ashby), `adobe` (workday), `brex` (unknown), and `OpenAI` (NULL) all
  correctly skipped with distinct log messages, not silently dropped.
  `figma` and `palantir` (never scraped before this run) spot-checked
  against the DB and live pages — correct. `kraken` legitimately scraped
  0 jobs (its real Lever board has 0 open postings right now, confirmed
  live — not a detection or spider bug).

Not yet started / explicitly deferred (NOTE: this list predates the
Workday, SmartRecruiters, and Ashby spiders — all now built; see their
bullets above): broadening the curated company
list beyond the current 9, automating the
`detect_and_store_ats.py` -> `main.py` sequence (currently two separate
manual steps), broader scraper coverage generally, any UI/API surface for
`get_sponsorship_summary()` (it's a Python function today, called
directly, not exposed via an endpoint or the scraper pipeline), curating
`sponsor_name_overrides` for companies fuzzy matching doesn't resolve
cleanly (table is still empty), broader test coverage, scraper
parsing/HTTP tests, CI linting/build/deploy steps, any FK from
`companies` to `lca_disclosures` (deliberately not built — see the
architectural decisions above), and an LLM-extraction fallback for career
pages that resist both static fetch and rendering. Nothing beyond what's
listed above should be assumed built.

**Since the above (see SESSIONS.md for full detail on each), a full
sponsorship-matching + API + frontend stack was built on top of Phase 2's
scraping foundation, and the Frontend/UI MVP is now closed out
(2026-08-23):** DOL LCA disclosures ingested (1,431,321 rows) and fuzzy-
matched to companies (`find_matching_employers()`/
`get_sponsorship_summary()`); embedding-based resume-to-job match scoring
(pgvector, `all-MiniLM-L6-v2`) with a Groq-based skills-matching engine
(matched/missing skills per job) and a daily-cron-integrated sanity
filter; a FastAPI backend (`src/huntloop/api/`) exposing `GET /jobs`,
`GET /jobs/{id}`, and `PATCH /jobs/{id}/application` over real Postgres
data, with `job_applications` (status/`applied_at`/`status_updated_at`/
`notes`) as a real upserted-not-history table; and a Next.js frontend
(`frontend/`, App Router + TanStack Query + Tailwind) rendering the real
job list with company/min-score filtering, sorting, and pagination, a
calibrated score indicator, matched/missing skill chips, and — as of this
entry — an interactive `StatusControl` on each job card wired to the real
`PATCH` endpoint via a TanStack Query mutation with optimistic updates,
rollback-on-failure, and toast feedback, fully verified against the real
running system (see SESSIONS.md's 2026-08-23 "Interactive status updates
on job cards" entry for the exact screenshots/psql evidence). Prometheus/
Grafana observability and local cron scheduling are also in place,
layered on the scraper.

**The frontend was reskinned 2026-08-23 (see SESSIONS.md's "Frontend
reskin against the Claude Design mockup" entry) against
`design/HuntLoop.dc.html`** — a Claude Design mockup in x-dc/sc-for/sc-if
runtime format; read it as the visual/layout spec (colors ported into
`frontend/src/lib/theme.ts`, typography via `next/font/google`'s
JetBrains Mono, Tailwind theme tokens in `globals.css`), not as literal
code. **SUPERSEDED 2026-09-07 by the Register visual identity (see the
top of the frontend bullet above and SESSIONS.md "Frontend visual
identity") — this mockup's warm-cream/terracotta palette, JetBrains
Mono, and rounded-card look are no longer current; the page structure
and the two screens it added are.**
  **Two screens that didn't exist before this reskin were built as
part of it**, reusing only the existing three API endpoints: a job detail
page (`frontend/src/app/jobs/[id]/`, matched skills shown first in green,
missing second in dashed muted styling) and an applications tracker
(`frontend/src/app/applications/`, kanban board with native HTML5
drag-and-drop + a list view, both driving the same `PATCH` mutation now
shared via `frontend/src/hooks/useApplicationStatus.ts` instead of living
only in `StatusControl`). The job list gained a cards/table view toggle.
Per-job H-1B sponsor status, ATS platform, and salary estimate were shown
in the mockup but not exposed by the real API at reskin time — since
closed (2026-08-23, see the sponsor-summary entries above and
SESSIONS.md): the API now returns all three and the job detail
page/sponsor sidebar/list-view sponsor indicator all consume them for
real. AI resume-review screens and location-radius/department filters
remain explicitly out of scope per that task's own instructions.
**Dashboard is also no longer out of scope** — `frontend/src/app/
dashboard/page.tsx` (added 2026-08-23, see SESSIONS.md's "Real dashboard
page" entry) is real, wired to `GET /dashboard/stats`, and is the app's
home view (`/` redirects there — see the routing note above). **Neither
is Resume version management** — `frontend/src/app/resumes/page.tsx`
(added 2026-08-24, see SESSIONS.md's "Real resume management page"
entry) is real, wired to Step 13's `GET /resumes`/`POST /resumes/
upload`/`PATCH /resumes/{id}/activate`, and is reachable via `NavBar`'s
fourth tab. This reskin (plus the sponsor-data, dashboard, and resume-
management follow-ups) is now the current state of the frontend — treat
everything above this note (Phase 0 repo hygiene through the
ATS-detection standalone function) as historical foundation, not the
latest picture. Not yet started: the AI resume-review UI (missing
keywords/phrasing suggestions/formatting notes — no backend for it
exists yet), Ashby/Workday spiders, and everything else already listed
as deferred above — those deferrals still stand.

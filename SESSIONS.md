# SESSIONS.md

Append-only running log, one entry per work session. Short and factual —
detail lives in the external prompt log, not here.

---

## 2026-08-17 — Phase 0, steps 1-6 (retrospective)

**Did:** Renamed `scrapers` package to `huntloop`, fixed `src/` as the actual
package root. Extracted hardcoded DB credentials into `.env`/`DATABASE_URL`
with fail-fast if unset. Set up Alembic (initial migration stamped against
the already-existing live schema, since tables predated migrations).
Reconciled `db_models.py` against live schema drift in a second migration
(added missing columns, dropped `location`, fixed the `job_url` unique
constraint, cleaned up 53 leftover test rows + duplicate job source). Fixed
five small standalone bugs (missing `Field()` parens, two `__repr__`
AttributeErrors, a `NameError`-prone log line, dead `IntegrityError` except
clause, ~50 lines of commented-out legacy pipeline code). Added a pytest
scaffold with schema-isolated integration tests for the pipeline.

**Decided:** Config extraction, migrations, and schema reconciliation were
kept as separate, reviewable steps rather than one big cleanup — see
CLAUDE.md for the specific architectural calls (job_url as key,
h1b_sponsorship kept, schema-based test isolation, location dropped).

**Next:** Write README.md. Beyond that, Phase 1 (broader test coverage,
scraper parsing/HTTP tests) is an open decision, not yet started.

---

## 2026-08-19 — README (retrospective)

**Did:** Wrote README.md covering prerequisites, setup, running the scraper,
running tests, adding a company to scrape, and project status.

**Next:** Dockerize the app.

---

## 2026-08-19 — Dockerize app + Postgres (retrospective)

**Did:** Added a `Dockerfile` (python:3.13-slim, installs `requirements.txt`,
runs `python main.py` unchanged) and a `docker-compose.yml` with `app` +
`db` (postgres:18) services; `db` credentials come from `.env` via
`POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`, and `app`'s
`DATABASE_URL` is built from those same vars pointed at the `db` service
hostname. Named volume for Postgres data. Verified against a genuinely
empty containerized Postgres, which surfaced and fixed several pre-existing
bugs: missing `psycopg2-binary` in `requirements.txt`, an unpinned `scrapy`
that had moved to an async `start()` spider API, a schema-reconciliation
migration that assumed a `location` column already existed, and several
model columns (`companies.website`/`h1b_sponsorship`/etc.) that earlier
migrations assumed were already live but never actually created via DDL.
Added a migration for the latter. README got a "Run with Docker" section.

**Decided:** Fixed the above bugs in place rather than working around them
in the Docker setup, since they blocked `alembic upgrade head` and the
scraper from working against a fresh database at all — not scope creep,
a precondition for the Docker step's own verification.

**Next:** Add CI (GitHub Actions).

---

## 2026-08-19 — GitHub Actions CI

**Did:** Added `.github/workflows/ci.yml`: on push/PR to `master`, checks
out the repo, sets up Python 3.13, installs `requirements.txt`, spins up a
real `postgres:18` service container, runs `alembic upgrade head` against
it, then runs `pytest`. DB credentials are throwaway values defined in the
workflow's `env:` (a fresh, disposable CI database, not real credentials).
No build/push/deploy/lint steps yet — just test execution.

**Decided:** Triggered on `master`, not `main` — this repo's actual default
branch is `master`.

**Next:** Linting and a build/push step are open, not yet started.

---

## 2026-08-20 — LCA disclosures table

**Did:** Added `LcaDisclosure` model (`lca_disclosures` table) to
`db_models.py` for DOL LCA disclosure records, based on the real DOL LCA
schema audited from downloaded sample files (`CASE_NUMBER`, `EMPLOYER_NAME`,
`CASE_STATUS`, `JOB_TITLE`, `WORKSITE_*`, `WAGE_RATE_OF_PAY_*`, etc.), plus
`fiscal_year`/`quarter`/`source_file` provenance columns and an
`employer_name_normalized` column left nullable/unpopulated for now.
Indexes on `employer_name`, `employer_name_normalized`, `fiscal_year`, and
`(fiscal_year, quarter)`. Generated via `alembic revision --autogenerate`
and applied with `alembic upgrade head` — table stands alone, no FK to
`companies` yet. No parsing/ingestion code, no existing table touched.

**Decided:** The local Docker Postgres volume's schema had drifted from
Alembic's bookkeeping — a prior session's tables were created via the
pipeline's `Base.metadata.create_all()` fallback rather than
`alembic upgrade head`, leaving no `alembic_version` row even though the
schema matched head. Ran `alembic stamp head` to reconcile that bookkeeping
(no DDL, no data touched) before generating this migration, since
autogenerate needs Alembic to know the true current revision.

**Next:** Step 5 will add the employer-name normalization/matching logic
and populate `employer_name_normalized` + the FK to `companies`. No LCA
ingestion pipeline exists yet — this step only added the table.

---

## 2026-08-20 — Remove Base.metadata.create_all() fallback

**Did:** Removed the `Base.metadata.create_all(engine)` call (and the now-
unused `Base` import) from `JobDataPipeline.__init__` in `pipelines.py` and
from `test_db_insert.py`'s `main()`. `alembic upgrade head` is now the
single source of truth for schema creation — no other logic in either file
touched. `tests/conftest.py`'s `create_all()` is untouched; it creates
tables in pytest's throwaway per-session schema, a separate test-isolation
mechanism, not an app startup fallback. Verified via a full clean-state
run: `docker-compose down -v`, `alembic upgrade head` against the fresh
empty Postgres (all 4 migrations applied), then the scraper end-to-end
with no `create_all` to fall back on — 44 jobs inserted successfully.
`pytest` still passes (2/2).

**Decided:** This was the root cause of the `alembic_version` drift found
in the previous step — the pipeline's `create_all()` fallback could silently
create tables outside of Alembic's bookkeeping whenever the scraper ran
before migrations did. Removing it makes that drift structurally
impossible going forward.

**Next:** Step 5 (employer-name normalization/matching) remains the next
piece of LCA-related work; no LCA ingestion pipeline exists yet.

---

## 2026-08-20 — One-off LCA ingestion script (FY2025_Q4)

**Did:** Added `scripts/ingest_lca_disclosures.py`, a one-off (not part of
the app's ongoing pipeline) script that loads
`data/raw/dol_lca/LCA_Disclosure_Data_FY2025_Q4.xlsx` into `lca_disclosures`.
Reads via pandas/openpyxl, filters to non-null `CASE_NUMBER` (padding-row
guard) and `CASE_STATUS` in `('Certified', 'Certified - Withdrawn')` (the
approved-sponsorship statuses per Step 1), maps DOL columns to model
fields, stamps every row with `fiscal_year=2025`/`quarter=4`/`source_file`,
leaves `employer_name_normalized` null, and stores `WAGE_UNIT_OF_PAY`
as-is without attempting to fix the known mismatched-unit rows. Batches
inserts at 5,000 rows via Postgres `INSERT ... ON CONFLICT (case_number)
DO NOTHING`, so duplicate `case_number`s are skipped per-row rather than
aborting the batch. Added `pandas`/`openpyxl` to `requirements.txt`.

Ran against the containerized Postgres (`localhost:5433`, verified via
`inet_server_port`/`version()` to be the Docker instance, not the real
local one on 5432): 118,580 real rows in the sheet, 2,885 filtered as
non-approved, 115,695 inserted in 277s (~420 rows/sec). Row count in the
DB matches exactly; spot-checked 5 random rows field-by-field against the
source Excel with an exact match. Re-ran the script a second time: 0
inserted, all 115,695 correctly skipped as duplicates, no crash, DB count
unchanged. `pytest` still passes (2/2) with `pandas`/`openpyxl` now in the
app image.

**Decided:** Chose a batch size of 5,000 (~23 batches for 115K rows) to
balance round-trip count against blast radius — small enough that one bad
batch (e.g. a future file with a NOT NULL violation) doesn't lose a huge
chunk of otherwise-good work, large enough to get real throughput from
Postgres's multi-row `INSERT ... VALUES`. Used `ON CONFLICT DO NOTHING`
(Postgres-specific) rather than per-row try/except, since it resolves
uniqueness conflicts within a single batched statement instead of forcing
a slow one-row-at-a-time fallback.

**Next:** This script only covers FY2025_Q4. Ingesting the other quarters
downloaded in Step 1, and Step 5's employer-name normalization/matching +
FK to `companies`, are both still open.

---

## 2026-08-20 — Generalized LCA ingestion to all downloaded quarters

**Did:** Generalized `scripts/ingest_lca_disclosures.py` from a single
hardcoded FY2025_Q4 run to scan `data/raw/dol_lca/` for every file matching
`LCA_Disclosure_Data_FY<YYYY>_Q<N>.xlsx` (11 matched), parsing
`fiscal_year`/`quarter` from each filename instead of hardcoding them.
Explicitly skips `H-1B_Disclosure_Data_FY*` (legacy pre-2021 format) and
`LCA_Appendix_A_*` (cap-exemption file, separate schema) via the filename
pattern. The per-file filtering/batching/`ON CONFLICT DO NOTHING` logic
from the FY2025_Q4-only version was refactored into a shared
`ingest_file()` function, called once per file in oldest-fiscal-year/
quarter-first order, with per-file progress logging (rows read, filtered,
inserted, skipped).

Hit one new issue generalizing beyond a single quarter: FY2024_Q3 has at
least one row with a malformed `SOC_CODE` cell (`"15-1253 Software Quality
Assurance"`, 34 chars — the SOC title looks accidentally appended to the
code) that overflowed the `VARCHAR(20)` column and crashed the run
mid-file with `psycopg2.errors.StringDataRightTruncation`. Fixed by adding
a generic truncate-and-log guard (`STRING_COLUMN_MAX_LENGTHS`, sourced from
the `LcaDisclosure` model's own column widths, not hardcoded) that trims
any oversized string field to fit before insert and logs a warning with the
case number and full original value — same "store as-is, don't silently
lose it" philosophy already used for the `WAGE_UNIT_OF_PAY` quirk. Thanks
to per-batch commits, the partial progress from the crashed run wasn't
lost; the ON CONFLICT dedup logic picked back up cleanly on re-run.

Verified end-to-end: ran the full script against all 11 files (1,448,680
approved rows considered total, 844,539 newly inserted given the
already-present FY2025_Q4 data, wall time ~44min for the fully-fresh
portions); grand total row count (1,431,321) matches `SELECT COUNT(*) FROM
lca_disclosures` exactly; spot-checked that every `source_file` in the DB
maps 1:1 to the `fiscal_year`/`quarter` parsed from its filename, across
all 11 files, not just 2; ran the whole script a second time end-to-end
(exit code 0, ~46min) and confirmed 0 new inserts, all 1,448,680 approved
rows correctly skipped as duplicates — full-run idempotency holds, not
just single-file. Also discovered and fixed unrelated local-Postgres
issues blocking this work: `companies`/`job_locations`/`job_skills` were
owned by `postgres` instead of `job_scraper` (fixed via `ALTER TABLE ...
OWNER TO`), and `alembic_version` was stuck one revision behind
(`37f5b1de06fe`) despite the DB schema already fully matching
`7c464378b3de` — fixed via `alembic stamp 7c464378b3de` (bookkeeping only,
no DDL) then `alembic upgrade head`, which then only needed to run the
`lca_disclosures` `CREATE TABLE`.

**Decided:** Truncate-and-log rather than drop the row or widen the
column for the oversized `SOC_CODE` value — dropping would silently lose
an otherwise-valid disclosure record over one malformed field, and
widening the schema wasn't asked for and risks masking future data-quality
regressions in a column that should stay short. Logging the full original
value alongside the truncation keeps it inspectable without blocking the
run.

**Next:** 11 fiscal-year/quarter files are now loaded (FY2021 Q1 & Q4,
FY2022 Q4, FY2024 Q1-Q4, FY2025 Q1-Q4; the legacy pre-2021 H-1B format and
the FY2026 Q3 Appendix A file remain out of scope), 1,431,321 total rows
in `lca_disclosures`. Step 5 (employer-name normalization/matching + FK to
`companies`) remains the next piece of LCA-related work.

---

## 2026-08-20 — Reconciled a reported-totals discrepancy (no bug found)

**Did:** The write-up above cited two different totals for the same run —
1,431,321 ("DB total") and 1,448,680 (from the idempotency re-run's "Total
approved rows" line) — a 17,359-row gap that needed explaining. Verified
`SELECT COUNT(*) FROM lca_disclosures` fresh: **1,431,321**, matching
`SELECT COUNT(DISTINCT case_number)` exactly (zero duplicate rows in the
table) and matching the sum of `SELECT source_file, COUNT(*) ... GROUP BY
source_file` across all 11 files.

Traced the gap to a reporting mismatch, not a data or script bug: the
script's "Total approved rows" line sums rows that passed the
`CASE_NUMBER`/`CASE_STATUS` filter *before* `ON CONFLICT (case_number) DO
NOTHING` collapses duplicates — it was never meant to equal the stored row
count, but the earlier summary juxtaposed the two numbers in a way that
implied they should match. Confirmed the actual mechanism with direct
evidence rather than assuming: `LCA_Disclosure_Data_FY2021_Q4.xlsx` has
123,716 approved rows, all with distinct `CASE_NUMBER`s within that file
(checked directly — 0 intra-file duplicates), yet only 123,314 of them are
attributed to `FY2021_Q4` in the DB; the other 402 case numbers are
attributed to `FY2021_Q1`, because those same LCA case numbers appear in
*both* the Q1 and Q4 DOL disclosure exports (a real DOL data quirk — a
case can reappear across adjacent quarterly files), and since files
process oldest-first, `ON CONFLICT` correctly kept the Q1 copy and skipped
Q4's. Per-file (approved-rows-considered minus DB count) diffs sum to
exactly 17,359, confirming this fully accounts for the gap with no other
contributing bug.

**Decided:** No fix needed — ingestion, batching, and dedup logic are all
correct. `1,431,321` is the verified, trustworthy total. Going forward,
"approved rows considered" (pre-dedup, sum-of-filtered-rows-across-files)
and the actual stored row count are two legitimately different metrics
and should not be conflated when reporting.

**Next:** Unchanged — Step 5 (employer-name normalization/matching + FK to
`companies`) is next.

---

## 2026-08-20 — Employer-name normalization + backfill

**Did:** Added `normalize_employer_name()` in
`src/huntloop/matching/normalize.py`: uppercases, strips periods/commas,
collapses repeated whitespace, and drops a trailing legal-entity suffix
(`INC`, `LLC`, `LLP`, `LP`, `CORP`, `CO`, `LTD`, `PLLC`, `PC` — punctuated
variants like `L.L.C.` collapse to `LLC` once punctuation is stripped, so
they match without special-casing). Suffixes are only stripped when
trailing — a suffix word appearing mid-name is left alone. This is
mechanical normalization only, not full entity resolution; it's
deliberately conservative about not merging unrelated companies. 8 unit
tests in `tests/test_normalize.py`, using real raw `EMPLOYER_NAME` values
pulled from the ingested data (not invented examples) — including the
6-variant `AMAZON DEVELOPMENT CENTER U.S./US` group, the 5-variant
`AMAZON.COM SERVICES LLC` group, and the `A&A INFOSYSTEMS, INC` vs.
`INFOSYS LIMITED` false-positive-risk check. All pass.

Wired `normalize_employer_name()` into `scripts/ingest_lca_disclosures.py`
so `employer_name_normalized` is populated at insert time for any future
file, not left `NULL`. Added a new one-off script,
`scripts/backfill_employer_name_normalized.py`, to backfill the
1,431,321 rows that predated this — selects only rows where
`employer_name_normalized IS NULL` (safe to interrupt/re-run), batches the
`UPDATE`s at 5,000 rows (same round-trip-vs-blast-radius reasoning as the
ingestion script's insert batches), using a Core-level (not ORM-bulk)
parameterized `UPDATE ... WHERE id = :id` executed per batch via
`session.execute(stmt, list_of_dicts)` — SQLAlchemy's ORM-level bulk
`update()` rejected this pattern (`InvalidRequestError`) since it expects
either full ORM object tracking or `synchronize_session=None` plus
primary-key-bearing mappings; using the plain `Table` object side-steps
that entirely, which is simpler than fighting the ORM bulk-update API.

Backfill ran against the live `lca_disclosures` table: 1,431,321 rows
updated in 368.2s (~6.1 min), 0 errors. Verified `SELECT COUNT(*) FROM
lca_disclosures WHERE employer_name_normalized IS NULL` = 0 afterward.
Distinct `employer_name` count before: 129,295; distinct
`employer_name_normalized` count after: 108,575 — 20,720 fewer distinct
values (~16% consolidation). Spot-checked Amazon specifically: 54 distinct
raw `employer_name` values collapse into 22 distinct
`employer_name_normalized` values; the two example groups from the tests
collapse exactly as expected, and one intentionally-distinct case
(`Amazon.com Services LLC (Hong Kong Branch)`) correctly stays separate
since parenthetical branch text isn't stripped.

**Decided:** Suffix list is limited to the abbreviations explicitly
requested (`INC`/`LLC`/`LLP`/`LP`/`CORP`/`CO`/`LTD`/`PLLC`/`PC`) — no
full-word variants like `LIMITED` or `CORPORATION` were added, since
that wasn't asked for and would risk exactly the over-merging the
false-positive-risk test guards against (`INFOSYS LIMITED` must not
collapse toward something else). Chose to truncate/log nothing extra here
since `employer_name_normalized` shares `employer_name`'s 255-char column
width and normalization only ever shortens a name.

**Next:** Step 5's remaining piece — the FK from `lca_disclosures` to
`companies` — is still open. `employer_name_normalized` is now populated
for all rows and at insert time going forward, but no matching/linking
logic against `companies` exists yet.

---

## 2026-08-20 — Fuzzy company-name matching (Step 6)

**Did:** Added `find_matching_employers()` in
`src/huntloop/matching/fuzzy_match.py`, on top of Step 5's
`normalize_employer_name()`. Given a raw company name, it first checks the
new `sponsor_name_overrides` table for an exact (case-insensitive) match on
`raw_company_name` and short-circuits to that confirmed
`employer_name_normalized` mapping without running fuzzy matching at all;
otherwise it normalizes the query the same way LCA employer names are
normalized and fuzzy-matches it against the distinct
`employer_name_normalized` values in `lca_disclosures` via `rapidfuzz`
(added to `requirements.txt`), returning `EmployerMatch(employer_name_normalized,
score, source)` ranked by score. Added the `sponsor_name_overrides` table
(`raw_company_name` unique + `employer_name_normalized`) via
`alembic revision --autogenerate` + `alembic upgrade head`, same workflow
as the `lca_disclosures` migration — not linked to `companies` and not
populated with real curated entries yet, per scope; this step is only the
matching mechanism. Note: autogenerate also picked up unrelated
pre-existing schema drift between this DB and `db_models.py`
(`job_postings`/`job_skills`/`job_sources` column type/nullability/FK
changes) that predates this work; that drift was left out of the
generated migration file by hand rather than applied, since it's not part
of this change.

Tried `rapidfuzz.fuzz.WRatio` first and rejected it: its partial-ratio
component activates whenever one string is much shorter than the other,
and it scores "INFOSYS" vs. "A&A INFOSYSTEMS" at 90 - indistinguishable
from a true match, and exactly the false-positive risk the Step 1 audit
flagged. Switched to `token_set_ratio`, which still tolerates reordering
and extra legal-entity words (so "Amazon" matches "AMAZON WEB SERVICES"
and "AMAZON DEVELOPMENT CENTER US" at 100) but treats "INFOSYS" and
"INFOSYSTEMS" as different tokens, scoring "INFOSYS" vs. "A&A INFOSYSTEMS"
at only 63.6.

Chose `DEFAULT_THRESHOLD = 88` empirically, against real data pulled live
from the 1,431,321-row `lca_disclosures` table (not synthetic examples):
true company variants consistently score 100 once normalized (all of
Amazon's and Google's subsidiary/division names in the data); the audit's
flagged false positive ("INFOSYS" vs. "A&A INFOSYSTEMS") scores 63.6, ~24
points below threshold; the tightest real near-miss found while tuning -
two unrelated small companies sharing only a generic industry-suffix word
("Nous Infosystems" vs. "VNS Infosystems", sharing just "INFOSYSTEMS") -
tops out at 90.3. 88 sits in the gap between that near-miss cluster and
the 100-scoring true positives. It does not eliminate every
generic-suffix collision (e.g. querying "Tata Consultancy Services" also
returns several unrelated "... CONSULTANCY SERVICES" companies scoring
88.9-95.8, below the true match's 100 but still above threshold) - that
residual ambiguity for common industry-generic phrases is an accepted
limitation, not something a single global threshold should be tuned to
eliminate entirely; `sponsor_name_overrides` exists specifically to
correct cases like that by hand.

12 unit tests total: 8 in `tests/test_normalize.py` (unchanged from Step
5) and a new `tests/test_fuzzy_match.py` (7 tests, seeded against a
throwaway-schema `db_session`, not the real table) covering true-variant
ranking for Amazon and Google, the "INFOSYS" vs. "A&A INFOSYSTEMS"
false-positive-risk case, override short-circuiting (including
case-insensitive lookup), fallback-to-fuzzy when no override exists, and
an unrelated query returning no matches. All 17 project tests pass
(`test_normalize.py`, `test_fuzzy_match.py`, `test_pipeline.py`).

Live-verified against the real `lca_disclosures` table: `find_matching_employers`
for "Amazon" and "Google" returns only true subsidiary/division names, all
at 100.0; for "Infosys", all 10 top results score 100.0 and
`A&A INFOSYSTEMS` is confirmed absent from the results; for "Tata
Consultancy Services" (the messier/smaller real example), the two true
variants (`TATA CONSULTANCY SERVICES LIMITED` and a leading-digit OCR/typo
variant `1 TATA CONSULTANCY SERVICES LIMITED`) rank at 100.0, correctly
above every unrelated result.

**Decided:** Also discovered while autogenerating the migration: this same
local Postgres DB still has the `job_postings`/`job_skills`/`job_sources`
column drift noted above, present but not yet reconciled in a migration -
flagging it here since it'll surface again in any future
`alembic revision --autogenerate` until it's addressed on purpose, but
fixing it wasn't part of this task so it was deliberately left alone.

**Next:** Step 7 is applying `find_matching_employers()` to the actual
scraped `companies` table and curating real `sponsor_name_overrides`
entries. No FK from `lca_disclosures`/`sponsor_name_overrides` to
`companies` exists yet.

---

## 2026-08-20 — Shared logging configuration

**Did:** Added `src/huntloop/logging_config.py`: a single `setup_logging()`
that configures the root logger once (idempotent - later calls no-op) with
a consistent format (`timestamp [LEVEL] module.name: message`), a level
read from the `LOG_LEVEL` environment variable (default `INFO`), and two
handlers - console (`StreamHandler`) and a rotating file
(`logs/huntloop.log`, 5MB per file, 3 backups). Added `logs/` to
`.gitignore` (runtime artifact, not source, same treatment as
`data/raw/`).

Wired it into every module that previously did its own ad hoc logging
setup:
- `huntloop/settings.py` now calls `setup_logging()` at import time (before
  anything else, since `settings.py` is the first thing Scrapy imports)
  and sets `LOG_ENABLED = False` so Scrapy doesn't also configure its own
  competing root-logger handlers - without that, every scraper log line
  would print twice (once via Scrapy's own handler, once via ours) since
  both attach to the root logger. The old hardcoded `LOG_LEVEL = "DEBUG"`
  Scrapy setting is gone; level is controlled by the `LOG_LEVEL` env var
  read in `logging_config.py` instead.
- `huntloop/pipelines.py` and `huntloop/middlewares.py` already used
  `logging.getLogger(__name__)` with no setup of their own, so they
  needed no code change beyond emoji cleanup (below) - they pick up the
  shared config automatically once `settings.py` has run.
- `huntloop/spiders/greenhouse_spider.py`: added a module-level
  `logger = logging.getLogger(__name__)` and switched the one stray
  `logging.warning(...)` call (previously calling the logging module
  directly instead of a named logger) to use it. `self.logger` calls
  (Scrapy's built-in per-spider logger) were left as-is - idiomatic Scrapy,
  and they still propagate to the root logger our config controls.
- `scripts/ingest_lca_disclosures.py` and
  `scripts/backfill_employer_name_normalized.py`: replaced their own
  `logging.basicConfig(...)` calls with `setup_logging()` from the shared
  module.
- `src/huntloop/matching/normalize.py` and `fuzzy_match.py` do no logging
  today, so nothing needed changing there - noted since the task
  description named them explicitly.

Removed emoji prefixes from log messages in `pipelines.py`
(`✅ Inserted job`, `❌ Integrity error`, `❌ Unexpected error`,
`🚀 [DEBUG] ...`, `🚀 [PIPELINE TRIGGERED] ...`, `⚠️ Skipping item`,
`🟡 Skipping reposted job`, `🛑 JobDataPipeline closed`) and
`greenhouse_spider.py` (`🧩 Yielding job item`), leaving the text and log
level of each call otherwise untouched - purely a formatting change, not a
logic change. Left `src/huntloop/test_db_insert.py` (a manual, non-pytest
smoke-test script) and inert emoji in code comments (not log messages)
alone - not part of what the task named, and out of scope.

Verified: ran `python main.py` (scraper) and
`scripts/backfill_employer_name_normalized.py` (ingestion-family script) -
both produced the same consistent format from different loggers
(`huntloop.pipelines`, `scrapy.core.engine`, `__main__`), and both were
also written to `logs/huntloop.log`. Confirmed `LOG_LEVEL=WARNING`
suppresses INFO-level output (demonstrated directly against
`logging_config.setup_logging()`: default level logs both an INFO and a
WARNING line; with `LOG_LEVEL=WARNING` set, only the WARNING line appears,
in both console and file). Full pytest suite (17 tests) still passes
unchanged.

**Decided:** Setting `LOG_ENABLED = False` in Scrapy's settings was
necessary, not optional - it's the only way to let one shared config own
all logging (Scrapy's own internal log lines included, since they're
standard Python loggers that still propagate to the root logger we
configure) without doubled output. Chose not to touch
`test_db_insert.py` even though it has the same ad hoc
`logging.basicConfig()` + emoji pattern, since it wasn't in the task's
named module list and touching it wasn't requested.

**Next:** Nothing logging-related outstanding. `test_db_insert.py` still
has its own ad hoc logging setup if a future session wants to fold it in.

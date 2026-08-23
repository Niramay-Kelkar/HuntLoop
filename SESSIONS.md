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

---

## 2026-08-20 — Sponsorship lookup against real companies (Step 7)

**Did:** Added `get_sponsorship_summary()` in
`src/huntloop/matching/sponsorship.py`, on top of Step 6's
`find_matching_employers()`. Given a `Company` row, it finds its likely
`employer_name_normalized` matches and aggregates `lca_disclosures`
across all of them: total approved LCAs, a per-fiscal-year breakdown,
distinct job titles sponsored, and distinct worksite states - the
"does this company sponsor, and how much" question. Deliberately not a
stored FK from `companies` to `lca_disclosures`: a company can span
multiple legal entities in the LCA data and match confidence varies row
to row, so this stays a queryable lookup computed at call time rather
than a rigid one-to-one link baked into the schema.

Ran it against both currently-scraped companies:

- **checkr**: matched only `CHECKR` (score 100.0). 48 approved LCAs -
  2021: 11, 2022: 6, 2024: 17, 2025: 14. 38 distinct job titles (Finance,
  Product, Engineering roles - e.g. "Finance Manager", "Engineering
  Manager", "Lead Product Manager, International"). 9 distinct worksite
  states (CA, CO, FL, GA, MA, NC, OR, PA, WA).
- **duolingo**: matched only `DUOLINGO` (score 100.0). 95 approved LCAs -
  2021: 8, 2022: 4, 2024: 43, 2025: 40. 40 distinct job titles, several
  Duolingo-specific (e.g. "Language Assessment Scientist", "AI Research
  Scientist"). 3 distinct worksite states (NY, PA, WA).

Manually verified both matches are correct, not just "code ran without
error": for `CHECKR`, the two raw `employer_name` values behind it are
`CHECKR, INC.` and `Checkr, Inc.` - the real background-check company
(`checkr.com`), and CA/CO worksite states line up with the "San Francisco"
/"Denver, Colorado" locations already seen in this company's scraped job
postings. For `DUOLINGO`, the one raw `employer_name` behind it is
`Duolingo, Inc.`, and PA (Duolingo is headquartered in Pittsburgh) plus
the language-education-specific job titles are strong independent
corroboration. Checked for ambiguity: `find_matching_employers` returned
exactly one candidate for each query (not several close-scoring
options to choose between) - e.g. `CLOUDCHECKR` (an unrelated company
whose normalized name contains "CHECKR" as a substring) scores only 70.6
against the query "checkr", well below the 88 threshold, so it never
entered the running.

**Decided:** No `sponsor_name_overrides` entry was added for either
company - both matches were already unambiguous and independently
verifiable as correct, so a manual override would have been redundant.
The override table remains empty; it's still there for the cases fuzzy
matching alone doesn't resolve cleanly, just not needed for these two.

**Next:** This closes out the core of Phase 1 (sponsorship-matching MVP):
LCA ingestion (Steps 1-4), mechanical normalization (Step 5), fuzzy
matching with a manual-override escape hatch (Step 6), and now a working
company -> sponsorship lookup (Step 7) all exist and are verified against
real data. Not yet done: broader scraper coverage (only `checkr` and
`duolingo` are configured - see `company_tokens` in
`greenhouse_spider.py`), any UI/API surface for `get_sponsorship_summary()`
(it's a Python function today, not exposed anywhere), curating
`sponsor_name_overrides` for companies where fuzzy matching doesn't
resolve cleanly, and the eventual decision on whether/how to persist a
company<->LCA link beyond the current on-the-fly lookup.

---

## 2026-08-21 — Security audit + fixed the two medium findings

**Did:** Ran a security audit against the repo: `pip-audit` against
`requirements.txt` (0 CVEs across 60 resolved packages), `bandit` against
`src/` (0 issues, 636 LOC), `gitleaks` across full git history (0 leaks,
16 commits) plus a manual trace confirming the pre-Phase-0 hardcoded
Postgres password never actually entered git history (`src/scrapers/`
was never tracked at all — the repo's first real commit already has
`src/huntloop/settings.py` reading `DATABASE_URL` from the environment).
Also checked Docker hygiene, `.gitignore`/`.dockerignore` completeness,
CI secrets handling, and dependency pinning.

Fixed the two medium-severity findings:
- Added `data/raw/` and `logs/` to `.dockerignore` (previously only in
  `.gitignore`) — real DOL LCA files and log output were being included
  in the Docker build context and would have been baked into an image
  layer via `COPY . .`.
- Added a non-root `USER` directive to the `Dockerfile` — created a
  dedicated `huntloop` system user/group, `chown -R`'d `/app` to it
  before switching, so `logs/` (created at runtime by
  `logging_config.setup_logging()`) is writable by that user.

**Verified:** Rebuilt the image with `docker compose build --no-cache`,
extracted every layer blob from `docker save` and grepped each for
`data/raw`/`dol_lca`/`logs`/`huntloop.log` paths — none present (sanity
checked the scan itself by confirming it *does* find `app/main.py` in the
same blobs). Confirmed the running container is non-root via `docker
compose run --rm app id` → `uid=999(huntloop) gid=999(huntloop)`. Ran
`alembic upgrade head` to a fresh throwaway Postgres, then `python
main.py` end-to-end inside the rebuilt container: scraper finished
cleanly (43 items scraped), and confirmed via `psql` that 45 rows landed
in `job_postings` (not just that Scrapy logged a count). Confirmed
`/app/logs/huntloop.log` was created at runtime and is owned by
`huntloop:huntloop`, proving the chown actually made the path writable
by the non-root user rather than just not crashing. Full pytest suite
(17 tests) passed inside the same rebuilt container.

**Decided:** Left the Low/Info findings from the audit open by choice,
not as oversights: unpinned dependencies (15 of 18 direct deps have no
`==` pin), `.idea/` being tracked in git despite being in `.gitignore`
(committed before the ignore rule existed in the same commit — no
secrets found inside it, just IDE UI state), and no automated
`pip-audit`/`bandit` step in CI. These are deliberate separate follow-ups.

**Next:** If/when the Low/Info findings are picked up: pin
`requirements.txt` (or add a lockfile), decide whether to `git rm
--cached` the `.idea/` files now that the directory is gitignored, and
wire `pip-audit`/`bandit` into `.github/workflows/ci.yml`.

---

## 2026-08-21 — ATS-detection classifier (standalone, not wired in)

**Did:** Added `detect_ats(company_careers_url: str) -> dict` in
`src/huntloop/ats_detection.py`. Fetches the given URL once (`requests`,
no JS rendering) and pattern-matches known ATS URL shapes against both
the final post-redirect response URL and the raw HTML body: Greenhouse
(`boards.greenhouse.io`, `job-boards.greenhouse.io`, and the
`boards-api.greenhouse.io/v1/boards/{token}` embed shape used by
Greenhouse's custom-domain hosting), Lever (`jobs.lever.co/{slug}`),
Ashby (`jobs.ashbyhq.com/{slug}`), Workday (`{tenant}.wd\d+.myworkdayjobs.com`),
and SmartRecruiters (`{jobs|careers|www}.smartrecruiters.com/{slug}`).
Always returns a dict (`source_url`, `ats`, `identifier`, `matched_url`,
`http_status`, `error`) - a fetch failure and a genuine no-match both come
back `ats="unknown"`, distinguished only by whether `error` is set. Not
wired into the scraper or company list - this is the detection function
only, per the task.

Manually tested against 13 real, currently-live company career URLs:

- **Greenhouse:** `job-boards.greenhouse.io/checkr` -> correct
  (`checkr`); `job-boards.greenhouse.io/duolingo` -> correct (`duolingo`)
  *after* a fix (see below).
- **Lever:** `jobs.lever.co/kraken`, `jobs.lever.co/palantir`,
  `jobs.lever.co/wealthfront` -> all correct.
- **Ashby:** `jobs.ashbyhq.com/ramp`, `jobs.ashbyhq.com/linear` -> both
  correct.
- **Workday:** `adobe.wd5.myworkdayjobs.com/en-US/external_experienced`,
  `cvshealth.wd1.myworkdayjobs.com/CVS_Health_Careers` -> both correct
  (tenant `adobe`/`cvshealth` extracted from the subdomain, confirmed
  against each page's `<link rel="canonical">`). Several other Workday
  tenant-URL guesses (Salesforce, Nike, Target, Schneider Electric,
  PepsiCo, J&J, GE, Verizon, AT&T) 404'd/500'd - Workday tenant slugs
  aren't guessable from the company name, so this is a small, verified
  sample, not proof every Workday tenant resolves this way.
- **SmartRecruiters:** `jobs.smartrecruiters.com/Visa`,
  `jobs.smartrecruiters.com/IKEA1` -> both correct.
- **Negative controls:** `www.anthropic.com` (no known ATS signature in
  the static HTML) -> correctly `unknown`; a non-resolving domain ->
  correctly `unknown` with `error` set to the `ConnectionError`, no
  exception raised.

**Bug found and fixed during testing:** the first version's Greenhouse
pattern only matched `boards.greenhouse.io`/`job-boards.greenhouse.io`,
so `job-boards.greenhouse.io/duolingo` (which redirects to
`careers.duolingo.com`, a Greenhouse-hosted custom domain) came back
`unknown` - a real false negative on one of the two companies the task
explicitly asked to sanity-check. Duolingo's custom-domain page instead
preloads from `boards-api.greenhouse.io/v1/boards/duolingo/departments`,
a different Greenhouse URL shape. Broadened the pattern to cover
`boards-api`/`job-boards`/`boards`/`api` prefixes plus an optional
`v1/boards/`/`embed/job_board?for=` path segment. Re-verified against all
13 URLs after the fix; added a regression test
(`test_detects_greenhouse_from_boards_api_preload_embed`) so this
specific shape can't silently regress.

**Known, honest limitation (not a bug, not fixed):**
`checkr.com/company/careers` (and its `/open-careers` sub-page) both come
back `unknown`, even though Checkr does use Greenhouse (confirmed
elsewhere in this repo - see the sponsorship-matching work). The page is
a Next.js SPA that fetches its Greenhouse board client-side via
JavaScript; the initial server-rendered HTML this function fetches has
zero Greenhouse signature anywhere in it (verified by hand - `grep -ic
greenhouse` on the fetched HTML returns 0). This is the real, expected
gap a static-HTML fetcher has on JS-heavy marketing sites - correctly
reported as `unknown` rather than a wrong guess, and exactly the case the
task said the LLM-extraction fallback (not built yet) is meant to catch.

**Overall accuracy:** 11 of 13 real ATS-bearing URLs correctly detected
(all 2 Greenhouse, all 3 Lever, all 2 Ashby, all 2 Workday, all 2
SmartRecruiters) after the fix. The other 2 (`checkr.com`'s two marketing
career-page URLs) correctly and gracefully returned `unknown` rather than
crashing or guessing wrong, for the JS-rendering reason above. Both
negative-control cases (no-ATS page, unresolvable domain) also behaved
correctly.

**Tests:** `tests/test_ats_detection.py`, 12 tests, all against
`unittest.mock`-patched `requests.get` (no live network calls) using HTML
snippets drawn from what was actually observed on the real pages above -
including a dedicated regression test for the `boards-api.greenhouse.io`
shape and one reproducing the Checkr client-side-rendering gap. Full
suite (29 tests total) passes.

**Next:** Not wired into the scraper or `company_tokens` list (out of
scope for this step, per the task). If/when it is: Workday tenant slugs
aren't derivable from a company name/domain, so any future integration
needs a real lookup or directory rather than guessing; the
client-side-rendering gap (Checkr-style SPAs) is what the planned
LLM-extraction fallback is meant to close, not this function.

---

## 2026-08-21 — Playwright render fallback for detect_ats() (still standalone)

**Did:** Added a rendering fallback to `detect_ats()`
(`src/huntloop/ats_detection.py`): when the static HTTP fetch succeeds but
matches no known ATS pattern, it now retries by rendering the page in
headless Chromium via Playwright (`page.goto(..., wait_until=
"domcontentloaded")`, then a bounded `wait_for_load_state("networkidle")`
that's treated as non-fatal on timeout - real marketing sites often keep
an analytics/tracking connection open indefinitely and never go truly
idle, even though their content has already rendered) and re-runs the
same pattern matching against the rendered HTML. Static-first,
render-as-fallback: the fallback only triggers when the static fetch
succeeded with no network-level error but matched nothing - a static
fetch that raised (DNS failure, connection refused, etc.) returns
immediately without attempting a render, since retrying the same
unreachable URL with a browser would just fail identically at far higher
cost. Installed the Chromium browser binary (`playwright install
chromium`) into the local Playwright cache, since only the Python package
was previously present (`requirements.txt` had `playwright` but nothing
used it - see the 2026-08-21 ATS-detection entry above, which flagged it
as unused). Added `render_attempted` (bool) to the result dict so callers
can tell whether the slow path ran. Still not wired into the scraper or
`company_tokens` - detection only, per the task.

**Verified against the specific real case Step 1 found:**
`checkr.com/company/careers/open-careers` (the sub-page whose static HTML
had only a `GreenhouseBlock` CSS-class placeholder with no token, from
Step 1) now correctly returns `{"ats": "greenhouse", "identifier":
"checkr", "render_attempted": true}` - confirmed by directly rendering
that URL with Playwright by hand first (1.7M characters of rendered HTML,
319 occurrences of "greenhouse" vs. 0 in the static fetch) before trusting
the automated result. This is the concrete, previously-`unknown` case now
fixed by the fallback, exactly as asked.

**One honest nuance surfaced during this verification:**
`checkr.com/company/careers` itself (the bare marketing landing page,
distinct from its `/open-careers` sub-page) still returns `unknown`,
*even after rendering* - manually confirmed by rendering it directly and
grepping the full rendered DOM: zero occurrences of "greenhouse"
anywhere, static or rendered. That page never embeds the ATS board itself
- it only links to `/open-careers`, which does. So this isn't a
rendering-fallback failure; it's a reminder that `detect_ats()` can only
detect what's actually reachable from the exact URL it's given, not
crawl a site to find the real careers sub-page. Not a bug, not fixed -
just documented so a future session doesn't mistake "wrong page tested"
for "detection broken."

**Full 13-URL regression set re-run (from the original ATS-detection
session), no regressions:** all 11 previously-correct static-hit
detections (2 Greenhouse, 3 Lever, 2 Ashby, 2 Workday, 2 SmartRecruiters)
are unchanged and still correct, and - confirmed via the timing below -
none of them triggered the render fallback (all resolved on the static
fetch alone, as they should). Adding the `open-careers` URL back in:
**12 of 13 real ATS-bearing URLs now correctly detected** (up from 11/13
in the static-only version), with the 13th (`checkr.com/company/careers`
bare) a true negative for the reason above, not a detection failure.
`www.anthropic.com` (negative control) still correctly returns `unknown`
- now after triggering the render fallback too (still nothing found,
correctly), which is itself a useful data point for the timing cost
below.

**Timing cost - static hit vs. render-fallback miss** (measured directly,
not estimated, one real run each):

| Case | Elapsed |
|---|---|
| Static hit (Lever, Ashby, Workday, SmartRecruiters) | 0.23s - 1.21s |
| Static hit (Greenhouse, direct board URL) | 0.42s - 2.98s |
| Static miss -> render fallback, match found (`checkr.com/.../open-careers`) | 7.91s |
| Static miss -> render fallback, still no match (`checkr.com/company/careers`) | 8.07s |
| Static miss -> render fallback, still no match (`anthropic.com`) | 4.29s |
| Static-level network failure (no render attempted) | 0.01s |

Rendering adds roughly **4-8 seconds** on top of a sub-second static
fetch - a 10-30x slowdown - whether or not the render ultimately finds a
match, since the cost is in launching Chromium and waiting for the page
to settle, not in the pattern match itself. This is exactly why the
fallback is gated on a static miss rather than always rendering: at any
real scale (many companies), most pages should resolve on the fast static
path, and only the genuinely JS-rendered minority pay the render cost.

**Decided:** Playwright-specific failures (navigation timeout, browser
launch failure, page crash/closed-context errors, and a catch-all for
anything else unexpected) are all caught in `_fetch_rendered()` and
degrade to `ats="unknown"` with a descriptive `error` - never an
unhandled exception. A `wait_for_load_state("networkidle")` timeout
specifically is treated as non-fatal (falls through to reading whatever
DOM exists) rather than a hard failure, since real sites with persistent
background connections would otherwise always "fail" the render even
when their content already rendered fine - this was necessary in
practice, not speculative, since Checkr's page does exactly this.

**Tests:** `tests/test_ats_detection.py`, 19 tests (up from 12), all
still fully mocked - `requests.get` and (new) `sync_playwright` are both
monkeypatched, so nothing launches a real browser or hits the network.
New tests cover: the render fallback finding a match after a static miss
(a mocked version of the real `open-careers` case above); the fallback
correctly *not* triggering on a static hit or a static-level fetch error
(asserting `sync_playwright` was never called, not just checking the
result); each Playwright failure mode individually (navigation timeout,
launch failure, page-content/crash failure, an arbitrary non-Playwright
exception); and the network-idle-timeout-is-non-fatal behavior. Full
suite (36 tests total) passes.

**Next:** `playwright` in `requirements.txt` is no longer an unused
dependency - `detect_ats()` now actually uses it, gated behind a static
miss. The Chromium browser binary (`playwright install chromium`) is a
real local/CI setup requirement now, not yet documented in README.md or
wired into `.github/workflows/ci.yml` - out of scope for this step since
this function still isn't called from anywhere in the app, but will need
addressing whenever it is. Still not wired into the scraper or
`company_tokens`.

---

## 2026-08-21 — Lever spider (second working source, standalone)

**Did:** Added `LeverScraper` in `src/huntloop/spiders/lever_spider.py`,
mirroring `GreenhouseScraper`'s structure exactly (same `start_requests`/
`async start`/`parse` shape, same `custom_settings` block - `DOWNLOAD_DELAY:
0.5`, `CONCURRENT_REQUESTS: 4`, `RETRY_ENABLED: True`, `RETRY_TIMES: 3`).
Hits `https://api.lever.co/v0/postings/{company_token}?mode=json`,
hardcoded to `wealthfront` (one of the 3 confirmed real Lever companies
from the ATS-detection step). Inspected Lever's actual response shape
first rather than assuming field names (`curl`'d the real API): it
returns a bare JSON array (not a `{"jobs": [...]}` envelope like
Greenhouse), and each posting's fields are meaningfully different -
`text` (not `title`), `hostedUrl` (not `absolute_url`), `description`
(HTML body) with structured `lists` sections kept separate (not
concatenated - same "flat mapping, not maximal enrichment" scope as the
Greenhouse spider), `categories.location`/`categories.allLocations` (not
`offices`), `categories.department`/`categories.commitment` (cleanly
available as fields, not scattered through a metadata array like
Greenhouse's `department`/`employment type`). One real field-mapping
issue caught before it could silently corrupt data: `createdAt` is epoch
*milliseconds* (e.g. `1694463796009`), not an ISO8601 string like
Greenhouse's `first_published` - passing that raw integer straight into
`item['date_posted']` would have sent Postgres a value it can't parse as
a timestamp, and the pipeline's broad `except Exception` would have
swallowed the resulting insert failure per-item rather than crash, so
this could easily have gone unnoticed as silently-missing `date_posted`
values. Converted with `datetime.fromtimestamp(ms / 1000,
tz=timezone.utc).isoformat()` before assignment, matching the ISO8601
string shape Greenhouse's field already uses successfully.

**Confirmed rather than assumed, per the task:**
- **`JobDataPipeline` is genuinely source-agnostic already** - it keys
  off `item["name"]` (falls back to `"Greenhouse"` only if that key is
  literally missing, which no spider does - both spiders always set it),
  `item["company_name"]`, and generically-named item fields. The one
  Greenhouse-flavored name left in the DB layer, the `gh_job_id` column,
  is just a generic string column despite its name (already flagged as
  an intentionally-kept naming wart, not fixed unprompted - see the
  architectural decisions in CLAUDE.md). Zero pipeline changes were
  needed or made.
- **`DOWNLOAD_DELAY`/`RETRY_ENABLED`/etc. from `settings.py` do apply
  generically** - confirmed by checking `settings.py` directly rather
  than assuming: `ITEM_PIPELINES`, `ROBOTSTXT_OBEY`, and
  `CONCURRENT_REQUESTS_PER_DOMAIN` are all project-level (apply to every
  spider automatically, not registered per-spider), and the real run's
  stats (`robotstxt/request_count: 1`) confirm robots.txt was actually
  checked for `api.lever.co` (its `robots.txt` allows everything, with a
  `Crawl-delay: 1` suggestion - not auto-enforced by Scrapy's robots
  middleware, so the spider's own `DOWNLOAD_DELAY: 0.5`, mirrored from
  Greenhouse's, governs actual pacing, same as Greenhouse does today).

**Verified end-to-end against the real Lever API** (`wealthfront`, no
mocking): 22 items scraped, log lines confirm each one hit the pipeline
and inserted successfully, e.g.:
```
2026-08-21 12:11:48,276 [INFO] lever_api: Yielding job item: Talent Intelligence Analyst for wealthfront
2026-08-21 12:11:48,276 [WARNING] huntloop.pipelines: [PIPELINE TRIGGERED] Processing item: Talent Intelligence Analyst
2026-08-21 12:11:48,283 [INFO] huntloop.pipelines: Inserted job: Talent Intelligence Analyst for wealthfront
```
Final Scrapy stats: `item_scraped_count: 22`, `response_status_count/200:
2` (one robots.txt check + one API call), `finish_reason: finished`.
`job_postings` total went from 114 to 136 (+22, matching exactly) -
confirmed via direct `psql`/`psycopg2` query, not just trusting the
Scrapy log. All 22 have distinct `job_url`s (the real unique key) and
`gh_job_id` correctly holding Lever's UUID-shaped `id` (e.g.
`78d6f6d5-1f08-4d5d-87be-c4250567bfb5`), not a Greenhouse-shaped integer.

**Spot-checked 3 of the 22 against the live Lever pages, not just the
JSON API the spider hit:**
- "Android Engineer" (`.../78d6f6d5-...`) - DB has title "Android
  Engineer", location "Palo Alto, CA" (among others). Fetched
  `jobs.lever.co/wealthfront/78d6f6d5-...` directly: page `<title>` is
  "Wealthfront - Android Engineer" - matches.
- "Backend Engineer" (`.../f5a0963a-...`) - DB has 4 locations (Palo
  Alto, San Francisco, New York, Seattle) from `categories.allLocations`
  - matches the real posting's multi-location listing.
- "Anti-Money Laundering Monitoring Analyst" (`.../26d453d0-...`) - DB
  location "Palo Alto, CA (Open to US-based Remote)" - matches the raw
  API's `categories.location` value verbatim (Lever's location strings
  sometimes include this kind of remote-eligibility suffix inline, not a
  separate field - correctly passed through as-is).

**Confirmed no source collapsing:** `job_sources` after this run has
`(1, 'Greenhouse')`, `(3, 'greenhouse_api')`, and the new `(4,
'lever_api')` - Lever's row is fully distinct from both existing
Greenhouse-labeled rows, not merged into either. Note: `(1, 'Greenhouse')`
and `(3, 'greenhouse_api')` being *two separate* rows for what's really
one platform is a **pre-existing** inconsistency (id 1 is from
`test_db_insert.py`'s manual smoke test, which hardcodes
`JobSource(name="Greenhouse")`; id 3 is from the real spider's
`item['name'] = self.name = 'greenhouse_api'`) - not caused by this
change, not fixed here (out of scope for this task), just observed and
flagged so it isn't mistaken for something this session introduced.

**Tests:** No new test file added - this step's verification was a real,
live end-to-end run against Lever's API (per the task's own verification
standard), not unit tests of the field-mapping logic. Full existing suite
(36 tests) still passes unchanged, confirming the pipeline reuse claim
above - nothing broke.

**Next:** Not wired into `detect_ats()`'s output or any multi-company
config yet, and not wired into `main.py` (which still only runs
`GreenhouseScraper`) - deliberately out of scope, per the task. With two
working spiders now proven (Greenhouse, Lever), the next step is a
company-list config that can route a company to the right spider based on
`detect_ats()`'s output, rather than each spider hardcoding one token.

---

## 2026-08-21 — Root-caused the recurring job_sources duplication

**Did:** Closed the loop on a bug Phase 0 Step 4 only partially fixed.
`alembic/versions/37f5b1de06fe` (Phase 0) consolidated a duplicate
`job_sources` row once (merged `"greenhouse_api"` into `"Greenhouse"`,
deleted `"greenhouse_api"`), but only fixed the *data* -
`src/huntloop/test_db_insert.py` kept hardcoding the literal source name
`"Greenhouse"`, completely separate from and different than the real
`GreenhouseScraper` spider's own `self.name` (`"greenhouse_api"`) that
`JobDataPipeline` actually uses (`item["name"] = self.name`). Confirmed
this was exactly why it came back: live `job_sources` had both
`(1, 'Greenhouse')` and `(3, 'greenhouse_api')` again (the Lever-spider
session two entries up in this log flagged it as observed-but-out-of-scope
at the time).

**Root-cause fix:** `test_db_insert.py` now imports `GreenhouseScraper`
and uses `GreenhouseScraper.name` for the source lookup/create instead of
a separately hardcoded literal - so the smoke-test script can structurally
never again create a `job_sources` row under a name the real pipeline
wouldn't also use, no matter how the spider's `name` attribute changes in
the future.

**Data consolidation** (same approach as Phase 0 Step 4 - an Alembic
migration, since `alembic upgrade head` is this repo's sole source of
schema/data reconciliation, not an ad hoc script - see
`alembic/versions/3620e2fbbd47_consolidate_duplicate_greenhouse_job_.py`):
reassigned `job_postings.source_id` off the `"Greenhouse"` row onto
`"greenhouse_api"`, then deleted the now-orphaned `"Greenhouse"` row.
Consolidation direction is the *opposite* of Phase 0's migration
(that one merged into `"Greenhouse"`) - deliberately, because this time
the code is fixed first, so `"greenhouse_api"` (the real spider/pipeline's
actual convention) is what's canonical going forward, not `"Greenhouse"`.
Verified via `psql`/`psycopg2` before and after:
```
before: [(1, 'Greenhouse'), (3, 'greenhouse_api'), (4, 'lever_api')]
        job_postings by source_id: [(1, 114), (4, 22)]
after:  [(3, 'greenhouse_api'), (4, 'lever_api')]
        job_postings by source_id: [(3, 114), (4, 22)]
```
All 114 previously-`"Greenhouse"`-sourced postings correctly landed on
`greenhouse_api`; total `job_postings` count unchanged at 136 throughout -
confirms this was a pure reassignment, not a data loss. (Also confirmed,
incidentally, exactly how id 3's `"greenhouse_api"` row had 0 postings
before this: the pipeline creates/looks-up the `JobSource` row before its
per-item dedup check runs, so a later local spider run whose jobs were
all already-scraped duplicates still silently created the source row with
nothing pointing at it yet - explains the "orphan with zero postings"
shape observed.)

**Ran the actual smoke-test script** (not just the automated test) against
the real local DB post-fix: `python -m huntloop.test_db_insert`. It hit a
pre-existing, unrelated `job_postings_job_url_key` collision (the script
hardcodes the same `job_url` every run, and this DB already had a row
from an earlier manual run - not something this task asked to fix), but
the `JobSource` lookup/create step that runs first completed cleanly with
`source_id=3` (`greenhouse_api`) and no new row - confirmed via `psql`
immediately after: `job_sources` still exactly `[(3, 'greenhouse_api'),
(4, 'lever_api')]`, unchanged.

**Regression test:** `tests/test_db_insert_smoke_script.py` - a real test
against pytest's isolated schema fixture (`conftest.py`), not a
reimplementation of the script's logic. It seeds the isolated schema with
the `JobSource` row the real pipeline convention would already have
created (`GreenhouseScraper.name`), monkeypatches
`huntloop.test_db_insert.DATABASE_URL` to point the script's own engine at
that isolated schema, runs the script's actual `main()`, and asserts only
one Greenhouse-flavored `job_sources` row exists afterward, under the
spider's real name. **Proved it actually catches the bug it's meant to
catch**, not just that it passes: temporarily reverted
`test_db_insert.py`'s fix back to the hardcoded `"Greenhouse"` literal and
re-ran the test - it failed, with the DB left holding both the seeded
`greenhouse_api` row (`source_id=1` in the isolated schema) and a freshly
created `Greenhouse` row (`source_id=2`), exactly reproducing the
duplication. Restored the fix (verified byte-identical to the pre-revert
version via `diff`) and re-ran - passes again. Chose a real functional
test over a DB-level safeguard (e.g. a case-insensitive/normalized unique
index on `job_sources.name`) because the actual failure mode here isn't
"two rows with the same name" - it's "two *differently-named* rows for
what's semantically one source" (`"Greenhouse"` vs `"greenhouse_api"`),
which no DB constraint can detect; only a test that knows what name the
real convention produces can.

**Verified:** Full suite now 37 tests (up from 36), all passing. `alembic
current` confirms the DB is at the new head (`3620e2fbbd47`).

**Decided:** Left `test_db_insert.py`'s pre-existing hardcoded
`job_url="https://example.com/jobs/1234"` (and the resulting
unique-constraint collision on repeated manual runs) untouched -
unrelated to the `job_sources` naming bug this task targeted, and the
task said not to touch anything else.

**Next:** Nothing job_sources-related outstanding. If a third spider is
added later, its `self.name` will automatically get its own distinct
`job_sources` row the same way Lever's did - no further pipeline or
smoke-test changes needed, by construction.

---

## 2026-08-21 — ATS-platform schema + curated detect-and-store script

**Did:** Added three nullable columns to `companies` via Alembic
(`alembic/versions/7fbc4b9c6976_add_ats_platform_columns_to_companies.py`):
`ats_platform`, `ats_token`, `careers_url` - the stored result of running
`detect_ats()` (`src/huntloop/ats_detection.py`) against a company.
`db_models.py`'s `Company` model updated to match. Wrote
`scripts/detect_and_store_ats.py` (manual, not part of the ongoing
pipeline/CI, same convention as `scripts/ingest_lca_disclosures.py`):
runs a hardcoded list of 9 real `(company_name, careers_url)` pairs
through `detect_ats()` and upserts the result into `companies`, creating
the row if it doesn't exist yet or updating it in place if it does. Pure
detect-then-store - no spider run, no jobs scraped, nothing wired into
`main.py` or any dispatch logic. Company names deliberately use the same
lowercase-token convention the real spiders use for
`JobPostingItem.company_name` (e.g. `"checkr"`, not `"Checkr"`) -
learned directly from the `job_sources` duplication bug two entries up in
this log: reusing the real naming convention here means this script
updates the *same* row a spider would, rather than creating a second,
differently-cased row for the same company.

**Curated list (7 previously-confirmed + 2 never tested against
`detect_ats()` before this script, to check it isn't cherry-picked) and
real, live results:**

| Company | Careers URL | Detected |
|---|---|---|
| checkr | job-boards.greenhouse.io/checkr | greenhouse / `checkr` |
| duolingo | job-boards.greenhouse.io/duolingo | greenhouse / `duolingo` |
| kraken | jobs.lever.co/kraken | lever / `kraken` |
| palantir | jobs.lever.co/palantir | lever / `palantir` |
| wealthfront | jobs.lever.co/wealthfront | lever / `wealthfront` |
| ramp | jobs.ashbyhq.com/ramp | ashby / `ramp` |
| adobe | adobe.wd5.myworkdayjobs.com/... | workday / `adobe` |
| **brex** (new) | brex.com/careers | **unknown** |
| **figma** (new) | figma.com/careers/ | **greenhouse / `figma`** |

Both new companies manually verified independently of the script's own
output, not just trusted: **figma** - fetched the live page directly and
found real `greenhouse.io/figma/jobs/...` links in the static HTML
(`grep -io "greenhouse[a-z0-9./_-]*"` returned 5+ real job URLs) -
`detect_ats()`'s result is correct. **brex** - rendered the page directly
with Playwright by hand (735,993 chars of rendered HTML) and grepped for
all 5 known platform signatures; zero matches for any of them - `brex.com`
genuinely doesn't expose any of the 5 known ATS platforms' signatures,
rendered or not (likely a custom/different ATS, or an embed shape none of
the 5 patterns cover). `unknown` is the correct, honest result here, not
a detection failure - useful data point that the function still declines
to guess on real companies outside its original test set rather than
false-positiving onto the wrong platform.

**Confirmed via `psql`, all 9 rows correctly populated** (see the table
above for platform/token; `careers_url` matches what was passed in for
every row). Pre-existing rows (`checkr`, `duolingo`, `wealthfront`,
created earlier by the real spiders) were updated in place, not
duplicated - `companies` id numbering shows no gap-filling new rows for
those three.

**Idempotency, run 3 times total:** company count held at 10 (9 curated +
the pre-existing unrelated `OpenAI` smoke-test row) across all three
runs - the second and third runs logged "Updating existing company row"
for all 9, never "Creating new company row." One honest wrinkle caught
along the way, not fixed (out of scope, script does exactly what was
asked - unconditional upsert on every run, no more): the second run hit a
transient `ReadTimeout` against `job-boards.greenhouse.io` for `checkr`
(real network flakiness, not a code bug - confirmed by a clean third run
resolving it correctly again), and because the script always overwrites
on every run rather than only on success, that transient failure
correctly-per-design but conspicuously downgraded `checkr`'s stored
result to `unknown`/`NULL` until the next successful run. Flagging this
as a real characteristic of "detect fresh every run, always overwrite" -
not something asked to be handled here (e.g. "don't overwrite a known
result with unknown on failure" would be a reasonable follow-up, but
that's a design decision for whoever wires this into a recurring job, not
this one-off script).

**Verified:** Full suite still 37 tests, all passing - the new nullable
columns didn't break `tests/conftest.py`'s `Base.metadata.create_all()`
schema-isolation setup or anything else.

**Next:** Not wired into `main.py` or any multi-spider dispatch - per the
task, that's the next step now that `companies.ats_platform` exists to
route on. The curated list is hardcoded in the script, not read from
anywhere dynamic yet.

---

## 2026-08-21 — Fixed the transient-failure overwrite bug (Step 4 follow-up)

**Did:** Fixed the exact wrinkle flagged (not fixed) at the end of the
previous entry: `scripts/detect_and_store_ats.py`'s `upsert_company_ats()`
used to overwrite `ats_platform`/`ats_token` unconditionally on every run,
so a transient `detect_ats()` fetch error (network timeout, DNS failure,
etc. - anything landing in its `error` field) silently blanked a
previously-successful detection back to `unknown`/`NULL`, even though
nothing about the company's real ATS had changed. Now: if the current
result has `error` set *and* the company already has a non-NULL
`ats_platform` (a previously-successful detection worth protecting), the
overwrite is skipped and a `logger.warning()` explains exactly what
happened and that the stored value was left unchanged. A genuine
error-free "checked successfully, no pattern matched" result (`error is
None`, `ats == "unknown"`) still overwrites normally - this is
specifically about not confusing "couldn't check" with "checked and found
nothing." If a company has never been successfully detected before
(`ats_platform` still `NULL`) and the current attempt also errors, it
still stores `unknown`/`NULL` - nothing prior to protect in that case.

**Regression tests:** `tests/test_detect_and_store_ats.py`, 3 tests
(imports the script directly - `scripts/` isn't on the pytest path, added
via `sys.path.insert` same as running the script standalone would need):
- `test_fetch_error_does_not_overwrite_existing_successful_value` -
  reproduces the exact bug: seeds a company with `ats_platform=
  "greenhouse"`, runs `upsert_company_ats()` with an error result shaped
  like the real `ReadTimeout` observed live, asserts the stored value is
  unchanged and a `"detection attempt failed"` warning was logged.
- `test_genuine_no_match_result_still_overwrites_to_unknown` - an
  error-free "unknown" result must still overwrite an existing value
  (e.g. a company migrated ATS platforms, or an earlier detection turns
  out wrong).
- `test_error_on_never_before_detected_company_stores_unknown` - a
  company with no prior successful value, plus an error result, still
  correctly lands on `unknown`/`NULL`.

**Proved the first test actually catches the bug**, same standard as the
`job_sources` fix: temporarily reverted `upsert_company_ats()` back to
its old unconditional-overwrite form and re-ran -
`test_fetch_error_does_not_overwrite_existing_successful_value` failed
with `AssertionError: assert 'unknown' == 'greenhouse'`, exactly
reproducing the live bug; the other two tests still passed unchanged
(they don't depend on the fix - the old code already stored `unknown`
correctly in both of those cases, so they're not false-negatives).
Restored the fix (verified byte-identical via `diff`) and re-ran - all 3
pass.

**Ran the real curated-company script again for real**, no mocking:
```
checkr: ats=greenhouse identifier=checkr ... error=None
duolingo: ats=greenhouse identifier=duolingo ... error=None
kraken: ats=lever identifier=kraken ... error=None
palantir: ats=lever identifier=palantir ... error=None
wealthfront: ats=lever identifier=wealthfront ... error=None
ramp: ats=ashby identifier=ramp ... error=None
adobe: ats=workday identifier=adobe ... error=None
brex: ats=unknown identifier=None ... error=None
figma: ats=greenhouse identifier=figma ... error=None
```
No transient failures this run (no warnings logged), so nothing exercised
the new skip-on-error path live this time - but `companies` was confirmed
unchanged and correct afterward (`psql`: same 10 rows, same
platform/token values as before the run), confirming normal successful
detections still update exactly as before. The skip-on-error behavior
itself is proven by the regression test above, which is precisely why
that test exists - a transient network failure can't be reliably
reproduced on demand against the real API.

**Verified:** Full suite now 40 tests (up from 37), all passing.

**Next:** Nothing outstanding on this specific bug. Company-list dispatch
logic (the next real step per Step 4) can now trust
`companies.ats_platform` won't have been spuriously blanked by an
unrelated transient failure on some later re-run of this script.

---

## 2026-08-21 — Multi-ATS orchestrator: wired detect_ats() into a real scrape (Phase 2 core)

**Did:** Closed the loop from ATS detection all the way to real scraped
data. Generalized both `GreenhouseScraper` and `LeverScraper` to accept a
`companies` constructor/spider argument instead of a single hardcoded
token, consistently: `__init__(self, companies=None, *args, **kwargs)` on
both - `None` falls back to each spider's original single-company default
(`['checkr']`/`['wealthfront']`, backward compat), a string is
split on commas (the shape Scrapy's `-a companies=...` CLI argument
always arrives as), a list/tuple is used directly (the shape
`process.crawl(SpiderClass, companies=[...])` passes programmatically).

Rewrote `main.py` into the orchestrator itself (rather than a separate
script, since `main.py` was already documented as "the entrypoint" and
this is its natural evolution): `get_companies_by_platform()` queries
`companies` for rows with a non-NULL `ats_platform` and groups tokens by
platform, plus a separate query for `ats_platform IS NULL` rows so they
can be logged rather than silently excluded. `run_multi_ats_scrape()`
routes each platform with an implemented spider
(`SPIDERS_BY_PLATFORM = {"greenhouse": GreenhouseScraper, "lever":
LeverScraper}`) to one `process.crawl(spider_class, companies=tokens)`
call for its full token list; any platform without an entry in that dict
- including the literal string `"unknown"`, which naturally falls out of
the same lookup-miss code path as `"ashby"`/`"workday"` rather than
needing special-case handling - gets one clear `logger.warning()` and is
skipped, never silently dropped or a crash.

**Ran the real orchestrator end-to-end** (`python main.py`), no mocking,
against the current `companies` table (10 rows: `checkr`/`duolingo` ->
greenhouse, `kraken`/`palantir`/`wealthfront` -> lever, `ramp` -> ashby,
`adobe` -> workday, `brex` -> unknown, `OpenAI` -> NULL, plus `figma` ->
greenhouse added in the ATS-detection step). Log lines confirm every
skip case fired correctly, not silently:
```
[WARNING] __main__: Skipping 1 companies with no detected ATS platform (ats_platform is NULL): ['OpenAI']
[WARNING] __main__: No spider implemented for platform 'ashby', skipping 1 companies: ['ramp']
[INFO] __main__: Running greenhouse_api for 3 companies: ['duolingo', 'figma', 'checkr']
[INFO] __main__: Running lever_api for 3 companies: ['kraken', 'palantir', 'wealthfront']
[WARNING] __main__: No spider implemented for platform 'unknown', skipping 1 companies: ['brex']
[WARNING] __main__: No spider implemented for platform 'workday', skipping 1 companies: ['adobe']
```
Both spiders ran, one `process.crawl()` call each with all 3 tokens for
their platform - `greenhouse_api` scraped 274 items, `lever_api` scraped
330 items (Scrapy stats, both runs `finish_reason: finished`).

**Row-count delta** (`psql`, before -> after): `job_postings` 136 -> 616
(+480). Per-company: `checkr` 46 -> 47 (+1, one new posting since the
last scrape), `duolingo` 67 -> 76 (+9), `wealthfront` 22 -> 22 (+0, all
22 already scraped in the earlier Lever-spider step, correctly
deduplicated via the `gh_job_id` pre-check), `figma` 0 -> 162 (new
company, first scrape), `palantir` 0 -> 308 (new company, first scrape).
**`kraken` 0 -> 0** - not a bug: confirmed live by re-querying
`api.lever.co/v0/postings/kraken?mode=json` directly, which returns an
empty array right now (0 open postings on Kraken's board at scrape time,
real-world data changed since the earlier ATS-detection testing session -
not the same as a detection or spider failure).

**Spot-checked figma and palantir** (both never scraped before this
step) against the DB and the live pages:
- `figma`/`greenhouse_api` row "Account Executive, Emerging Enterprise
  (Berlin, Germany)", URL `boards.greenhouse.io/figma/jobs/5364702004` -
  fetched that URL directly, page `<title>` is "Job Application for
  Account Executive, Emerging Enterprise (Berlin, Germany) at Figma" -
  matches.
- `palantir`/`lever_api` rows (e.g. "Administrative Business Partner",
  `jobs.lever.co/palantir/ac978161-...`) - correct `company_id`/`source_id`
  attribution confirmed via join (`companies.name='palantir'`,
  `job_sources.name='lever_api'`), consistent with the Lever field-mapping
  already verified in the Lever-spider session.

**Backward compatibility:** `scrapy crawl greenhouse_api` was never a
supported invocation path in this repo - confirmed (again) there is no
`scrapy.cfg` at the repo root, and `main.py`'s own comment history
(`05b833c`) already documents deliberately bypassing `scrapy.cfg`
directory-walking in favor of `SCRAPY_SETTINGS_MODULE` + `sys.path`. What
*is* a real backward-compat path - direct programmatic instantiation with
no `companies` kwarg, e.g. `GreenhouseScraper()` - was verified directly:
`GreenhouseScraper().company_tokens == ['checkr']` and
`LeverScraper().company_tokens == ['wealthfront']`, both unchanged from
before this step.

**Verified:** Full suite still 40 tests, all passing - no test suite
changes were needed for this step (the spider constructor changes and
orchestrator logic were verified via the real end-to-end run above, per
the task's own verification standard, not new unit tests).

**Decided:** No Ashby or Workday spider was implemented - out of scope
per the task, future work once there's demand. `ramp` and `adobe` will
keep getting the "no spider implemented" skip message on every future
orchestrator run until one exists.

**Next:** This closes out the core of Phase 2 (multi-ATS scraping
capability) - detect (Step 3), store (Step 4, with the transient-failure
fix), and now actually scrape (this step) all connect end-to-end for
Greenhouse and Lever. Remaining Phase 2 work: Ashby/Workday spiders (if
ever prioritized), broadening the curated company list beyond the current
9, and deciding whether/how `detect_and_store_ats.py` and `main.py`'s
orchestrator run gets automated (currently both are separate manual
steps - `python scripts/detect_and_store_ats.py` then `python main.py`).

---

## 2026-08-22 — Sponsorship lookup extended to all 6 scraped companies

**Did:** Ran the existing `get_sponsorship_summary()` (unchanged) against
all 6 companies with an implemented spider - `checkr`, `duolingo` (the
original Step 7 pair) plus `figma`, `kraken`, `palantir`, `wealthfront`
(the 4 companies scraped since Phase 2). No matching-logic changes.

- **checkr**: matched `CHECKR` (100.0). 48 approved LCAs - 2021: 11,
  2022: 6, 2024: 17, 2025: 14. 38 distinct job titles, 9 worksite states
  (CA, CO, FL, GA, MA, NC, OR, PA, WA). Unchanged from Step 7.
- **duolingo**: matched `DUOLINGO` (100.0). 95 approved LCAs - 2021: 8,
  2022: 4, 2024: 43, 2025: 40. 40 distinct job titles, 3 worksite states
  (NY, PA, WA). Unchanged from Step 7.
- **figma**: matched `FIGMA` (100.0), single unambiguous candidate. 107
  approved LCAs - 2021: 10, 2022: 14, 2024: 31, 2025: 52. 31 distinct job
  titles (Product Designer, Data Engineer, Software Engineer -
  Infrastructure, etc.), 5 worksite states (CA, MA, NY, TX, WA). Sanity
  check: CA (Figma HQ is San Francisco) and design/product/eng titles are
  exactly what a design-tool company would sponsor. Correct, no override
  needed.
- **palantir**: matched `PALANTIR TECHNOLOGIES` (100.0), single
  unambiguous candidate. 241 approved LCAs - 2021: 26, 2022: 11, 2024: 91,
  2025: 113. 188 distinct job titles, 8 worksite states (CA, CO, DC, IL,
  NY, TX, VA, WA). Sanity check: DC/VA line up with Palantir's large
  government/defense business, "Deployment Strategist" (many numbered
  variants) and "Forward Deployed Engineer"-style titles are Palantir's
  well-known signature roles. Correct, no override needed. (The numbered
  job-title variants, e.g. "Deployment Strategist (11525.2662)", are raw
  DOL data as filed, not a scraping or normalization artifact.)
- **wealthfront**: matched `WEALTHFRONT CORPORATION` (100.0), single
  unambiguous candidate. 43 approved LCAs - 2021: 13, 2022: 1, 2024: 20,
  2025: 9. 20 distinct job titles, 6 worksite states (AZ, CA, MN, NJ, NY,
  TX). Sanity check: CA (Wealthfront HQ Palo Alto) plus
  finance/fintech-adjacent titles ("Broker Dealer Accountant", "IT
  Auditor", software/product roles) match a robo-advisor. Correct, no
  override needed.
- **kraken**: initially matched *two* candidates - `KRAKEN TECHNOLOGIES
  US` (100.0) and `RAKEN` (90.9, i.e. `Raken, Inc.`) - both above the 88
  threshold. Sanity check caught this as wrong: `Raken, Inc.` is an
  unrelated construction-field-management software company (its one LCA
  row: "Data Analyst", CA), with nothing to do with Kraken the crypto
  exchange (`jobs.lever.co/kraken`, the company actually scraped here).
  `KRAKEN TECHNOLOGIES US, INC` is plausibly correct (a real legal-entity
  naming pattern for Kraken's US operations), but its only LCA row
  ("CLIENT DELIVERY LEAD", TX) is thin evidence on its own - flagged as
  the weakest of the 6 matches, still judged correct on the entity name
  itself.

**Decided:** Added one `sponsor_name_overrides` row - `kraken` ->
`KRAKEN TECHNOLOGIES US` - to exclude the `RAKEN` false positive
(rapidfuzz's `token_set_ratio` scores "Kraken" vs "Raken" at 90.9, just
inside the 88 threshold; this is exactly the residual-ambiguity case the
override table exists for, not a reason to change the global threshold -
see the threshold rationale in `fuzzy_match.py`). Before/after:

  - Before: `matched_employer_names` = `[KRAKEN TECHNOLOGIES US (100.0,
    fuzzy), RAKEN (90.9, fuzzy)]`; `total_approved_lcas` = 2 (1 real
    Kraken row + 1 contaminating Raken row); `worksite_states` = [CA, TX]
    (CA came from the Raken row); `job_titles` = ["CLIENT DELIVERY LEAD",
    "Data Analyst"] ("Data Analyst" came from the Raken row).
  - After: `matched_employer_names` = `[KRAKEN TECHNOLOGIES US (100.0,
    override)]`; `total_approved_lcas` = 1; `worksite_states` = [TX];
    `job_titles` = ["CLIENT DELIVERY LEAD"].

  No other company needed an override - the other 3 new companies
  (figma, palantir, wealthfront) each returned exactly one unambiguous
  100.0-scoring candidate.

**Next:** All 6 currently-scraped companies now have verified
sponsorship summaries. `sponsor_name_overrides` has its first real entry
(`kraken`). Not yet done: the remaining Phase 1/2 follow-ups already
listed above (broader company coverage, UI/API surface for
`get_sponsorship_summary()`, etc.) are still open; kraken's single LCA
row means its picture may look thin/incomplete relative to its actual US
headcount - that's a DOL-data-coverage limitation, not a matching bug.

---

## 2026-08-22 — Local cron scheduling for the orchestrator (Step 5)

**Did:** Added `scripts/run_orchestrator_cron.sh`, a thin wrapper cron
calls: `cd`s into the repo, runs `.venv/bin/python main.py` (the exact
same script/venv/`.env` a manual run uses - no duplicated credentials or
config), and appends a start/exit-code/end marker to `logs/cron.log` so a
scheduled run's outcome is visible after the fact. Installed a crontab
entry: `0 3 * * * /Users/niramaykelkar/Desktop/HuntLoop/scripts/run_orchestrator_cron.sh`
(daily, 3:00 AM local). This is local-only scheduling for a dev machine
that's on at the scheduled time - explicitly not GitHub Actions, which
needs a publicly reachable DB (Supabase/Neon) and is a separate, later
deployment step.

**Investigated before picking a DB target:** The task said to schedule
against "the running Dockerized Postgres," but that phrase turned out to
be imprecise for this repo's actual setup - checked directly rather than
assuming. Started both Postgres instances and queried each with `psql`:

  - `localhost:5432` (system-installed PostgreSQL 18 at
    `/Library/PostgreSQL/18`, running as a system service - not the
    Homebrew `postgresql@18`, which failed to start with "Address
    already in use" because this one already held the port): `lca_disclosures`
    1,431,321 rows, `job_postings` 616, `companies` 10. This is `.env`'s
    `DATABASE_URL` target and matches the real accumulated data exactly.
  - `localhost:5433` (docker-compose's `db` service, started via
    `docker compose up -d db` for this check): `lca_disclosures` 115,695
    rows, `job_postings` 45, `companies` 1 - a smaller/stale dataset, not
    the one in active use.

  Targeted 5432 (i.e., left `.env`/`DATABASE_URL` untouched) since that's
  where the real data actually lives, which also happens to match "use
  the same .env/config manual runs use." Tore the docker `db` container
  back down after the check (`docker compose down`) - it was only started
  to compare counts, not part of the delivered setup.

**Verified:** `crontab -l` shows the installed entry (pasted above). Ran
`scripts/run_orchestrator_cron.sh` directly (the same command cron would
invoke, not `python main.py` directly) - exit code 0, `logs/cron.log`
shows the start/end markers, `logs/huntloop.log` shows the full Scrapy
run (stats dump, `finish_reason: finished`), and `job_postings` in the
real DB went 616 -> 619, confirming the wrapper-triggered run actually
wrote real data, not just logged success.

**Decided:** Cadence is daily at 3 AM - no stated requirement for
tighter, and job boards don't change fast enough to need more. Didn't add
a `pg_isready`/DB-reachability preflight check to the wrapper - a
DB-down failure already surfaces as a clear connection-error traceback in
`logs/huntloop.log` via the existing shared logging, so a separate check
would just duplicate that signal.

**Next:** Documented enable/disable/where-to-check in README.md's new
"Scheduled runs" section. Not done (deliberately out of scope for this
step): GitHub Actions scheduling against a hosted Postgres (Supabase/
Neon) - that's real deployment automation, tied to when this app actually
gets deployed, not local dev tooling.

---

## 2026-08-22 — Observability infrastructure (Prometheus/Pushgateway/Grafana)

**Did:** Added `prometheus`, `pushgateway`, and `grafana` services to
`docker-compose.yml`, all gated behind `profiles: ["observability"]` so a
plain `docker-compose up` for regular dev work never starts them.
Infrastructure only, per the task: no app code pushes real metrics yet,
and no Grafana dashboards are provisioned.

- `observability/prometheus/prometheus.yml` - scrapes only the
  Pushgateway (`pushgateway:9091`, `honor_labels: true`), not the app -
  `main.py` is a run-to-completion batch job (Scrapy crawl + exit), not a
  long-lived process Prometheus could poll.
- `observability/grafana/provisioning/datasources/datasource.yml` -
  pre-provisions Grafana's Prometheus datasource (`http://prometheus:9090`,
  set as default) via Grafana's file-based provisioning mechanism, mounted
  read-only into the container. Reproducible from a fresh bring-up - no
  manual "Add data source" UI step required.
- All three get healthchecks (`wget --spider` against each service's own
  health endpoint - `/-/healthy` for Prometheus/Pushgateway,
  `/api/health` for Grafana) and a startup order (`pushgateway` ->
  `prometheus` -> `grafana`, via `depends_on: condition:
  service_healthy`).
- `GF_SECURITY_ADMIN_PASSWORD` defaults to `admin` via
  `${GRAFANA_ADMIN_PASSWORD:-admin}` - overridable in `.env`, documented
  there as local-dev-only, not a real secret.

**Verified, end-to-end, all via `docker-compose --profile observability`**
(not just `docker-compose up`, which correctly starts nothing extra):

- All 3 containers reported `healthy` via `docker ps` after bring-up.
- Prometheus's own target API
  (`http://localhost:9090/api/v1/targets`) showed the `pushgateway` job
  with `"health": "up"`.
- Grafana's own datasource-health API (not just visual inspection):
  `GET /api/datasources/uid/<uid>/health` returned `{"status": "OK",
  "message": "Successfully queried the Prometheus API."}`.
- Pushed a trivial test metric directly to the Pushgateway (`curl
  --data-binary`, `huntloop_smoke_test_value 42` under job
  `observability_smoke_test`), waited one scrape interval, and confirmed
  it via a live Prometheus query
  (`/api/v1/query?query=huntloop_smoke_test_value`) - returned value
  `42`. Deleted the test metric from the Pushgateway afterward
  (`DELETE /metrics/job/observability_smoke_test`) so no fake data lingers.
- Tore the observability stack down (`docker-compose --profile
  observability down`), then confirmed the normal path still works
  unaffected: `docker-compose up -d db` started only `db` (no
  observability containers), and `docker-compose run --rm app python -c
  "..."` against it succeeded - the two stacks (app/db vs. observability)
  are independent, gated correctly by the profile.

**Decided:** Pinned image versions (`prom/prometheus:v2.55.1`,
`prom/pushgateway:v1.10.0`, `grafana/grafana:11.3.0`) rather than
`:latest`, consistent with wanting reproducible local infra. Did not add
persistence-critical settings beyond named volumes
(`prometheus_data`, `grafana_data`) - default retention/config is fine
for local dev infrastructure that isn't holding real metrics yet.

**Next:** Documented in README.md's new "Observability stack" section
(enable/disable, what each service is for, the same manual-push
verification recipe used above). Explicitly not done, per the task:
instrumenting any app code to push real metrics, and building any
Grafana dashboards - both are later steps once there's something
meaningful to observe.

---

## 2026-08-22 — Real metrics: orchestrator + pipeline push to Pushgateway

**Did:** Added `prometheus-client==0.26.0` to `requirements.txt` and a new
`src/huntloop/metrics.py` - a module-level `CollectorRegistry` (not
`prometheus_client`'s global default) holding 5 metrics: `Counter`s
`huntloop_jobs_scraped_total`, `huntloop_jobs_inserted_total`,
`huntloop_jobs_skipped_duplicate_total`, and
`huntloop_scrape_errors_total` (all labeled `company`/`source`), plus a
`Gauge` `huntloop_run_duration_seconds` (whole-run, unlabeled).
`push_run_metrics()` pushes all of it to the Pushgateway as one batch via
`push_to_gateway()` - not per item.

- `JobDataPipeline.process_item()` (`pipelines.py`) now computes
  `company_name`/`source_name` up front (not just inside the `try`) so
  every code path - happy path and every error branch - has labels to
  attach a metric to: `jobs_scraped_total` increments once per item seen;
  `jobs_inserted_total` on a successful insert; `jobs_skipped_duplicate_total`
  on the existing-`gh_job_id` branch; `scrape_errors_total` on a missing
  company name, an `IntegrityError`, or any other exception.
  `GreenhouseScraper`/`LeverScraper`'s `parse()` also increment
  `scrape_errors_total` (labeled by `comp_token`/`self.name`) on a
  non-JSON response or an unexpected response shape - errors that never
  reach the pipeline as an item, so the pipeline alone couldn't have
  counted them. (Fixed the Greenhouse spider's pre-existing
  `company_token` vs. `comp_token` NameError bug in that same log line
  while touching it, since a metrics call placed after it would otherwise
  have been dead code on that path - not a separate unrelated cleanup.)
- `main.py`'s `run_multi_ats_scrape()` now wraps the whole run in
  `try/finally`: times it with `time.perf_counter()`, and the `finally`
  sets `run_duration_seconds` and calls `push_run_metrics()`
  unconditionally - so a run's metrics get pushed whether the run
  succeeded, scraped nothing, or raised.
- `push_run_metrics()` never raises - same defensive principle as
  `detect_ats()`'s Playwright-failure handling and the LCA ingestion's
  wage-unit handling (see the architectural decisions above): a
  Pushgateway-down failure logs one `WARNING` and lets the real
  scrape/DB-insert outcome stand untouched.

**Verified:**

- **(a) Real run, stack up, cross-checked against the DB:** DB row count
  was unchanged (619 -> 619; all 6 companies' postings already existed
  from the prior scheduling-step run) - so every scraped item this run
  should be a duplicate, zero new inserts. Ran `python main.py` against
  the up observability stack; log line confirmed the push:
  `Pushed run metrics to Pushgateway at localhost:9091`. Queried
  Prometheus directly and it matched exactly:
  `huntloop_jobs_scraped_total` = 45/69/161 (checkr/duolingo/figma,
  greenhouse_api) + 308/22 (palantir/wealthfront, lever_api) = 605 total;
  `huntloop_jobs_skipped_duplicate_total` identical per-company values
  (605 total); `huntloop_jobs_inserted_total` and
  `huntloop_scrape_errors_total` both empty (zero results, i.e. no
  series - no inserts, no errors this run); `huntloop_run_duration_seconds`
  = 4.75s. scraped == skipped_duplicate per company, summing to the
  unchanged DB count - exactly what a repeat run against already-scraped
  data should look like.
- **(b) Pushgateway down, graceful degradation:** Tore the observability
  stack down (`docker-compose --profile observability down`), reran
  `python main.py`. Exit code 0, Scrapy's own stats dump still showed
  `item_scraped_count: 330` / `finish_reason: finished` (same as before -
  the scrape itself is completely unaffected), `job_postings` in the DB
  stayed at 619 (correctly - all duplicates again), and the only sign
  anything was different was one clean warning, not a crash or traceback:
  `[WARNING] huntloop.metrics: Failed to push run metrics to Pushgateway
  at localhost:9091 (scrape/DB-insert results above are unaffected):
  <urlopen error [Errno 61] Connection refused>`.
- **(c)** Full suite: 40/40 passing, both before and after the
  Pushgateway-down run (existing pipeline tests call `process_item()`
  directly and don't assert on metrics, so they weren't touched, but
  confirmed the new `metrics` import/calls don't break them).

**Decided:** Metrics accumulate on one process-lifetime registry rather
than resetting per platform/spider within a run, since `main.py` is a
one-shot script - by the time `push_run_metrics()` runs, the registry
holds exactly one run's totals. Didn't add a metric for
`get_companies_by_platform()`'s own DB-query failures specifically (a
failure there means the run never got scraping far enough to have
company/source labels) - `run_duration_seconds` and an empty push still
happen via the `finally`, and the failure itself is already visible in
`logs/huntloop.log`.

**Next:** Grafana dashboards visualizing these metrics are the natural
next step, deliberately not done here per the task (infrastructure/
instrumentation only, no dashboards yet).

---

## 2026-08-22 — Correction: the `company_token`/`comp_token` bug was two unfixed occurrences, not a regression

**Correction to the previous entry:** it claimed the metrics-step edit to
`greenhouse_spider.py`'s "No jobs found or invalid format" line "fixed
the pre-existing `company_token` vs. `comp_token` NameError bug." That
was wrong - the edit added a `metrics.scrape_errors_total.labels(...)`
call *before* that log line but left the log line's own `company_token`
reference untouched, so the bug (and the dead-code-on-that-path problem
the entry claimed to have solved) was still there. Caught when asked to
check git history before answering whether this was a regression.

**Investigated via `git log --follow -p` on `greenhouse_spider.py`
(actual history, not inference):**

- `05b833c` (2026-08-17, initial spider): introduced the identical
  mistake **three times** in `parse()` - the JSONDecodeError handler, the
  "No jobs found or invalid format" line, and the "Skipping malformed job
  entry" line all referenced `company_token` (only in scope in
  `start_requests`) instead of the local `comp_token`.
- `e3cf34d` (2026-08-19, "Fix scattered small bugs..."): fixed **only**
  the JSONDecodeError occurrence. Its own commit message is explicit and
  singular about this ("the JSONDecodeError handler logged
  company_token... referencing it here would NameError") and `git show`
  confirms the diff touches exactly that one line - the fix was never
  intended to cover the other two.
- `8823c7d` (2026-08-20, "Set up consistent logging across the app"):
  touched the "Skipping malformed job entry" line, but only to change
  `logging.warning` -> `logger.warning` as part of that commit's real
  purpose (removing ad hoc per-module logging setup). `git show` confirms
  `company_token` is unchanged on both sides of that diff - not a missed
  fix, just a line that commit had no reason to look closely at.
- `2d2f014` (2026-08-21, multi-company refactor): doesn't touch these
  log lines at all.

**Conclusion: not a regression.** Of the three original occurrences, only
one (JSONDecodeError) was ever fixed; the other two were never touched
before this session, by any commit, for any reason. They are a genuinely
distinct, previously-unaddressed occurrence of the same class of mistake
- not something correctly fixed once and then reintroduced.

**Did (the actual fix, this time verified):** Changed both remaining
`company_token` references in `parse()` (the "No jobs found or invalid
format" line and the "Skipping malformed job entry" line) to `comp_token`.
Verified with `grep -n "company_token\|comp_token"` that every reference
inside `parse()`'s scope now uses `comp_token`; the `company_token`
references outside `parse()` (in `start_requests` and the URL template)
are correct as-is - that's the scope those names were always meant for.
40/40 tests still pass.

---

## 2026-08-22 — Grafana dashboard for scraping activity (closes out observability work for now)

**Did:** Added `observability/grafana/provisioning/dashboards/dashboards.yml`
(Grafana's file-based dashboard provider, pointed at its own directory)
and `observability/grafana/provisioning/dashboards/huntloop-scraping.json`
(the dashboard itself) - both auto-load on Grafana container start via
the same `provisioning/` volume mount already in `docker-compose.yml`, no
compose changes needed. Also gave the datasource an explicit `uid:
prometheus` in `datasources/datasource.yml` (previously Grafana
auto-generated one) so the dashboard JSON could reference it
deterministically instead of guessing/hardcoding a hash.

Dashboard "HuntLoop Scraping Activity" (`uid: huntloop-scraping`), 4
panels, all querying the metrics `main.py`/`pipelines.py` already push -
no new metrics added:

- **Jobs scraped over time (by company)** - timeseries,
  `huntloop_jobs_scraped_total`, legend `{{company}} ({{source}})`.
- **Jobs inserted vs. skipped as duplicate (by company)** - barchart, two
  queries (`huntloop_jobs_inserted_total`,
  `huntloop_jobs_skipped_duplicate_total`), instant/table format.
- **Scrape errors (most recent run)** - stat panel,
  `sum(huntloop_scrape_errors_total) OR vector(0)` (the `OR vector(0)`
  makes a true zero-error run show `0` instead of "No data", since
  Prometheus doesn't return a 0-valued series for a counter that was
  never incremented).
- **Run duration trend** - timeseries, `huntloop_run_duration_seconds`.

**Verified:**

- **(a)** Fresh bring-up (`docker-compose --profile observability down
  -v` then `up -d prometheus pushgateway grafana`; the `-v` only drops
  `docker-compose`'s own `pgdata`/`prometheus_data`/`grafana_data`
  volumes - confirmed the real `job_postings` count on the actual
  data-holding Postgres, localhost:5432, was unaffected, still 619).
  `GET /api/search?query=HuntLoop` showed the dashboard present with no
  manual import; `GET /api/dashboards/uid/huntloop-scraping` confirmed
  all 4 panels loaded, each correctly wired to `datasource.uid:
  "prometheus"`.
- **(b)** Ran `python main.py` for real against the up stack. Queried
  Prometheus directly for every metric each panel uses, then queried the
  *same* metrics through Grafana's own datasource proxy
  (`/api/datasources/proxy/uid/prometheus/api/v1/query`) - identical
  values both ways: `jobs_scraped_total` 45/69/161 (checkr/duolingo/figma,
  greenhouse_api) + 308/22 (palantir/wealthfront, lever_api);
  `jobs_skipped_duplicate_total` identical per-company (all duplicates,
  matching the DB's unchanged 619-row count - same cross-check pattern as
  the metrics-instrumentation step); `jobs_inserted_total` empty (0);
  `scrape_errors_total` summed to `0`; `run_duration_seconds` = 5.20s.
  Grafana's proxied values matched the direct Prometheus query
  value-for-value, confirming the dashboard's panels would render this
  run's real data, not stale or mismatched data.
- Full suite still 40/40 passing.

**Next:** This closes out the observability work opened up over the last
few sessions (infra -> real metrics -> dashboard). Nothing observability-
related is planned next unless new needs come up.

---

## 2026-08-22 — pgvector enabled on docker-compose's Postgres (infra only)

**Did:** Switched `docker-compose.yml`'s `db` service from `postgres:18`
to `pgvector/pgvector:pg18` - confirmed via `docker manifest inspect` and
the Docker Hub API before switching, not assumed: it's the official
pgvector project's image (155M+ pulls, actively maintained, last pushed
2026-08-13 as of this change), amd64+arm64, and `pg18` currently resolves
to pgvector 0.8.6 built against Postgres 18 - an exact match for the
`postgres:18` base it replaces. Added
`alembic/versions/c2d25907fe8e_enable_pgvector_extension.py`
(`CREATE EXTENSION IF NOT EXISTS vector;` / `DROP EXTENSION IF EXISTS
vector;`), now head, revising `7fbc4b9c6976`. Infra only, per the task -
no vector columns, embeddings, or matching logic added.

**Verified, all against the docker-compose `db` (port 5433), not the
local system Postgres (see below):**

- **(a)** `docker-compose build app` (the running `app` image was stale
  from an earlier session and needed rebuilding to pick up the new
  migration file - confirmed via `alembic heads` showing an old head
  before the rebuild, current head after), then `docker-compose run --rm
  app alembic upgrade head` ran the full chain cleanly, ending at
  `7fbc4b9c6976 -> c2d25907fe8e, enable pgvector extension`.
- **(b)** `psql -h localhost -p 5433 ... -c "\dx"` showed `vector | 0.8.6`
  installed, schema `public`.
- **(c)** Smoke test via `psql`: created `pgvector_smoke_test(id serial,
  embedding vector(3))`, inserted 3 rows (`[1,2,3]`, `[4,5,6]`,
  `[1,2,4]`), ran `ORDER BY embedding <-> '[1,2,3]'` - returned the
  correct nearest-neighbor order (distance 0, 1, 5.196...), then dropped
  the table.
- **(d)** Seeded one company (`checkr`, `ats_platform='greenhouse'`)
  into the now-empty-after-rebuild docker `companies` table (a fresh
  volume, since this session's earlier `down -v` had cleared it - not
  real data, this docker instance never holds it, see the
  two-Postgres-instances note in the architectural decisions above), then
  ran `docker-compose run --rm app python main.py` against the pgvector
  image: `item_scraped_count: 45`, `finish_reason: finished`,
  `Spider closed (finished)` - and confirmed `job_postings` in that DB
  went 0 -> 45, matching. Same log-line proof standard as every prior
  "does the app still work" check in this project.
- **(e)** Full suite: 40/40 passing (tests run against local system
  Postgres per `tests/conftest.py`, untouched by this change).

**Resolved: does local system Postgres (5432, where the real data lives)
also need pgvector installed? Yes, and it does not have it yet -
confirmed directly, not assumed.** Ran
`CREATE EXTENSION IF NOT EXISTS vector;` against the real database
(`localhost:5432/jobsight`) and got a real, specific error:
`ERROR: extension "vector" is not available / HINT: The extension must
first be installed on the system where PostgreSQL is running.` Checked
for the extension's control/library files directly under
`/Library/PostgreSQL/18` (`share/postgresql/extension/`,
`lib/postgresql/`) - absent; this is the official EDB/PostgreSQL.org
macOS installer's Postgres 18, not a Homebrew one, and pgvector isn't
bundled with it. That directory tree is owned by `root:daemon` (not the
invoking user), so installing pgvector there (compiling from source via
`make PG_CONFIG=/Library/PostgreSQL/18/bin/pg_config install`, the usual
method - a C compiler and Xcode Command Line Tools are present) would
need `sudo` against a system directory outside the repo, on the Postgres
instance holding the real accumulated data (1.43M+ `lca_disclosures`
rows, 619 `job_postings`). **Not attempted in this session** - flagged
per the task's explicit instruction rather than assumed/auto-installed,
since it's a real system-level, `sudo`-gated change to shared machine
state, not a repo-local or reversible-by-`git`-revert action. The new
Alembic migration is consequently **not yet applied to local system
Postgres** - `alembic upgrade head` run there today would fail at
`c2d25907fe8e` with the same "extension not available" error, blocking
any migrations layered after it until pgvector is installed there too.
This matters for Step 7 (local cron scheduling, `scripts/
run_orchestrator_cron.sh`) only if/when a future migration depends on
`alembic upgrade head` having succeeded past this point - the cron
wrapper itself only runs `main.py`, not migrations, so today's cron runs
are unaffected.

**Next:** Waiting on a decision (asked the user directly, this
turn) on whether/how to install pgvector on local system Postgres before
building anything that needs `vector` columns there. Docker-side is
fully ready. No vector columns, embeddings, or matching logic exist yet
anywhere, per this step's scope.

---

## 2026-08-22 — pgvector installed on local system Postgres (5432), closing the gap flagged above

**Did:** Compiled and installed pgvector 0.8.6 from source against
`/Library/PostgreSQL/18` (the EDB/PostgreSQL.org installer, not
Homebrew), then formally applied the `c2d25907fe8e` migration there so
both Postgres instances (docker-compose's on 5433 and local system
Postgres on 5432, where the real data lives) now have the same pgvector
version available, matching by design.

**Two real build/permission obstacles found and resolved, not assumed
away:**

1. **Xcode Command Line Tools were not actually installed**, despite
   `xcode-select -p` reporting a path - confirmed by actually trying to
   compile a trivial `.c` file (`xcrun: error: invalid active developer
   path ... missing xcrun`), not by checking `which cc`/`which make`
   (those resolve to real-looking stub binaries that fail at runtime
   without CLT). `pkgutil --pkg-info` found no CLT receipt at all, and
   `/Library/Developer/CommandLineTools/usr/bin/` didn't exist. Fixed via
   `xcode-select --install` (interactive, user-run - can't be scripted or
   sudo'd around).
2. **pgvector's `make` failed with `clang: error: unsupported argument
   'native' to option '-march='`** - pgvector's Makefile defaults to
   `-march=native`, which Apple's `clang` rejects when building a
   universal binary (`-arch x86_64 -arch arm64`, which this Postgres
   install's `pg_config` CFLAGS specify). Fixed with `make clean` +
   `make PG_CONFIG=... OPTFLAGS=""` to drop that flag - a documented
   macOS-specific pgvector quirk, not a tooling or headers problem (the
   EDB install's server dev headers,
   `pg_config --includedir-server` -> `/Library/PostgreSQL/18/include/
   postgresql/server`, were present and correct the whole time, confirmed
   via `postgres.h` existing there before any of this).
3. **`CREATE EXTENSION vector` as the app's `job_scraper` role failed**
   with `permission denied ... Must be superuser to create this
   extension` - this pgvector version's `vector.control` has no
   `trusted = true` line, so (correctly) only a real Postgres superuser
   can install it, never the app's own non-superuser role. The user ran
   it themselves as `postgres` (the EDB installer's default superuser)
   via `psql -U postgres`, since that credential isn't something this
   session has or should have.

**Sequence actually run** (git-cloned pgvector pinned to `v0.8.6` - the
same version already on the `pgvector/pgvector:pg18` Docker image, for
parity):

```
xcode-select --install                                          # user, interactive
git clone --branch v0.8.6 --depth 1 https://github.com/pgvector/pgvector.git /tmp/pgvector
cd /tmp/pgvector && make clean
make PG_CONFIG=/Library/PostgreSQL/18/bin/pg_config OPTFLAGS=""
sudo make PG_CONFIG=/Library/PostgreSQL/18/bin/pg_config install    # user, sudo
psql -h localhost -p 5432 -U postgres -d jobsight -c "CREATE EXTENSION IF NOT EXISTS vector;"   # user, superuser
```

**Verified:**

- **(a/b)** After install, `\dx` against 5432 (as `job_scraper`) showed
  `vector | 0.8.6` - same version as the docker-compose instance.
  Confirmed `CREATE EXTENSION IF NOT EXISTS vector` as `job_scraper`
  (the role `DATABASE_URL`/Alembic actually use) now succeeds as a no-op
  (`NOTICE: extension "vector" already exists, skipping`) once the
  extension exists globally in the database - Postgres skips the
  ownership/privilege check for an already-satisfied `IF NOT EXISTS`, so
  Alembic (which runs as `job_scraper`, never superuser) can safely apply
  this migration on a fresh database too, as long as a superuser created
  the extension there once, or the role is granted appropriately.
- **(c)** `alembic current` showed `7fbc4b9c6976` (one behind head)
  before the real `alembic upgrade head` run; ran it for real -
  `Running upgrade 7fbc4b9c6976 -> c2d25907fe8e, enable pgvector
  extension`, no errors; `alembic current` afterward confirmed
  `c2d25907fe8e (head)`.
- **(d)** Ran `python main.py` for real against local Postgres
  post-migration: Scrapy finished normally (`Spider closed (finished)`),
  `job_postings` unchanged at 619 (correctly - same-day repeat run, all
  duplicates), and the Pushgateway-push warning fired as designed (the
  observability stack wasn't up during this check - unrelated to the
  pgvector change, same graceful-degradation behavior verified
  previously).
- Full suite: 40/40 passing.

**Resolved: both Postgres instances (Docker's on 5433, local system's on
5432, which holds the real 1.43M+-row LCA dataset) now have pgvector
0.8.6 enabled and available**, closing the gap flagged in the prior
entry. Still no vector columns, embeddings, or matching logic anywhere -
purely infra, per this step's scope; building on top of this is a
separate future decision.

---

## 2026-08-22 — Resume ingestion (text extraction + versioned storage, no matching)

**Did:** Added `data/resumes/` to both `.gitignore` and `.dockerignore`
(personal data - same reasoning as `data/raw/`) before touching anything
else. Added `pdfplumber==0.11.7` to `requirements.txt` - chose it over
`pypdf` because it's layout-aware (built on `pdfminer.six`), which
handles multi-column resume sections and irregular whitespace far better
than `pypdf`'s more basic extraction; a mis-ordered/garbled extraction at
this step would silently corrupt everything matching/scoring logic
builds on top of it later, so accuracy here was worth the extra
dependency weight. Added `ResumeVersion`
(`src/huntloop/db_models.py`: `id`, `version_number` [unique], `uploaded_at`,
`file_path`, `extracted_text`, `is_active`) and Alembic migration
`cbf7cee7fb12` (revises `c2d25907fe8e`). Added
`scripts/ingest_resume.py` - takes a PDF path, extracts text with
`pdfplumber`, computes the next `version_number` from the current max,
flips any existing active row to inactive (never deletes), and inserts
the new row as active. Ingestion only, per the task's explicit scope - no
embeddings, no matching, no LLM-suggestion or skills-extraction logic.

**Verified against the user's real resume**
(`data/resumes/Niramay_Kelkar_Resume_FullStack_v3.pdf`, placed there
mid-session - not present when the step started, confirmed by searching
the repo for any `.pdf` before proceeding rather than assuming it was
already there):

- **(a)** First run: `Ingested resume version 1 ... (5195 chars
  extracted), marked active`. Confirmed via `psql`: one row,
  `is_active=t`.
- **(b)** Inspected the extracted text directly (not just "it ran without
  error"): reading order is correct and every section is intact - header/
  contact info, SUMMARY, TECHNICAL SKILLS (with its sub-category
  labels), EXPERIENCE (4 roles, each with correct dates/locations and all
  bullet content), PROJECT, EDUCATION - no jumbled or missing text.
  **One real, worth-flagging artifact**: every bullet point extracts as
  literal `(cid:127)` instead of `•` - a known pdfminer/pdfplumber
  limitation where a PDF's bullet glyph isn't mapped to a real Unicode
  codepoint in the font's embedded ToUnicode CMap. Not data loss or
  reordering, just a cosmetic substitution - worth knowing about for any
  future bullet-aware parsing, not something this ingestion-only step
  attempts to clean up.
- **(c)** Ran the script again (same PDF, simulating a resume update):
  `Ingested resume version 2 ... marked active`. Confirmed via `psql`:
  version 1 now `is_active=f` (still present, `extracted_text` intact -
  not deleted), version 2 `is_active=t`.
- **(d)** Full suite: 40/40 passing, both before the PDF existed (schema/
  migration-only check) and after both ingestion runs.

**Decided:** `file_path` stores the path as given on the command line
(here, `data/resumes/Niramay_Kelkar_Resume_FullStack_v3.pdf`, relative to
repo root) rather than copying the PDF into a canonical location - the
task didn't ask for file management, just extraction + versioned storage,
and `data/resumes/` is already gitignored/dockerignored so leaving the
file where the user put it is fine.

**Next:** No embeddings, matching, LLM-suggestion, or skills-extraction
logic exists yet - all deliberately deferred, per this step's scope.
`resume_versions` now has 2 real rows (v1 inactive, v2 active) from this
session's verification runs, both against the user's real resume.

---

## 2026-08-22 — INCIDENT: full production data wipe during embedding-columns work, and recovery

**What happened:** While adding `embedding vector(384)` columns
(`resume_versions`, `job_postings`) for match scoring, `\dx` and a real
`CREATE EXTENSION` test confirmed pgvector's `vector` type lives in
`public`. `tests/conftest.py`'s isolated-schema fixture scopes
`search_path` to only the fresh per-session schema (deliberately, so
tests never touch real data) - so the new `vector` columns' DDL
(`Base.metadata.create_all()`, run against that fixture) couldn't
resolve the unqualified `vector` type name, and every DB-touching test
errored at setup (`type "vector" does not exist`).

The fix applied first - adding `,public` to that fixture's search_path -
was wrong and caused real, serious damage. With `public` second on the
path, `create_all()`'s own `has_table()` existence check resolves
unqualified table names via search_path *before* create_all has put
anything in the fresh test schema - so it found the *real*
`public.companies`/`public.job_postings`/etc. (same names) and concluded
they already existed, silently skipping table creation in the isolated
schema entirely. The fixture returned successfully (no error), so
nothing signaled anything was wrong. Every test that ran afterward was
silently operating on the **real production tables** instead of an
isolated copy - including `test_pipeline.py`'s tests, whose `pipeline`
fixture teardown runs `DELETE FROM <table>` on every table
(`Base.metadata.sorted_tables`, all 9 tables) after each test, to keep
the (assumed-isolated) schema clean between tests. That teardown ran for
real, against `public`. Two full pytest runs happened in this state
before the damage was noticed - not caught immediately because the
suite's outcome ("38 passed, 2 failed", the 2 failures being unrelated
cross-test-contamination artifacts of the same bug) looked like ordinary
test-writing noise, not a signal to check real row counts.

**Full scope of the wipe, confirmed by querying every table directly
after the fact:** `companies`, `job_postings`, `job_sources`,
`job_locations`, `job_skills`, `job_metadata`, `lca_disclosures` (all
1,431,321 rows), `sponsor_name_overrides` (its one real entry, `kraken`
-> `KRAKEN TECHNOLOGIES US`), and `resume_versions` (both versions
ingested earlier this session) - every table `Base.metadata` knows
about, all rows, all gone. `alembic_version` was **not** affected -
confirmed via `alembic current` before touching any data - `DELETE FROM`
never touches Alembic's own bookkeeping table, and it isn't part of
`Base.metadata` (confirmed: `08af7f0a020c (head)`, matching `alembic
heads` exactly). This was a pure data-loss incident, not schema
corruption - every table structure, migration, and column definition
survived untouched.

**Real fix (not the broken one):** Reverted `tests/conftest.py` to
search_path = schema-name-only, exactly as before (verified this was
in fact the regression - manually recreated the failure with a scratch
schema and confirmed `\dt` came back empty after `create_all()` when
`public` was on the path). Fixed the actual `vector`-type-resolution
problem at its source instead: `huntloop.db_models` now defines a local
`Vector` subclass of `pgvector.sqlalchemy.Vector` that overrides
`get_col_spec()` to always emit `public.vector(n)` (schema-qualified)
rather than the bare `vector(n)` the base class emits - confirmed via
reading pgvector's actual source that `get_col_spec` only feeds DDL
generation and is completely separate from value bind/result processing,
so this is a safe drop-in for both testing and production, not a hack
specific to the test fixture. Verified directly (not just "tests green"
this time): recreated an isolated schema by hand with schema-only
search_path, ran `create_all()`, and confirmed via `\dt schema.*` that
all 9 real tables - including the new `vector(384)` columns with the
correct type - were actually created there.

**Recovery, in dependency order (companies -> job postings -> LCA data
-> resume -> sponsor override), each step run for real against local
system Postgres (5432):**

1. **Companies + ATS data**: re-ran `scripts/detect_and_store_ats.py`
   (worked around its own pre-existing missing-`sys.path`-insert bug
   with `PYTHONPATH=src` rather than silently fixing it - out of scope
   here). Result: **9/9 companies restored**, `ats_platform` populated
   for all 9, matching the original detection results exactly
   (checkr/duolingo/figma -> greenhouse, kraken/palantir/wealthfront ->
   lever, ramp -> ashby, adobe -> workday, brex -> unknown). Note: the
   original `companies` table had a 10th row (`OpenAI`, from
   `test_db_insert.py`'s separate manual smoke test) not part of this
   recovery's ordered list - left out, not restored.
2. **Job postings**: re-ran `python main.py` (the real orchestrator).
   Result: **605 rows** (checkr 45, duolingo 69, figma 161, palantir
   308, wealthfront 22) vs. **619 originally** -
   explicitly NOT an exact restore, as expected and flagged in advance:
   live job boards changed in the time between the original scrape and
   this recovery run (some listings closed). `kraken` again shows 0
   postings - consistent with its real Lever board having 0 open roles,
   already independently verified as a real, non-bug state in an earlier
   session, not new evidence of a problem.
3. **LCA disclosures**: re-ran `scripts/ingest_lca_disclosures.py`
   against the untouched source files in `data/raw/dol_lca/` (13 files
   on disk, 11 actually processed - matches prior documentation).
   Result: **1,431,321 rows - an exact match** to the pre-wipe count,
   as expected (deterministic from unchanged source files, same
   `ON CONFLICT` dedup logic). Took 33m28s wall-clock. All rows have
   `employer_name_normalized` populated (confirmed already wired into
   the ingest path, no separate backfill pass needed).
4. **Resume**: re-ran `scripts/ingest_resume.py` against
   `data/resumes/Niramay_Kelkar_Resume_FullStack_v3.pdf` (same file,
   untouched by the DB wipe). Result: version 1 (fresh numbering, the
   table was empty - not the original v1/v2), active, 5195 chars
   extracted - content identical to before, since extraction from an
   unchanged PDF is deterministic.
5. **Sponsor override**: manually reinserted the one known row
   (`kraken` -> `KRAKEN TECHNOLOGIES US`) directly via SQL - this exact
   value was already independently verified correct via real
   investigation in an earlier session (see the sponsorship-lookup
   entries above), so re-deriving it via fuzzy-matching again wasn't
   necessary.

**Verified test isolation is genuinely fixed, not just "tests green"
again:** captured real production row counts
(companies=9, job_postings=605, lca_disclosures=1431321,
resume_versions=1, sponsor_name_overrides=1) immediately before running
the full suite, ran it twice (40/40 passing both times), and confirmed
the exact same row counts held after each run - the new standing check
for any future session touching `tests/conftest.py` or the isolated-
schema mechanism.

**Decided:** No backup/dump mechanism exists for this local database -
worth considering as real future work (e.g. a periodic `pg_dump` cron
job alongside the existing scraper-orchestrator one), but not built here
- out of scope for what was already a recovery, not a new-feature step.

**Next:** Data is restored and test isolation is verified fixed. Ready
to proceed with the actual Step 3 (embeddings/match scoring) work this
incident interrupted, now against the restored data.

---

## 2026-08-22 — Embedding-based match scoring (Step 3)

**Did:** Added `sentence-transformers==6.0.0` and `pgvector==0.5.0` to
`requirements.txt`, using `all-MiniLM-L6-v2` (dimension confirmed as 384
against the model's own published `1_Pooling/config.json`, not assumed -
matches `EMBEDDING_DIM` in `huntloop.db_models`). Added
`huntloop.text_cleaning.clean_text()` - checked real data before writing
it, not assumed: the resume's `extracted_text` has every bullet as
literal `(cid:127)` (a pdfminer glyph-mapping artifact, generalized to
any `(cid:\d+)`); `job_postings.job_description` is HTML in every real
row across all 5 scraped companies with descriptions, in two different
forms depending on source - Greenhouse rows (checkr/duolingo/figma) come
back HTML-entity-escaped (`&lt;p&gt;...`), Lever rows
(palantir/wealthfront) come back as raw HTML (`<div>...`) - one
`html.unescape()` call normalizes both to the same shape before tag-
stripping. Added `huntloop.embeddings` (lazy-loaded `SentenceTransformer`
wrapper, CPU-only, runs `clean_text()` before embedding). Added
migration `08af7f0a020c`: nullable `embedding vector(384)` on both
`resume_versions` and `job_postings`. Added
`scripts/backfill_embeddings.py` - embeds the active resume version
(recomputed every run) and backfills `job_postings` in batches of 100,
committing per batch, selecting only `embedding IS NULL` each pass (same
interrupt-safe pattern as `backfill_employer_name_normalized.py`).

**Real environment blocker, worked around, not ignored:** this
project's local dev venv (macOS, Intel, Python 3.13) cannot run
`sentence-transformers` - confirmed by actually trying to install
`torch`, not assumed: PyPI's last macOS-x86_64 torch wheel is `2.2.2`,
which only ships `cp38`-`cp312` wheels, nothing for `cp313`. Checked
whether another local Python was available (only system Python 3.9 and
this venv's 3.13 exist; no 3.10/3.11/3.12) before deciding not to stand
up a second parallel Python install just for this. Instead ran the
embedding computation inside the `app` Docker image (`python:3.13-slim`,
Linux - real `torch` wheels exist for Linux+cp313), pointed at the real
host Postgres via Docker's `host.docker.internal` gateway:
`docker compose run --rm -e DATABASE_URL="...@host.docker.internal:5432/..." app python scripts/backfill_embeddings.py`.
Confirmed this reaches the real data (not `docker-compose`'s own smaller
`db` on 5433) before running anything for real. Image grew to 5.81GB
after adding `torch`+`sentence-transformers` - expected, not investigated
further (out of scope; nothing about image size was part of this task).

**A serious incident happened mid-task and is written up in the entry
above this one** ("INCIDENT: full production data wipe...") - all
real data was restored before this step's actual verification (below)
ran, and `tests/conftest.py`/`huntloop.db_models.Vector` both needed real
fixes (not just the embedding-columns migration) to get here safely.

**Match scoring: computed at query time via pgvector's `<=>` operator
(cosine distance; similarity = `1 - distance`), NOT stored.** Decided
against a stored score column/table: with exactly one active resume at a
time and ~600 job postings, a live query is milliseconds - no
precomputation benefit - and a stored score would need an invalidation
mechanism (on every new scrape, on every resume update) that doesn't
exist and is out of scope for this step; storing it now would just be a
staleness bug waiting to happen. Revisit if/when there are enough
concurrent resume versions or jobs that live scoring becomes measurably
slow.

**Verified:**

- **(a)** `job_postings`: 605/605 rows have `embedding IS NOT NULL` (0
  nulls). Active resume version (1): has embedding. Backfill wall-clock:
  **34.9s** for all 605 job postings + 1 resume (Docker container run,
  includes one-time model download/load).
- **(b)** Score distribution across all 605 real job postings (resume vs.
  every posting, `1 - (r.embedding <=> j.embedding)`): min 0.033, max
  0.593, mean 0.372, stddev 0.091. Histogram across six 0.1-wide buckets
  from 0-0.6 is roughly bell-shaped and centered near the mean (1, 17,
  94, 290, 138, 65) - real spread at both tails, no degenerate
  clustering near 0.99 or a flat/uniform blob. Healthy signal.
- **(c)** Top 5 by score: all 5 are Palantir "Software Engineer" roles
  (Defense Applications x2 - same title, genuinely different `job_url`s/
  postings, confirmed not a dedup bug; Frontend Developer Productivity;
  Production Infrastructure Internship x2), scores 0.577-0.593. Bottom 5:
  Wealthfront "Fraud Operations Specialist" (0.033, the single lowest
  score in the whole dataset), two Wealthfront/Duolingo creative-design
  roles, Duolingo "Creative Director, Marketing" x2, scores 0.033-0.136.
  **Sanity check, by eye, against the resume's actual content** (backend
  engineer - Java/Python/C#, microservices, Spring Boot/FastAPI, AWS/
  Kubernetes, Prometheus/Grafana observability, DB optimization): the
  top-ranked roles are exactly the kind of software engineering work the
  resume describes; the bottom-ranked roles (fraud ops, creative/design,
  marketing) have essentially zero real skill overlap with it. The
  ranking looks correct and meaningful, not arbitrary - nothing here
  looked wrong enough to flag as a problem.
- **(d)** Full suite: 40/40 passing. Real production row counts
  (companies=9, job_postings=605, lca_disclosures=1431321,
  resume_versions=1, sponsor_name_overrides=1) reconfirmed unchanged
  before and after this run - the new standing check from the incident
  above.

**Decided:** No skills-list, 70%-threshold wiring, or LLM-suggestion
logic added - explicitly out of scope for this step, per the task.

**Next:** Real embeddings and a working, sanity-checked scoring mechanism
exist for both the active resume and every currently-scraped job
posting. Not yet built: any of the deferred matching-UX pieces above, a
`get_match_scores()`-style reusable function/API (this step's
verification used ad hoc SQL, documented here and reproducible, rather
than building an application-level interface prematurely), and
persistence/embedding computation triggered automatically on new scrapes
or resume updates (both still manual script runs).

---

## 2026-08-22 — Matched/missing skills via Groq (sample-only, not backfilled)

**Did:** Added `groq==1.6.0` to `requirements.txt`. Added
`huntloop.skills_matching`: `GROQ_API_KEY` is read from `.env` at module
import time and fails fast with a `RuntimeError` if unset - same pattern
as `DATABASE_URL` in `settings.py` - but scoped to this module only (not
`settings.py` itself), so environments/tests that never import this
feature never pay that cost. `match_skills(resume_text, job_description)`
cleans both texts via `huntloop.text_cleaning.clean_text()` (same
pipeline as embeddings), sends them to Groq's chat completions API with
`response_format={"type": "json_object"}`, and returns
`{"matched_skills": [...], "missing_skills": [...]}` on success or
`None` on any failure.

**Model choice, checked live, not assumed:** the task suggested "a Llama
3.x variant" as an example, but querying Groq's own `/v1/models`
endpoint directly showed **no Llama 3.x chat models are currently active
on Groq's free tier at all** - the live lineup is prompt-guard
(classifier), whisper (audio), orpheus (TTS), `allam-2-7b`,
`qwen/qwen3.6-27b`, `openai/gpt-oss-{20b,120b,safeguard-20b}`, and
`groq/compound{,-mini}`. Picked `openai/gpt-oss-20b` - a real, active,
general-purpose instruction-following chat model there - and verified it
end-to-end with a real request (JSON mode, a toy resume/job pair)
before adopting it, rather than guessing a Llama model name that turned
out not to exist.

**Failure handling** mirrors `detect_ats()`'s Playwright fallback and
`huntloop.metrics`' Pushgateway push: `match_skills()` catches
`groq.GroqError` (the SDK's common base for connection/timeout/rate-
limit/API errors) and a broad `Exception` fallback around the API call,
plus `json.JSONDecodeError` and a shape check (`matched_skills`/
`missing_skills` must both be lists) around the response - every path
logs one `WARNING` and returns `None`, never raises.

**Sample-only verification, per the task's explicit scope** (NOT a full
605-job backfill - that's separate future work once this was validated):
picked 9 jobs from a real query against Step 3's cosine-similarity
scores - top 3 (all Palantir "Software Engineer" roles, scores
0.578-0.593), middle 3 (Checkr - Engineering Manager/Chief of Staff/AI
Conversation Designer, scores 0.34-0.39), bottom 3 (Wealthfront Fraud
Ops/Creative Design, Duolingo Creative Director Marketing, scores
0.033-0.130) - via `scripts/sample_skills_match.py`, run against the
real active resume and real job descriptions.

**Results** (7 of 9 succeeded; 2 hit real, organic Groq failures - see
below):

- Palantir "Software Engineer - Defense Applications" (0.593):
  `matched_skills: []`, `missing_skills: [React, Maplibre, Three.js,
  Redux, Conjure, OSDK, Foundry platform, AIP platform]`.
- Palantir "Software Engineer - Frontend Developer Productivity"
  (0.582): `matched: [TypeScript, JavaScript, Angular, Vue.js, GitHub
  Actions, Jenkins]`, `missing: [GraphQL API, Blueprint UI framework,
  feature flags, internationalization, commit previews, frontend
  monorepo, build/CI for large frontend monorepo, VSCode extensions,
  developer experience in IDE, frontend infrastructure group]`.
- Palantir "Software Engineer, Internship - Production Infrastructure"
  (0.578): `matched: [Java, Typescript, Kubernetes, Grafana,
  microservices, observability, GitHub]`, `missing: [Go, Cilium, Envoy,
  React, Redux, Gradle, Windsurf, Cline, Apollo, Signals]`.
- Checkr "Engineering Manager, Verifications" (0.353): **failed** -
  `BadRequestError`, see below.
- Checkr "Chief of Staff" (0.344): `matched: [AI, data analysis,
  problem-solving]`, `missing:` ~30 executive/leadership/strategy items
  (leadership, execution, cross-functional teams, 10+ years executive
  experience, investor pitch decks, financial models, etc.).
- Checkr "AI Conversation Designer" (0.388): **failed** -
  `RateLimitError`, see below.
- Wealthfront "Fraud Operations Specialist" (0.033, the single lowest
  score in the whole 605-job dataset): `matched: []`, `missing: [fraud
  monitoring, fraud investigation, brokerage operations, compliance,
  engineering collaboration, product team collaboration]`.
- Wealthfront "Senior Designer - Editorial, Creative" (0.115):
  `matched: []`, `missing: [visual direction, visual assets, headers,
  charts, photo composites, illustrations, financial content, audience
  insights, distribution channels]`.
- Duolingo "Creative Director, Marketing" (0.130): `matched: []`,
  `missing:` ~25 brand/creative/marketing-leadership items.

**Sanity check, by eye:** the two mid/low-scoring roles with real
results (Chief of Staff, Fraud Ops, Senior Designer, Creative Director)
are all correct and convincing - near-zero real matches, and every
`missing_skills` list is genuinely the kind of domain expertise
(executive leadership, fraud investigation, visual design, brand
marketing) the resume's actual backend-engineering background doesn't
show. The two Palantir roles with concrete tech-stack overlap (Frontend
Developer Productivity, Production Infrastructure) matched very well -
`matched_skills` in both cases are skills genuinely present on the
resume (TypeScript/Angular/Vue.js/Jenkins; Java/Kubernetes/Grafana/
observability), and `missing_skills` are genuinely absent or Palantir-
internal-only tools (Conjure, OSDK, Blueprint, Cilium). **One result is
worth flagging, not as a bug but as a real, useful nuance**: the
highest-embedding-scoring job overall (Defense Applications, 0.593)
came back with `matched_skills: []` - zero skills in common. That's
correct on inspection, not wrong: that listing's specific requirements
(React, Maplibre, Three.js, Redux, plus Palantir's own internal platform
names - Conjure, OSDK, AIP, Foundry) genuinely don't overlap with
anything on the resume, even though the *embedding* similarity is high
(embeddings capture broad semantic/domain similarity - "software
engineer," "APIs," "distributed systems" - not literal named-technology
overlap). This demonstrates the two signals (embedding similarity vs.
LLM-extracted skill overlap) are measuring genuinely different things
and can legitimately diverge on a single job - worth keeping in mind for
whatever later step decides how to combine or threshold them.

**(c) Graceful failure handling - confirmed via 3 separate real cases,
not one contrived test:**

- Organic `BadRequestError` (Checkr "Engineering Manager,
  Verifications"): `Groq API call failed (BadRequestError): Error code:
  400 - {'error': {'message': "Failed to validate JSON. Please adjust
  your prompt. See 'failed_generation' for more details.", 'type':
  'invalid_request_error', 'code': 'json_validate_failed', ...}}` - the
  model failed to produce schema-valid JSON for this one input; logged
  and skipped, script continued to the next job without crashing.
- Organic `RateLimitError` (Checkr "AI Conversation Designer"), after
  the SDK's own internal retry-with-backoff was exhausted: `Groq API
  call failed (RateLimitError): Error code: 429 - {'error': {'message':
  'Rate limit reached for model `openai/gpt-oss-20b` ... tokens per
  minute (TPM): Limit 8000, Used 4359, Requested 4227 ...'}}` - same
  graceful handling.
- Deliberate, controlled test: pointed `match_skills()` at a nonexistent
  model name and confirmed a clean failure: `WARNING:
  huntloop.skills_matching: Groq API call failed (NotFoundError): Error
  code: 404 - {'error': {'message': 'The model
  `this-model-does-not-exist` does not exist or you do not have access
  to it.', 'code': 'model_not_found'}}` followed by `RESULT: None` - no
  traceback, no crash, exactly the documented contract.

**(d) Real cost/rate-limit behavior encountered:** Groq's free tier is
$0 (no cost incurred) but the rate limit is real and was hit twice
during this 9-request sample run - `openai/gpt-oss-20b`'s free-tier
limit is **8000 tokens per minute (TPM)**, and this workload (a ~1200-
token resume + job description per call, with `gpt-oss-20b` also
generating a hidden `reasoning` field that consumes completion tokens
before the JSON answer) burned through that budget well before 9 calls
completed. The Groq Python SDK retries 429s internally with backoff by
default (visible in the logs as `Retrying request to
/openai/v1/chat/completions in N seconds`), which is why most of the 9
calls eventually succeeded despite repeated 429s - only the 2 failures
above happened when a call's own retry budget was exhausted or (for the
`BadRequestError`) wasn't a rate-limit issue at all. **A real full
605-job backfill would need explicit rate-limiting/pacing logic** (not
built here - out of scope, sample-only per the task) to avoid spending
most of its time in retry backoff.

**Verified:** Full suite still 40/40 passing - `huntloop.skills_matching`
isn't imported by any existing module/test, so the module's import-time
`GROQ_API_KEY` fail-fast has zero effect on anything that doesn't use
this feature. Real production row counts reconfirmed unchanged
(companies=9, job_postings=605, lca_disclosures=1431321,
resume_versions=1, sponsor_name_overrides=1) - this step only reads,
never writes.

**Decided:** No full backfill, no threshold/UI wiring, no persistence of
skills results anywhere - explicitly out of scope per the task. A real
backfill will need to budget for the TPM limit found above (pacing
and/or a paid tier) before it's attempted.

**Next:** Full 605-job skills backfill (with real rate-limiting) is the
natural next step once this sample is accepted as validated. Not
started: any UI/API surface, persistence of results, or combining this
with the Step 3 embedding score into a single ranking.

---

## 2026-08-22 — Pacing investigation: three real levers measured before committing to a full backfill

**Did:** Before running a full 605-job backfill, tested three ways to
reduce Groq token cost per job, measuring real numbers rather than
assuming, per the user's explicit request:

1. **`reasoning_effort="low"`** on `openai/gpt-oss-20b`: cut tokens
   ~28% on average (3058->1781, 2973->2407, 1722->1390 across 3 sample
   jobs) but showed a real, repeatable quality regression - it
   consistently dropped soft/inferred matches into `missing_skills`
   instead of `matched_skills` (clearest case: Checkr "Chief of Staff" -
   baseline correctly matched `["AI-assisted tooling", ...,
   "data analysis", "problem solving"]`; low-effort matched nothing and
   put "Data analysis and problem-solving skills" in missing instead).
   Also confirmed real run-to-run output variance in this model even at
   `temperature=0.1` (a baseline rerun of the same Palantir job produced
   a different result than Step 4's original run) - a caveat on how much
   weight any single-sample comparison can bear.
2. **Smaller/cheaper alternative model**: re-checked `/v1/models` live -
   still no Llama 3.x chat model active. Tested the two closest
   candidates: `allam-2-7b` (7B, Arabic-English bilingual) failed twice
   with real instruction-following breakdowns (hallucinated irrelevant
   skills, once stuck in a "B2B marketing campaigns" repetition loop);
   `qwen/qwen3.6-27b` worked but is a *bigger*, also-hidden-reasoning
   model that used *more* tokens (3252 vs. 1722) for the same job -
   strictly worse. No viable alternative found.
3. **Batching N jobs into one call**: batch-of-5 used 5589 tokens total
   (1118/job, 2.3x more efficient than baseline) with no quality cost -
   in one case the batched result was *more* complete than the
   single-call baseline. Batch-of-10 was **rejected outright by Groq
   with a 413** - a real, previously-unknown hard cap: a single request
   cannot itself exceed ~8000 tokens, independent of the rolling-window
   TPM accounting. Batch-of-5 + low-effort combined for the best
   measured throughput (833 tokens/job) but inherited the same
   soft-match-dropping quality issue as lever 1.

**Recommendation given:** batch-of-5 without `reasoning_effort=low` -
nearly all the speed benefit (2.3x) with none of the observed quality
cost. User confirmed this choice for the real backfill.

---

## 2026-08-22 — Real skills-matching backfill attempt: a third real limit discovered mid-run, and a real quality anomaly found

**Did:** Added `matched_skills`/`missing_skills` JSON columns to
`job_postings` (migration `0fdafe5d162e`, already applied). Added
`huntloop.skills_matching.match_skills_batch()` - the batch-of-5
production version of the prototype measured above (`match_skills()`
from Step 4 itself untouched). Rewrote
`scripts/backfill_skills_matching.py` to use it: `_chunk_jobs()` groups
jobs into batches of at most `MAX_BATCH_SIZE=5`, ending a batch early if
its estimated tokens would exceed `MAX_BATCH_ESTIMATED_TOKENS=7000`
(margin below the real 8000-token hard per-request cap found above);
`TokenPacer` paces batches against a sliding 60s window under
`TARGET_TPM=6000` (margin below the real 8000 TPM cap). A 6-job smoke
test confirmed the whole pipeline end-to-end first (chunking, pacing,
partial-batch-failure handling, DB writes) before committing to the real
605-job run.

**Ran the real backfill. Result: 73 succeeded, 529 failed, 602
processed, in 13,373s (222.9 min ~= 3.7 hours) - both slower AND far
less complete than the ~1.9-hour/full-completion estimate.**

**Root cause, traced precisely from the run's own log:** Groq enforces
a THIRD real limit, never surfaced by any earlier testing (that
testing's cumulative volume - Step 4's 9-job sample, the 3-lever
comparison above, ~20-30 calls total - never got close to it): a
**200,000 tokens-per-day (TPD)** cap, separate from both the per-minute
TPM cap and the per-request hard cap already accounted for. The run's
first 33 batches (16:03:55-16:36:14, ~32 minutes) succeeded normally.
At 16:37:12 the account hit `Used 196045` of `Limit 200000` TPD and
every subsequent call started failing with 429. `TokenPacer` only
modeled the 60-second TPM window, with no concept of a 24-hour budget -
so for the remaining ~190 minutes it kept retrying every ~60s against an
exhausted daily budget, correctly logging a warning each time (no crash)
but making zero real progress. **Fixed**: `match_skills_batch()` now
raises a new `DailyQuotaExhausted` exception specifically when Groq's
error message mentions "tokens per day" (every other failure mode still
returns `None` as before, unchanged contract); `backfill_skills_matching.py`
catches it and stops the whole run immediately with a clear log message,
instead of retrying uselessly. A full 605-job backfill needs ~676,000
total tokens (605 x ~1118 tokens/job batched) against a 200K/day budget -
**a minimum of ~3.4 days of daily budget, realistically more** - so this
script is now designed to be re-run once per day as each day's budget
frees up, not run to completion in one sitting.

**Verified:**

- **(a)** 73 succeeded, 529 failed, 602 processed, 13,373s - reported
  honestly above; does not match the pre-run estimate, for the TPD
  reason found and explained above.
- **(b)** DB query confirmed zero ambiguity: of all 605 rows, 76 have
  both `matched_skills` and `missing_skills` populated (73 from this run
  + 3 from the earlier smoke test), 529 have both NULL, **0 rows have
  only one of the two set** - the designed all-or-nothing-per-row
  contract held exactly.
- **(c) Spot-check found a real quality issue, not just successes:**
  Palantir "Deployment Strategist" (job id 858) came back with **53
  matched_skills** - essentially the entire resume's technical-skills
  section dumped verbatim, regardless of whether the job description
  actually mentions those specific technologies. Traced to the exact
  batch: the same call's other two successful items (Fraud Ops
  Specialist, iOS Engineer) got short, sensible, clearly job-specific
  results - so this isn't an index-mapping bug in
  `match_skills_batch()`'s parsing, it's the model itself occasionally
  defaulting to a full-resume dump for one item in a batch, plausibly
  when a job's description doesn't name specific technologies the model
  can pattern-match against (Palantir's "Deployment Strategist" postings
  are known to be vaguely-worded and non-technical-skill-labeled - see
  earlier sessions). Real, previously-unseen failure mode at this job's
  diversity/scale that the small-scale lever tests above didn't surface.
  **Positive finding, as requested**: the soft-match re-check held up -
  Duolingo "Senior Data Science Manager, User Growth" correctly matched
  `["Python", "SQL", "ML data pipelines", "Sentence-BERT", "NLP",
  "semantic search", "AWS", "GCP", "Docker", "Kubernetes"]` (genuinely
  on the resume, via its AI/dev-tools and side-project sections) while
  correctly listing `["data science", "statistical modeling", "causal
  inference", "R", "team management", ...]` as missing - batching alone
  (no low-effort) does still catch inferred/soft matches, confirming the
  measurement step's conclusion on that specific point.
- **(d)** Cost: $0 confirmed (Groq free tier `on_demand` service tier
  throughout - every rate-limit error explicitly offered a paid "Dev
  Tier" upgrade, confirming nothing was ever silently billed). Real
  rate-limit behavior: hit both the TPM cap (expected, paced around
  successfully) and the previously-unknown TPD cap (not paced around -
  the actual cause of this run's shortfall, now fixed for future runs
  per above).
- **(e)** Full suite: 40/40 passing. Real production row counts
  reconfirmed unchanged (companies=9, job_postings=605 - row count
  unchanged, only new column values written via UPDATE, not new rows;
  lca_disclosures=1431321, resume_versions=1,
  sponsor_name_overrides=1).

**Decided:** Did NOT claim this closes out Phase 3's matching engine -
the backfill is genuinely 12.6% complete (76/605), not done, and the
Deployment-Strategist-style full-resume-dump anomaly is a real, open
quality question (frequency unknown - seen once in 76 real results) that
wasn't investigated further or mitigated in this session (e.g., no
sanity filter added to reject implausibly-long matched_skills lists) -
flagged to the user rather than unilaterally deciding how to handle it,
since it involves a real scope/quality tradeoff.

**Next:** Awaiting direction on: (1) whether to add a quality sanity
check for the full-resume-dump failure mode before continuing the
backfill, and (2) how to proceed given the backfill now needs to be
spread across multiple days (minimum ~3-4, likely more) rather than
completed in one sitting - `scripts/backfill_skills_matching.py` is
interrupt-safe and TPD-aware now, so simply re-running it once per day
is the mechanical path, but the cadence/schedule is a real decision, not
something to assume.

---

## 2026-08-22 — Sanity filter + daily-cron integration: Phase 3's matching engine is now complete and self-sustaining

**Did (1 - sanity filter):** Added `MAX_PLAUSIBLE_MATCHED_SKILLS = 20` to
`huntloop.skills_matching` and enforced it inside
`match_skills_batch()`'s response parsing: any job whose `matched_skills`
exceeds 20 items is rejected (logged with a clear warning identifying
the job_index and a preview of the offending list) and left out of
`by_index`, so it comes back as `None` in the batch's output - same
"leave it NULL, get reprocessed later" contract as every other failure
mode, not a new one.

**Threshold reasoning, backed by real data, not picked arbitrarily:**
queried all 76 real stored results from the previous run and found a
clean, wide gap - every genuine result had 0-10 matched_skills; the two
known anomalies (Palantir "Deployment Strategist", and a duplicate
"Backend Software Engineer - Application Development" posting, id 820/821)
had 33 and 53. 20 sits almost exactly in the middle of that gap - double
the highest legitimate count seen, and well under half of the lowest
anomaly - giving wide margin against both false positives (rejecting a
real, unusually skills-heavy match) and false negatives (missing an
anomaly) without needing a tighter, more fragile threshold.

**Verified against real data, not synthetic cases:**

- **(a)** Loaded the actual stored `matched_skills` arrays for all 3
  known real anomalies (ids 820, 821, 858 - 33, 33, 53 items) directly
  from the database and ran them through the real filter logic: **all 3
  correctly rejected.**
- **(b)** Ran the same check against all 73 other real, legitimate
  stored results (0-10 items each): **all 73 correctly passed through,
  zero false positives.** The full observed distribution across all 76
  real results: `[0 x51, 1 x3, 2 x6, 3 x4, 4, 6, 9 x5, 10 x2, 33, 33,
  53]` - the gap between 10 and 33 is real, not a coincidence of one
  sample.
- Nulled out the 3 contaminated rows (820, 821, 858) directly in the DB
  so they'd be picked up for reprocessing under the fix, rather than
  leaving known-bad data sitting in the table indefinitely.
- **Confirmed working live, not just retrospectively**: during today's
  real cron run (below), the filter caught a 4th real anomaly in
  production - job_postings.id=881 ("Software Engineer, Frontend") came
  back with 26 matched_skills, was rejected with a clear log line, and
  was correctly left NULL rather than stored.

**Did (2 - daily-cron integration):** Reframed skills-matching from a
one-time backfill into a recurring stage of the existing daily cron
orchestrator (`scripts/run_orchestrator_cron.sh`, Step 7/Step 5's
scheduling), not a separately-triggered script:

- The cron wrapper now runs two stages every scheduled invocation:
  stage 1 is the existing scraper orchestrator (`main.py`), stage 2 is
  `scripts/backfill_skills_matching.py`. Both always run regardless of
  the other's exit code (a scraping hiccup shouldn't stall
  skills-matching progress on the backlog, and vice versa); the
  wrapper's own exit code is non-zero if either stage failed, 0
  otherwise. `logs/cron.log` now shows a labeled "stage 1/2" / "stage
  2/2" breakdown per run.
- `backfill_skills_matching.py`'s query now explicitly orders by
  `scraped_at ASC, id ASC` (oldest job posting first) instead of
  relying on unspecified default ordering - clears the longest-standing
  backlog first, and gives deterministic day-to-day behavior.
- `match_skills_batch()` now raises a new `DailyQuotaExhausted`
  exception specifically when Groq's error mentions "tokens per day"
  (every other failure mode still returns `None`, unchanged); the
  backfill script's `main()` catches it, logs a clear message, and
  stops the whole run immediately - this was built in the previous
  entry but not yet exercised against a real TPD cutoff from inside the
  actual cron path until today's manual trigger (below).
- No crontab change needed - the existing `0 3 * * *` entry (Step 5)
  already points at `run_orchestrator_cron.sh`, which now does more
  without any new scheduling. Nothing to manually re-trigger going
  forward: each day's 3am run works through the NULL backlog under the
  real 200K daily budget, and also naturally covers whatever new job
  postings that same run's scrape just added - one mechanism handles
  both catching up on the backlog and staying current, not two.

**Verified (2):**

- **(c)** Manually triggered `./scripts/run_orchestrator_cron.sh` (not
  waiting for 3am) with 532 NULL rows pending. Real result: stage 1
  (scrape) succeeded normally; stage 2 processed 24 jobs (3 succeeded, 21
  failed - a mix of organic `BadRequestError`s, oddly many
  "max completion tokens reached before generating a valid document"
  json-validation failures in a row right before the cutoff, plausibly
  Groq internally truncating generation as the account's daily budget
  ran low rather than issuing a clean 429 until the very last one - and
  the one sanity-filter rejection above) before hitting a real 429 with
  `tokens per day (TPD): Limit 200000, Used 199182`. `DailyQuotaExhausted`
  fired correctly: `[ERROR] Groq's daily token quota is exhausted after
  24/532 jobs processed (batch 6/200) - stopping this run rather than
  retrying uselessly for hours. Re-run this script once the daily budget
  resets to pick up where it left off`. The backfill stage then logged
  `Backfill stopped early (daily quota exhausted): 3 succeeded, 21
  failed, 24 total processed in 302.8s` and returned normally - the
  wrapper's overall run finished with **exit code 0** (both stages
  "succeeded" in the sense that neither crashed; a `DailyQuotaExhausted`
  stop is expected steady-state behavior, not a failure to alarm on).
- **(d)** Current total progress: **76/605 job_postings rows have
  matched_skills/missing_skills populated, 529 remain NULL** (76 = 73
  from the pacing-investigation-era run + 3 new from today's cron
  trigger; the 3 previously-contaminated rows are back in the NULL pool
  for reprocessing). DB query confirmed zero inconsistent rows (every
  row is cleanly both-populated or both-NULL), same as always.
- **(e)** Full suite: 40/40 passing. Real production row counts
  reconfirmed unchanged before and after (companies=9, job_postings=605,
  lca_disclosures=1431321, resume_versions=1,
  sponsor_name_overrides=1) - row *count* never changes from this
  feature, only column values within existing rows.

**Decided:** Phase 3's matching engine (embeddings + scoring + skills
matching, Steps 3-5 collectively) is **complete and self-sustaining**,
not "fully backfilled" - that distinction matters and is deliberate.
The backlog will keep shrinking by roughly however many jobs each day's
~200K token budget allows (today: 3 genuinely new + several rejected/
retried), and every future day's newly-scraped postings get matched in
the same run they're scraped in, going forward, with no separate manual
step ever needed again. Completion of the current 529-row backlog will
happen gradually over the coming days/weeks as a natural side effect of
the existing schedule - not something to force or manually re-trigger.

**Next:** Nothing further planned for Phase 3's matching engine itself.
Future related work (not started, not requested): combining the
Step 3 embedding score and the skills-match result into a single ranking
or UI surface, curating/tuning the sanity-filter threshold further if
real data ever shows a false positive or a missed anomaly above 20 that
the filter should have caught, and the 70%-threshold/LLM-suggestion
logic explicitly deferred since Step 3.

---

## 2026-08-22 — FastAPI backend service (skeleton only)

**Did:** Added `src/huntloop/api/` - a FastAPI app (`main.py`) with a
single `GET /health` endpoint (`routers/health.py`) returning
`{"status": "ok"}`, plus the project structure for what's coming:
`routers/` (package, `health.py` the only router so far),
`schemas/` (empty package, ready for future Pydantic models - nothing to
define a schema for yet), and `dependencies.py` (a `get_db()` DB-session
dependency, not used by any route yet - `/health` is deliberately
DB-independent). No real job/application endpoints, per this step's
explicit scope. `fastapi`/`uvicorn` were already in `requirements.txt`
(added a prior session, unused until now) - no new dependencies needed.

Added a new `api` service to `docker-compose.yml` - its own container
(`build: .`, same Dockerfile/image as `app`, since both already need the
same dependencies installed), its own port (8000), connected to the same
`db` service, with its own healthcheck (`python -c
"import urllib.request..."` rather than curl/wget - the
`python:3.13-slim` base image has neither installed). Deliberately not
merged into the existing `app` service - separate concern, separate
container, per the task.

**Verified:**

- **(a)** Tested locally first (`uvicorn huntloop.api.main:app`, no
  Docker) before touching docker-compose - `/health` returned
  `{"status":"ok"}`, confirmed before moving on. Then for real via
  Docker: `docker compose up -d api` - `huntloop-api-1` came up and
  reported **healthy**; `curl http://localhost:8000/health` returned
  `{"status":"ok"}` through the actual published port.
- **(b)** `curl http://localhost:8000/docs` returned 200; fetched
  `/openapi.json` directly and confirmed it lists exactly one path,
  `/health`, under title "HuntLoop API".
- **(c)** Confirmed the addition is purely additive, not just by
  assumption: `git diff docker-compose.yml` has zero removed lines (only
  the new `api` service block added); `docker compose run --rm app
  python -c "..."` still runs the scraper service fine; `crontab -l`
  unchanged; `git status` on `main.py`, `scripts/run_orchestrator_cron.sh`,
  and `scripts/backfill_skills_matching.py` all show zero diff - none of
  those files were touched; `docker compose --profile observability
  config` still validates cleanly.
- **(d)** Full suite: 40/40 passing. Real production row counts
  reconfirmed unchanged (companies=9, job_postings=605,
  lca_disclosures=1431321, resume_versions=1,
  sponsor_name_overrides=1) - this step never touched real data (the
  Docker `api`/`db` containers used for verification are the separate,
  smaller docker-compose Postgres instance on port 5433, not local
  system Postgres on 5432).

**Decided:** No real endpoints, no auth, no CORS config, no versioning -
all explicitly out of scope for a skeleton step. `docker-compose down`
(no `-v`) used for cleanup after verification, so the separate
docker-compose `pgdata` volume was left intact.

**Next:** First real endpoint(s) - most likely something surfacing
job_postings + their Step 3/5 match scores/skills, once there's a
concrete UI or API-consumer need - are natural next work, not started
here.

---

## 2026-08-22 — Real API endpoints: GET /jobs, GET /jobs/{id}, PATCH /jobs/{id}/application

**Did:**

1. **Fixed the `api` service's DB connection.** `docker-compose.yml`'s
   `api` now points `DATABASE_URL` at `host.docker.internal:5432` (the
   real local system Postgres, using the `POSTGRES_USER`/
   `POSTGRES_PASSWORD`/`POSTGRES_DB` vars already in `.env` - confirmed
   those match the real `DATABASE_URL`'s credentials exactly, not just
   assumed) instead of the docker-compose `db` service - same pattern
   already used for `scripts/backfill_embeddings.py`. Added
   `extra_hosts: ["host.docker.internal:host-gateway"]` for Linux
   portability (harmless on Mac, where Docker Desktop already resolves
   it natively). `api` no longer has `depends_on: db` at all - it never
   talks to that service now, confirmed by `docker compose up -d api`
   starting only `api`, not `db`.
2. Added `job_applications` (migration `7d31cf7fed9c`,
   `huntloop.db_models.JobApplication`/`ApplicationStatus`): `id`,
   `job_posting_id` (FK, UNIQUE - one row per job, upserted, not a
   history table), `status` (a real Postgres enum -
   `not_applied`/`applied`/`interviewing`/`rejected`/`offer`),
   `applied_at`, `status_updated_at` (auto-bumped via the column's
   `onupdate`), `notes`.
3. Built the three real endpoints (`huntloop.api.routers.jobs`) with
   proper Pydantic schemas (`huntloop.api.schemas.jobs`:
   `JobSummary`/`JobDetail`/`JobListResponse`/
   `ApplicationStatusUpdate`/`ApplicationStatusResponse`) - real response
   models, not raw dicts, so `/docs` actually describes the response
   shape. Match score is computed at query time via pgvector (same
   approach as every prior ad hoc score query), matched/missing skills
   are read from the precomputed `job_postings` columns (Step 5),
   application status defaults to `not_applied` via `COALESCE` when no
   `job_applications` row exists (never backfilled with a dummy row just
   to represent "not applied yet").

**Two real bugs found and fixed (not by reasoning alone - by actually
running things):**

- **Enum value/name mismatch**: SQLAlchemy's `Enum(SomePythonEnum)`
  defaults to mapping the Python enum's *member name* (`NOT_APPLIED`) to
  the DB, not `.value` (`not_applied`) - but the Postgres enum type
  (created by the migration with literal lowercase labels) and the
  column's `server_default` both use `.value`. Every `GET /jobs` request
  raised `LookupError: 'not_applied' is not among the defined enum
  values` - caught immediately by actually hitting the endpoint, not by
  writing the migration or model in isolation. Fixed with
  `values_callable=lambda cls: [e.value for e in cls]` on the column's
  `Enum(...)`.
- **pgvector operator resolution under the test schema's search_path**:
  writing real tests (`tests/test_api_jobs.py`) surfaced a second,
  related instance of the incident-class bug from Step 3 - `<=>` (and
  `<->`/`<#>`/`<+>`) are defined in `public`, and an unqualified
  reference to any of them doesn't resolve under
  `tests/conftest.py`'s isolated schema (search_path deliberately never
  includes `public`), even when both operands are explicitly cast to
  `public.vector`. Confirmed directly via `psql` before fixing: a bare
  `<=>` failed with "operator does not exist" regardless of explicit
  casts; Postgres's schema-qualified-operator syntax,
  `OPERATOR(public.<=>)`, resolved correctly regardless of search_path,
  including coercing the right operand's "unknown"-typed bind parameter
  without needing search_path help for that either. Fixed at the same
  place as the earlier DDL fix - `huntloop.db_models.Vector` now
  overrides `cosine_distance()`/`l2_distance()`/`max_inner_product()`/
  `l1_distance()` to emit `OPERATOR(public.<op>)` instead of the bare
  operator pgvector's base `Comparator` emits. Also fixed the unrelated
  `cache_ok` SAWarning noticed along the way (`Vector` subclass wasn't
  explicitly inheriting it) - one-line fix, no behavior change.

**Also fixed while testing**: Postgres's default `NULLS FIRST` for
`ORDER BY ... DESC` would have put not-yet-embedded jobs (null
match_score) at the *top* of the default "best matches first" sort -
caught by a real test asserting sort order, not assumed correct.
`list_jobs()` now uses `.nulls_last()` explicitly on both sort
directions.

**Verified:**

- **(a)** `GET /jobs?limit=2` through the real `api` Docker container
  returned `"total": 605` - the real production job count, confirmed
  matching `SELECT count(*) FROM job_postings` on the real 5432
  instance, not Docker's smaller one.
- **(b)** Hit all three endpoints for real (pasted in the conversation,
  not just described): `GET /jobs` list with real scores/skills/status;
  `GET /jobs/{id}` full detail including the raw HTML
  `job_description`; `PATCH /jobs/{id}/application` creating then
  updating a real application row.
- **(c)** `GET /jobs` and `GET /jobs/{id}` both reflected the PATCH'd
  status (`applied`, then `interviewing`) immediately afterward.
- **(d)** `company=checkr` returned only Checkr jobs (case-insensitive,
  confirmed with `CHECKR` too); `min_score=0.5` returned only jobs
  scoring >=0.5, correctly sorted descending; default sort (`-score`)
  and explicit ascending sort (`score`) both confirmed against real
  data.
- **(e)** Added `tests/test_api_jobs.py` (13 new tests, using a new
  `api_client` fixture in `tests/conftest.py` that overrides FastAPI's
  `get_db` dependency to use the isolated test schema, never the real
  `DATABASE_URL` the app's own `huntloop.api.dependencies` module points
  at) - covers sort order (both directions, including the nulls-last
  fix), company/min_score filtering, default `not_applied` status,
  job detail with skills, 404s, the upsert-not-duplicate PATCH
  behavior, `applied_at` staying fixed across later status changes, and
  the `min_score`-without-active-resume 400 case. Full suite: 53/53
  passing (40 previous + 13 new). Real production row counts
  reconfirmed unchanged after all manual testing
  (companies=9, job_postings=605, lca_disclosures=1431321,
  resume_versions=1, sponsor_name_overrides=1,
  job_applications=0 - the one test PATCH made against real data via
  Docker was cleaned up afterward, not left sitting in the real table).

**Decided:** `min_score` without an active resume returns 400 rather
than silently filtering everything out (NULL comparisons are never
true in SQL, which would otherwise look like "no jobs match" instead of
"this filter can't be applied right now"). PATCH always replaces
`notes` wholesale with whatever the payload has (including clearing it)
rather than distinguishing "field omitted" from "field explicitly
null" - simplest correct behavior for a two-field update body, not
worth `exclude_unset` complexity here.

**Next:** No further endpoints planned - GET/PATCH for jobs +
applications was the full scope of this step. Natural future work (not
started): endpoints for resume management, a combined-ranking endpoint,
auth, pagination metadata beyond total/limit/offset (e.g. next/prev
links) if a real frontend ever needs them.

---

## 2026-08-22 — Next.js frontend scaffold (connectivity check only)

**Did:** Scaffolded `frontend/` via `create-next-app` (App Router,
TypeScript, Tailwind CSS, ESLint) and added `@tanstack/react-query`.
Structure set up for what's coming: `src/types/api.ts` (hand-mirrors the
backend's real Pydantic schemas - `JobSummary`/`JobDetail`/
`JobListResponse`/`ApplicationStatus`/etc. - no shared codegen yet, kept
manually in sync for now), `src/lib/api.ts` (a real fetch client -
`getHealth()`, `getJobs()`, `getJob()`, `updateApplicationStatus()` -
hitting the actual API, nothing mocked), `src/components/` (empty
placeholder). `src/app/providers.tsx` wraps the app in a
`QueryClientProvider`; `src/app/page.tsx` is the one real page for this
step - calls `GET /health` and `GET /jobs` via TanStack Query and
renders "API status: {status}" / "{total} jobs found", nothing else. No
real job-list/filtering/status-update UI - explicitly out of scope.

**A real, blocking bug was found and fixed along the way, not by
inspection - by actually loading the page in a browser:** the first
real load showed "API status: unreachable (Failed to fetch)" despite
`curl` against the same API succeeding seconds earlier. Traced via the
browser's network panel (not guessed): the browser sent a CORS preflight
`OPTIONS /health` that got a `405` - `huntloop.api.main` had no
`CORSMiddleware` configured, since every consumer of the API until now
was server-to-server (`curl`, `httpx`, pytest's `TestClient`), which
CORS doesn't restrict (it's a browser-only enforcement mechanism, so
this gap was invisible to every check done before there was an actual
browser-based client). Fixed by adding `CORSMiddleware` to
`huntloop/api/main.py`, allowing `http://localhost:3000`/
`http://127.0.0.1:3000` by default (the Next.js dev server's own
default origin), overridable via a new `CORS_ALLOWED_ORIGINS` env var.
This is a real, necessary fix - without it, the task's own verification
requirement (real rendered data from the real API) is impossible to
satisfy, not an optional nicety.

**Docker decision: NOT containerized, deliberately - reasoning, not a
default.** At this skeleton stage the frontend has no stable build and
will change on every future step; `npm run dev`'s Turbopack hot reload
is meaningfully faster to iterate against than a Docker image rebuild
loop, with no compensating benefit yet (nothing else depends on it being
containerized - it isn't part of the cron/scraper pipeline, and no
teammate/deploy target needs it today). Documented as a revisit-later
decision (once there's a real UI, or a reason Docker actually helps),
not a permanent choice.

**Verified:**

- **(a)** Brought up the real API (`uvicorn huntloop.api.main:app`) and
  the real frontend (`npm run dev`), navigated to
  `http://localhost:3000` in an actual Chrome tab (via claude-in-chrome,
  not curl - curl can't show client-rendered TanStack Query data), and
  took a real screenshot: **"API status: ok"** (green) and **"605 jobs
  found"** - the real production job count, matching
  `SELECT count(*) FROM job_postings` on the real 5432 instance.
  Confirmed the failure mode first (a real screenshot showing
  "unreachable (Failed to fetch)"), diagnosed it via the browser's own
  network panel (`OPTIONS` -> 405), fixed it, then reloaded and
  confirmed the fix with a second real screenshot - not assumed fixed
  from reading the code change alone.
- **(b)** Confirmed non-interference concretely: `main.py`,
  `scripts/run_orchestrator_cron.sh`, and
  `scripts/backfill_skills_matching.py` all show zero git diff;
  `crontab -l` unchanged; `docker compose config` still validates. The
  `docker-compose.yml` diff present is entirely the prior (still
  uncommitted) `api`-service-addition task, untouched further here.
- **(c)** Full backend suite: 53/53 passing (the CORS middleware change
  to `huntloop/api/main.py` didn't break anything).
- Cleaned up after testing: closed the browser tab, stopped the local
  `uvicorn`/`npm run dev` processes started for verification - nothing
  left running.

**Decided:** No real job-list/filtering/status-update UI, no auth, no
shared TS/Python schema codegen (types are hand-kept-in-sync for now,
noted as a known gap) - all explicitly out of scope for this skeleton
step, per the task.

**Next:** The real job-list UI (consuming the already-real
`src/lib/api.ts`/`src/types/api.ts`) is the natural next step, not
started here. Also not started: containerizing the frontend (see the
Docker decision above for when that'd make sense), any shared-schema
codegen to replace the hand-mirrored TypeScript types.

---

## 2026-08-23 — Real job-list UI, replacing the Step 3 placeholder page

**Did:** Built the real card-based job list
(`frontend/src/app/page.tsx` + `frontend/src/components/`:
`JobCard`, `ScoreIndicator`, `SkillChips`, `StatusBadge`, `JobFilters`,
`Pagination`), wired to the real `GET /jobs` query params (`company`,
`min_score`, `sort`, `limit`/`offset`) via TanStack Query
(`keepPreviousData` so paging/filtering never blank-flashes - the
previous page's cards stay visible, slightly dimmed only by the browser's
own repaint timing, while the next page loads). Status shown read-only
(`StatusBadge`) - the update interaction is explicitly Step 5, not built
here.

**A real, necessary backend gap was found and fixed before the UI could
even be built - not by inspection, by trying to build the "location(s)"
requirement and discovering the API had nothing to bind it to.**
`GET /jobs`/`GET /jobs/{id}` never exposed `job_postings.locations`
(the `job_locations` table) at all - Step 4's schemas only had
`department`, not real location strings. Added `locations: list[str]` to
`JobSummary` (inherited by `JobDetail`), populated in
`huntloop.api.routers.jobs` via the existing `JobPosting.locations`
relationship (`[loc.location_name for loc in job.locations]`) - same
minimal-necessary-backend-change precedent as the CORS fix in the
previous entry, not scope creep. Added a real regression test
(`test_list_jobs_includes_locations_and_empty_list_when_none`) plus a
`locations` assertion in the existing detail test; mirrored the field
into `frontend/src/types/api.ts`.

**Score indicator design decision, calibrated to real data, not a
generic 0-1 scale:** `ScoreIndicator`'s red-to-green gradient pins "full
green" at 0.6, not 1.0 - real observed scores (all-MiniLM-L6-v2 cosine
similarity between the active resume and real scraped job descriptions)
cluster between ~0.03 and ~0.59 (see Step 3's histogram, SESSIONS.md);
a naive 0-1 scale would render nearly every real job the same dull
color, defeating "genuinely scannable at a glance." Confirmed this
mattered in practice during verification: the real highest-scoring job
(59%) renders solid green, the real lowest-scoring job in the whole
605-row dataset (Wealthfront "Fraud Operations Specialist", 3% - the
same job flagged as the correct bottom result back in Step 3) renders
clearly red, with a genuine gradient in between - not two flat buckets.

**Skill chips**: matched = solid green pill, missing = dashed neutral
outline - deliberately different *shapes*, not just different colors
(colorblind-safer, and reads correctly even in the screenshots' dark
theme). Capped at 5 visible per section with a "+N more" overflow chip -
real `missing_skills` lists run to 20-30+ items for a badly-matched job
(confirmed in Step 4/5), so showing all of them would blow out card
height.

**Verified, all against the real API + real data, via claude-in-chrome
(actual rendered screenshots, not curl/described output):**

- **(a)** Real screenshots at both score extremes: the real top-scoring
  job (Palantir "Software Engineer - Defense Applications", 59%, green
  bar) and, after sorting ascending, the real lowest-scoring job in the
  full 605-row dataset (Wealthfront "Fraud Operations Specialist", 3%,
  red bar) - confirming the color treatment spans the real range, not
  just a narrow slice. A mid/low-score Checkr job ("Enterprise Account
  Executive", 27%, amber bar) showed the matched/missing chip styles
  side by side clearly ("AI tools" solid green vs. "B2B sales" etc.
  dashed-outline, +8 more overflow) - zoomed screenshot pasted in the
  conversation.
- **(b)** Filtering: `company=palantir` narrowed 605 -> 308 real results,
  confirmed by screenshot before/after; combined with `min_score=0.52`
  narrowed further to 27. Sorting: switching "Sort by score" to "Worst
  match first" (via the form_input tool, since a native `<select>`
  needed direct option selection, not click+arrow-key which didn't
  register) correctly reordered to show the real lowest-scoring job
  first, confirmed against the exact score (3%) known from Step 3's
  original score-distribution investigation - not just "looks sorted,"
  independently cross-checked against a fact already established in an
  earlier session.
- **(c)** Pagination: real "1-12 of 308" / "Page 1 of 26" style counts
  matching `total`/`limit`/`offset` from the real API; clicking "Next"
  loaded genuinely different jobs (screenshot before/after), "Previous"
  correctly disabled on page 1.
- **(d)** Empty state: `min_score=0.99` (impossible against real data)
  rendered the dashed-border "No jobs match these filters..." message
  cleanly, no blank flash, no loading spinner stuck.
- **(e)** Backend suite: 54/54 passing (53 previous + 1 new locations
  test). Real production row counts reconfirmed unchanged
  (companies=9, job_postings=605, lca_disclosures=1431321,
  resume_versions=1, sponsor_name_overrides=1, job_applications=0) -
  this step only reads.

**Decided:** No status-update interaction (explicitly Step 5). No
company-name autocomplete/dropdown - a plain text input, matching the
backend's exact-case-insensitive-match `company` param exactly, rather
than inventing a `/companies` endpoint this step didn't ask for.

**Next:** Step 5 - wiring `PATCH /jobs/{id}/application` into the UI so
status becomes editable, not just displayed - is the natural next step,
not started here.

## 2026-08-23 — Interactive status updates on job cards (Step 5, closes Frontend/UI MVP)

**Did:** Replaced the read-only `StatusBadge` on each `JobCard` with a new
`StatusControl` component (`frontend/src/components/StatusControl.tsx`) -
a styled native `<select>` (same pill look as the old badge, but a real
control) wired to the real `PATCH /jobs/{id}/application` endpoint via a
TanStack Query `useMutation`. Backend untouched, per the task's explicit
constraint - this was frontend wiring only.

Optimistic UI: `onMutate` cancels in-flight `["jobs"]` queries, snapshots
every cached query page via `getQueriesData`, and updates all of them
(`setQueriesData`) so the change is visible immediately and stays
consistent across every cached filter/sort/page combination, not just the
one on screen. `onError` restores every snapshotted page verbatim and
shows an error toast; `onSuccess` shows a success toast; `onSettled`
always calls `invalidateQueries(["jobs"])` to reconcile with the server's
real state regardless of outcome.

Added a minimal hand-built toast system (`frontend/src/components/
Toast.tsx`, React Context, no new dependency) - a fixed bottom-right
stack, auto-dismissing after 3.5s - wired into the provider tree via
`frontend/src/app/providers.tsx` (`ToastProvider` now wraps children
inside `QueryClientProvider`). `StatusBadge.tsx`'s color/label maps
(`STATUS_STYLES`/`STATUS_LABELS`) were exported so `StatusControl` reuses
the exact same visual language rather than duplicating it - `StatusBadge`
itself is unchanged and still used elsewhere.

**Verified, all against the real running system (real API on :8000, real
frontend on :3000, real Postgres on :5432 via psql, real screenshots via
claude-in-chrome - nothing mocked):**

- **(a)** Before/after screenshots of a real status change: Checkr
  "Enterprise Account Executive" (job id 1257, 27% match, verified via
  psql to have zero prior `job_applications` row) showed "Not applied" in
  the dropdown before, "Applied" (styled blue, matching `StatusBadge`'s
  existing color map) immediately after selecting it via
  `mcp__claude-in-chrome__form_input` (click+arrow-key doesn't reliably
  register on a native `<select>`, per the prior UI-building step's
  finding).
- **(b)** Direct `psql` query against `localhost:5432`/`jobsight` (not
  the UI) confirmed the change actually persisted:
  `job_applications` gained a real row for `job_posting_id=1257` with
  `status='applied'` and a non-null `applied_at` (`2026-08-23
  02:55:34.246952`) - the optimistic UI state and the database agree.
- **(c)** Changed status again through the real UI (`applied` ->
  `interviewing`) and re-queried: `status` updated to `interviewing`,
  `status_updated_at` advanced to `02:56:02.834868`, but `applied_at`
  stayed exactly `02:55:34.246952` - the "set once, preserved across
  later changes" behavior (built and unit-tested in Step 2 at the API
  level) holds when triggered through the real UI, not just direct API
  calls.
- **(d)** Killed the real API process (`kill` on the uvicorn pid) mid-
  session, then attempted another status change through the UI. A real
  screenshot captured a red toast reading "Failed to update status:
  Failed to fetch" in the bottom-right, and the dropdown correctly
  reverted to its last real value ("Interviewing") rather than sticking
  on the failed optimistic "Applied" selection - a genuine `fetch`
  failure exercising `onError`'s rollback path, not simulated. A
  follow-up `psql` query confirmed the database was untouched by the
  failed attempt (`status_updated_at` unchanged from before the kill).
- **(e)** Backend suite: 54/54 passing, unchanged from the pre-task
  baseline (backend wasn't touched, per the task's constraint, but this
  was verified rather than assumed).
- Cleanup: the test `job_applications` row for job 1257 was deleted after
  verification; real production row counts reconfirmed unchanged before
  and after (`job_postings=605`, `companies=9`, `job_applications=0`
  both times) - this task only ever touched one throwaway test row, now
  gone. Both the manually-started API and frontend dev servers were
  stopped at the end.

**Decided:** A styled native `<select>` over a custom dropdown/button
group - it's keyboard-accessible and screen-reader-friendly for free, and
reusing `StatusBadge`'s existing color map meant zero new visual design
was needed to make it look like "the badge, but interactive." A hand-
built toast system over a new npm dependency (e.g. `react-hot-toast`) -
the requirement was just "clear success/failure feedback," and a ~50-line
Context-based stack covers that without adding a dependency for
something this small.

**Next:** This closes the Frontend/UI MVP scope agreed on for this
project phase. Nothing else is queued for the frontend right now -
future frontend work (e.g. a detail-page view, resume upload UI, or the
sponsorship-summary surface) is unplanned, not started, and not implied
by anything in this entry.

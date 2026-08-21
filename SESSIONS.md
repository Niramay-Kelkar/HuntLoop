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

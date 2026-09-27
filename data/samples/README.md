# Sample data

Two independent bundled samples live here, seeded by two independent
scripts into two unrelated sets of tables - loading one has no effect on
the other. Both are real subsets of this project's own production data,
not synthetic or fabricated.

## `job_postings_sample.csv.gz` + friends (companies/job_sources/job_locations)

A real, curated sample of scraped job postings, so a fresh
`docker compose up` shows a populated dashboard/job list immediately
instead of an empty one, before a stranger has onboarded any companies or
run their own scrape. Public ATS job-posting data (title, description,
URL, department, location, employment type) - no PII, confirmed by
inspecting the actual `companies`/`job_sources`/`job_postings`/
`job_locations` schemas before including anything.

### How it was generated

194 real postings (~256 KB compressed total across all four files),
covering all 7 implemented ATS platforms - 4 companies per platform (28
companies total), prioritizing companies with a resolved H-1B sponsor
match where possible, up to 8 of the most-recently-scraped postings per
company:

- `companies_sample.csv.gz` (859 bytes) - the 28 companies, keyed by
  `name` (unique). Columns: `name, website, h1b_sponsorship,
  ats_platform, ats_token, careers_url, matched_sponsor_employer_name`.
- `job_sources_sample.csv.gz` (120 bytes) - all 7 real `job_sources` rows
  (`greenhouse_api`, `lever_api`, `workday_api`, `smartrecruiters_api`,
  `ashby_api`, `icims_portal`, `gem_api`), keyed by `name` (unique).
- `job_postings_sample.csv.gz` (~246 KB) - the 194 postings, keyed by
  `job_url` (unique). References companies/sources by `company_name`/
  `source_name`, not the original production integer ids (those ids are
  meaningless once reloaded into a different, empty database and would
  collide with that database's own auto-incrementing sequences). Columns:
  `company_name, source_name, job_title, job_url, department,
  job_description, employment_type, date_posted, is_active, gh_job_id,
  is_relevant, department_category, matched_skills, missing_skills`.
  `is_relevant`/`matched_skills`/`missing_skills` are the REAL values from
  production, not fabricated to look complete - only ~25% of postings
  project-wide have a `matched_skills` result at any given time (see
  CLAUDE.md's skills-matching backlog notes), and this sample honestly
  reflects that partial coverage rather than pretending otherwise.
  `embedding` is deliberately NOT included/NULL for every sample row -
  it's a real column, but a real `vector(384)` value serializes to ~4.7 KB
  of text each, which would balloon this file past 1 MB for no benefit a
  stranger would see without also uploading their own resume (match
  scoring needs both an embedding *and* an active resume - see
  `huntloop.match_scoring`). Anyone who wants live match-scoring against
  this sample data can run the same one-time step a real scrape already
  needs: `docker compose run --rm app python scripts/backfill_embeddings.py`.
- `job_locations_sample.csv.gz` (~7.6 KB) - the 370 real location rows for
  those 194 postings (multi-location postings split into several rows
  each, same as production - see `huntloop.location_normalization`).
  References its parent posting by `job_url`, for the same
  id-portability reason as above.

### Loading it

Runs automatically as part of `docker compose up`, via the one-shot
`seed` service in `docker-compose.yml` (`alembic upgrade head && python
scripts/seed_sample_job_postings.py`) - no manual step needed. Can also be
run manually:

    python scripts/seed_sample_job_postings.py

**Gated on a table-level check, not a per-row one**: before touching
anything, it runs `SELECT COUNT(*) FROM job_postings` - if that's already
non-zero (a previous run of this same script, *or* a real scrape someone
has since run), it logs one line and exits 0 without inserting a single
row. This is deliberately different from the LCA sample's per-row
`ON CONFLICT DO NOTHING` idempotency below: LCA disclosures and this
sample are meant to coexist permanently, but sample job postings are a
placeholder that must never sit alongside - or reappear after - a
stranger's own real scraped data. See
`scripts/seed_sample_job_postings.py`'s own docstring for the full
reasoning.

## `lca_disclosures_sample.csv.gz`

A real subset of DOL LCA (H-1B) disclosure data already ingested into
this project's own `lca_disclosures` table via
`scripts/ingest_lca_disclosures.py` from DOL's own quarterly disclosure
files. It is not synthetic or fabricated data.

U.S. government works of this kind are generally public domain under
17 U.S.C. Β§105, so redistributing a subset here isn't a licensing concern.

### How it was generated

7,176 real rows (~300 KB compressed), covering FY2021-FY2025, made up of:

- Up to 10 of the most-recently-received real rows for each of the 538
  employer names this project's own fuzzy-matching (`find_matching_employers()`)
  had already resolved to a scraped `companies` row at the time this sample
  was built - so a fresh install using this sample can see real sponsor
  badges/salary estimates render for at least some scraped companies, not
  just empty states.
- A random sample of ~2,500 additional rows from the full table, for a
  realistic general distribution (most companies most users will scrape
  won't be in the curated list above).

Columns match `lca_disclosures` exactly: `case_number, employer_name,
employer_name_normalized, trade_name_dba, case_status, job_title, soc_code,
soc_title, worksite_city, worksite_state, worksite_postal_code,
wage_rate_of_pay_from, wage_rate_of_pay_to, wage_unit_of_pay, received_date,
decision_date, fiscal_year, quarter, source_file`.

### Loading it

    python scripts/seed_sample_lca_disclosures.py

See the top-level README's "H-1B sponsorship data (optional)" section for
the full picture, including how to load the real/current/full DOL dataset
instead.

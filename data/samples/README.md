# Sample data

`lca_disclosures_sample.csv.gz` is a real subset of DOL LCA (H-1B) disclosure
data already ingested into this project's own `lca_disclosures` table via
`scripts/ingest_lca_disclosures.py` from DOL's own quarterly disclosure
files. It is not synthetic or fabricated data.

U.S. government works of this kind are generally public domain under
17 U.S.C. Β§105, so redistributing a subset here isn't a licensing concern.

## How it was generated

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

## Loading it

    python scripts/seed_sample_lca_disclosures.py

See the top-level README's "H-1B sponsorship data (optional)" section for
the full picture, including how to load the real/current/full DOL dataset
instead.

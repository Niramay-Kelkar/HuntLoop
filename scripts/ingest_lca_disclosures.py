"""
Ingestion script: loads every modern-format DOL LCA disclosure quarterly
file in data/raw/dol_lca/ into the lca_disclosures table.

Not part of the app's ongoing pipeline - run manually via:

    python scripts/ingest_lca_disclosures.py

Expects source files at data/raw/dol_lca/<file> (see .gitignore - these raw
files aren't committed) and DATABASE_URL configured via .env, same as the
rest of the app. Assumes the lca_disclosures table already exists (via
`alembic upgrade head` - see db_models.py / Step 2's migration).

Only files matching the modern naming convention are ingested:

    LCA_Disclosure_Data_FY<YYYY>_Q<N>.xlsx

fiscal_year and quarter are parsed from the filename itself, not hardcoded.
Explicitly skipped (out of scope):
  - H-1B_Disclosure_Data_FY* - legacy pre-2021 format, different schema.
  - LCA_Appendix_A_* - separate cap-exemption file, not disclosure data.
  - Anything else in the directory (e.g. record-layout docs).

Known data-quality quirks handled here (see the Step 1 audit in SESSIONS.md):
  - Each sheet reports far more rows than actually exist (sheet.max_row is
    padded with thousands of fully-blank trailing rows) - filtered out by
    keeping only rows with a non-null CASE_NUMBER.
  - WAGE_UNIT_OF_PAY occasionally doesn't match the wage magnitude for that
    row (e.g. a annual-sized figure tagged "Week"). Not "fixed" here - the
    raw value is stored as-is; this is left for a later cleaning step.

employer_name_normalized is populated at insert time via
huntloop.matching.normalize.normalize_employer_name - mechanical
normalization only (uppercase, strip periods/commas/whitespace, drop a
trailing legal-entity suffix), not full entity resolution.

case_number has a unique constraint, so re-running this script (whether on
an already-ingested file or across the whole directory) is idempotent -
existing rows are skipped via ON CONFLICT DO NOTHING, not duplicated or
errored on.
"""

import logging
import os
import re
import sys
import time

import pandas as pd
from sqlalchemy import String, create_engine
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from huntloop.settings import DATABASE_URL  # noqa: E402
from huntloop.db_models import LcaDisclosure  # noqa: E402
from huntloop.matching.normalize import normalize_employer_name  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data", "raw", "dol_lca")

FILENAME_PATTERN = re.compile(r"^LCA_Disclosure_Data_FY(\d{4})_Q(\d)\.xlsx$")

# Only these two CASE_STATUS values represent an actually-approved
# sponsorship (see Step 1's audit) - Denied/Withdrawn are excluded.
APPROVED_STATUSES = {"Certified", "Certified - Withdrawn"}

# Chosen to keep ~115K rows to a manageable ~23 batches: few enough round
# trips to be fast, small enough that one bad batch (e.g. a NOT NULL
# violation) doesn't roll back a large chunk of already-good work, and
# small enough to keep the in-memory list-of-dicts per batch modest.
BATCH_SIZE = 5000

DOL_COLUMNS = [
    "CASE_NUMBER",
    "CASE_STATUS",
    "EMPLOYER_NAME",
    "TRADE_NAME_DBA",
    "JOB_TITLE",
    "SOC_CODE",
    "SOC_TITLE",
    "WORKSITE_CITY",
    "WORKSITE_STATE",
    "WORKSITE_POSTAL_CODE",
    "WAGE_RATE_OF_PAY_FROM",
    "WAGE_RATE_OF_PAY_TO",
    "WAGE_UNIT_OF_PAY",
    "RECEIVED_DATE",
    "DECISION_DATE",
]


# DOL's sheets occasionally have a value that overruns its column's declared
# width (e.g. a SOC_CODE cell with the SOC_TITLE accidentally appended in
# FY2024_Q3 - "15-1253 Software Quality Assurance" is 34 chars into a
# VARCHAR(20)). Per the same "store as-is, don't fix" approach already used
# for the WAGE_UNIT_OF_PAY quirk, oversized values are truncated to fit
# rather than dropping the row - but each truncation is logged so it stays
# visible rather than silently losing data.
STRING_COLUMN_MAX_LENGTHS = {
    col.name: col.type.length
    for col in LcaDisclosure.__table__.columns
    if isinstance(col.type, String)
}


def discover_source_files(data_dir):
    """Find modern-format LCA disclosure files, sorted oldest-fiscal-quarter first."""
    matches = []
    for filename in os.listdir(data_dir):
        m = FILENAME_PATTERN.match(filename)
        if not m:
            continue
        fiscal_year, quarter = int(m.group(1)), int(m.group(2))
        matches.append((fiscal_year, quarter, filename))
    matches.sort()
    return matches


def _clean_str(value):
    if pd.isna(value):
        return None
    return str(value).strip()


def _clean_date(value):
    if pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def _clean_number(value):
    if pd.isna(value):
        return None
    return float(value)


def _enforce_column_max_lengths(kwargs, source_file):
    """Truncate any string field that overruns its column's declared width,
    logging each occurrence. See STRING_COLUMN_MAX_LENGTHS above."""
    for field, max_length in STRING_COLUMN_MAX_LENGTHS.items():
        value = kwargs.get(field)
        if value is not None and max_length is not None and len(value) > max_length:
            logger.warning(
                f"[{source_file}] case {kwargs.get('case_number')}: {field} value "
                f"{value!r} ({len(value)} chars) exceeds column max {max_length}; truncating."
            )
            kwargs[field] = value[:max_length]
    return kwargs


def row_to_model_kwargs(row, fiscal_year, quarter, source_file):
    """Map one DOL column-named row to LcaDisclosure model field names."""
    employer_name = _clean_str(row["EMPLOYER_NAME"])
    kwargs = {
        "case_number": _clean_str(row["CASE_NUMBER"]),
        "employer_name": employer_name,
        "employer_name_normalized": (
            normalize_employer_name(employer_name) if employer_name is not None else None
        ),
        "trade_name_dba": _clean_str(row["TRADE_NAME_DBA"]),
        "case_status": _clean_str(row["CASE_STATUS"]),
        "job_title": _clean_str(row["JOB_TITLE"]),
        "soc_code": _clean_str(row["SOC_CODE"]),
        "soc_title": _clean_str(row["SOC_TITLE"]),
        "worksite_city": _clean_str(row["WORKSITE_CITY"]),
        "worksite_state": _clean_str(row["WORKSITE_STATE"]),
        "worksite_postal_code": _clean_str(row["WORKSITE_POSTAL_CODE"]),
        "wage_rate_of_pay_from": _clean_number(row["WAGE_RATE_OF_PAY_FROM"]),
        "wage_rate_of_pay_to": _clean_number(row["WAGE_RATE_OF_PAY_TO"]),
        # Stored as-is, including the known mismatched-unit rows - not
        # "fixed" here.
        "wage_unit_of_pay": _clean_str(row["WAGE_UNIT_OF_PAY"]),
        "received_date": _clean_date(row["RECEIVED_DATE"]),
        "decision_date": _clean_date(row["DECISION_DATE"]),
        "fiscal_year": fiscal_year,
        "quarter": quarter,
        "source_file": source_file,
    }
    return _enforce_column_max_lengths(kwargs, source_file)


def flush_batch(session, batch):
    if not batch:
        return 0, 0
    stmt = pg_insert(LcaDisclosure).values(batch).on_conflict_do_nothing(
        index_elements=["case_number"]
    )
    result = session.execute(stmt)
    session.commit()
    inserted = result.rowcount
    skipped = len(batch) - inserted
    return inserted, skipped


def ingest_file(session, source_path, fiscal_year, quarter, source_file):
    """Ingest one quarterly LCA disclosure file. Returns a summary dict."""
    logger.info(f"Reading {source_path} ...")
    df = pd.read_excel(source_path, usecols=DOL_COLUMNS, engine="openpyxl")
    total_raw_rows = len(df)
    logger.info(f"[{source_file}] Sheet reported {total_raw_rows} rows (includes blank padding rows).")

    # Filter out the blank padding rows - do not trust sheet.max_row.
    df = df[df["CASE_NUMBER"].notna()]
    real_rows = len(df)
    padding_filtered = total_raw_rows - real_rows

    # Filter to only approved-sponsorship statuses.
    df = df[df["CASE_STATUS"].isin(APPROVED_STATUSES)]
    approved_rows = len(df)
    non_approved_filtered = real_rows - approved_rows
    logger.info(
        f"[{source_file}] {approved_rows} approved rows considered "
        f"({padding_filtered} blank padding rows and {non_approved_filtered} "
        f"non-approved rows filtered out)."
    )

    inserted_count = 0
    duplicate_count = 0
    batch = []

    for _, row in df.iterrows():
        batch.append(row_to_model_kwargs(row, fiscal_year, quarter, source_file))
        if len(batch) >= BATCH_SIZE:
            ins, dup = flush_batch(session, batch)
            inserted_count += ins
            duplicate_count += dup
            logger.info(
                f"[{source_file}] Batch flushed: +{ins} inserted, {dup} duplicate(s) skipped "
                f"(running total: {inserted_count} inserted, {duplicate_count} skipped)."
            )
            batch = []

    ins, dup = flush_batch(session, batch)
    inserted_count += ins
    duplicate_count += dup
    if ins or dup:
        logger.info(f"[{source_file}] Final batch flushed: +{ins} inserted, {dup} duplicate(s) skipped.")

    return {
        "source_file": source_file,
        "fiscal_year": fiscal_year,
        "quarter": quarter,
        "sheet_reported_rows": total_raw_rows,
        "padding_filtered": padding_filtered,
        "non_approved_filtered": non_approved_filtered,
        "approved_rows": approved_rows,
        "inserted": inserted_count,
        "skipped": duplicate_count,
    }


def main():
    t0 = time.perf_counter()

    source_files = discover_source_files(DATA_DIR)
    if not source_files:
        logger.warning(f"No modern-format LCA disclosure files found in {DATA_DIR}.")
        return

    logger.info(f"Found {len(source_files)} file(s) to ingest, oldest fiscal year/quarter first:")
    for fiscal_year, quarter, filename in source_files:
        logger.info(f"  - {filename} (FY{fiscal_year} Q{quarter})")

    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    summaries = []
    try:
        for fiscal_year, quarter, filename in source_files:
            source_path = os.path.join(DATA_DIR, filename)
            summary = ingest_file(session, source_path, fiscal_year, quarter, filename)
            summaries.append(summary)
    finally:
        session.close()

    elapsed = time.perf_counter() - t0

    total_inserted = sum(s["inserted"] for s in summaries)
    total_skipped = sum(s["skipped"] for s in summaries)
    total_approved = sum(s["approved_rows"] for s in summaries)

    logger.info("=" * 70)
    logger.info("Per-file summary:")
    for s in summaries:
        logger.info(
            f"  {s['source_file']} (FY{s['fiscal_year']} Q{s['quarter']}): "
            f"{s['approved_rows']} approved rows considered, "
            f"{s['inserted']} inserted, {s['skipped']} skipped as duplicates."
        )
    logger.info("-" * 70)
    logger.info(f"Files processed:            {len(summaries)}")
    logger.info(f"Total approved rows:         {total_approved}")
    logger.info(f"Total rows inserted:         {total_inserted}")
    logger.info(f"Total rows skipped (dupes):  {total_skipped}")
    logger.info(f"Elapsed time:                {elapsed:.1f}s")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()

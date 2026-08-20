"""
One-off ingestion script: loads a single DOL LCA disclosure quarterly file
into the lca_disclosures table.

Not part of the app's ongoing pipeline - run manually, once per quarter file,
via:

    python scripts/ingest_lca_disclosures.py

Expects the source file at data/raw/dol_lca/<SOURCE_FILE> (see .gitignore -
these raw files aren't committed) and DATABASE_URL configured via .env, same
as the rest of the app. Assumes the lca_disclosures table already exists
(via `alembic upgrade head` - see db_models.py / Step 2's migration).

Known data-quality quirks handled here (see the Step 1 audit in SESSIONS.md):
  - The sheet reports far more rows than actually exist (sheet.max_row is
    padded with thousands of fully-blank trailing rows) - filtered out by
    keeping only rows with a non-null CASE_NUMBER.
  - WAGE_UNIT_OF_PAY occasionally doesn't match the wage magnitude for that
    row (e.g. a annual-sized figure tagged "Week"). Not "fixed" here - the
    raw value is stored as-is; this is left for a later cleaning step.

employer_name_normalized is intentionally left NULL - populated in a later
normalization/matching step, not here.
"""

import logging
import os
import sys
import time

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from huntloop.settings import DATABASE_URL  # noqa: E402
from huntloop.db_models import LcaDisclosure  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_FILE = "LCA_Disclosure_Data_FY2025_Q4.xlsx"
SOURCE_PATH = os.path.join(REPO_ROOT, "data", "raw", "dol_lca", SOURCE_FILE)
FISCAL_YEAR = 2025
QUARTER = 4

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


def row_to_model_kwargs(row):
    """Map one DOL column-named row to LcaDisclosure model field names."""
    return {
        "case_number": _clean_str(row["CASE_NUMBER"]),
        "employer_name": _clean_str(row["EMPLOYER_NAME"]),
        "employer_name_normalized": None,  # populated in a later step
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
        "fiscal_year": FISCAL_YEAR,
        "quarter": QUARTER,
        "source_file": SOURCE_FILE,
    }


def main():
    t0 = time.perf_counter()

    logger.info(f"Reading {SOURCE_PATH} ...")
    df = pd.read_excel(SOURCE_PATH, usecols=DOL_COLUMNS, engine="openpyxl")
    total_raw_rows = len(df)
    logger.info(f"Sheet reported {total_raw_rows} rows (includes blank padding rows).")

    # Filter out the blank padding rows - do not trust sheet.max_row.
    df = df[df["CASE_NUMBER"].notna()]
    real_rows = len(df)
    padding_filtered = total_raw_rows - real_rows
    logger.info(f"{real_rows} real rows after dropping {padding_filtered} blank padding rows.")

    # Filter to only approved-sponsorship statuses.
    df = df[df["CASE_STATUS"].isin(APPROVED_STATUSES)]
    approved_rows = len(df)
    non_approved_filtered = real_rows - approved_rows
    logger.info(
        f"{approved_rows} rows with CASE_STATUS in {sorted(APPROVED_STATUSES)} "
        f"({non_approved_filtered} non-approved rows filtered out)."
    )

    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    inserted_count = 0
    duplicate_count = 0
    batch = []

    def flush_batch(batch):
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

    try:
        for _, row in df.iterrows():
            batch.append(row_to_model_kwargs(row))
            if len(batch) >= BATCH_SIZE:
                ins, dup = flush_batch(batch)
                inserted_count += ins
                duplicate_count += dup
                logger.info(
                    f"Batch flushed: +{ins} inserted, {dup} duplicate(s) skipped "
                    f"(running total: {inserted_count} inserted, {duplicate_count} skipped)."
                )
                batch = []

        ins, dup = flush_batch(batch)
        inserted_count += ins
        duplicate_count += dup
        if ins or dup:
            logger.info(f"Final batch flushed: +{ins} inserted, {dup} duplicate(s) skipped.")
    finally:
        session.close()

    elapsed = time.perf_counter() - t0
    logger.info("=" * 60)
    logger.info(f"Source file:               {SOURCE_FILE}")
    logger.info(f"Sheet-reported rows:        {total_raw_rows}")
    logger.info(f"Blank padding rows dropped: {padding_filtered}")
    logger.info(f"Non-approved rows dropped:  {non_approved_filtered}")
    logger.info(f"Approved rows considered:   {approved_rows}")
    logger.info(f"Rows inserted:              {inserted_count}")
    logger.info(f"Rows skipped as duplicates: {duplicate_count}")
    logger.info(f"Elapsed time:               {elapsed:.1f}s")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

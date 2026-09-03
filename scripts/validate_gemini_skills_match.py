"""
Side-by-side validation harness for the Gemini-backend experiment
(huntloop.skills_matching_gemini, 2026-08-29, see SESSIONS.md). NOT part
of the app pipeline, CI, or the daily cron.

Runs match_skills() (single-job call path, same as Steps 4/5/F used)
against the exact same 11 sample jobs as scripts/validate_ollama_skills_match.py
- the 9 Step 4/5 samples plus the two known cases:

  - Duolingo "Senior Data Science Manager, User Growth" - the soft-match
    case (inferred ML / "data analysis and problem solving" should match,
    not drop to missing).
  - Palantir "Deployment Strategist" - the high-embedding-similarity /
    low-literal-overlap outlier that once produced a 53-item full-resume
    dump on Groq.

For EACH job it calls BOTH providers (Groq baseline + Gemini candidate)
and prints them together, so quality drift is visible directly. Reports
per-provider wall-clock latency.

    python scripts/validate_gemini_skills_match.py [--gemini-model MODEL]

Writes scratch_gemini_validation.json (gitignored).
"""
import argparse
import json
import logging
import os
import sys
import time

# Groq's free tier is 8000 TPM. A (resume + 1 job) call is ~2-4K tokens,
# so firing 11 back-to-back would 429 most of them. Pace to stay under.
GROQ_PACING_SECONDS = 35

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from huntloop.settings import DATABASE_URL

logger = logging.getLogger(__name__)

SAMPLE_JOBS = [
    ("palantir", "Software Engineer - Defense Applications"),
    ("palantir", "Software Engineer - Frontend Developer Productivity"),
    ("palantir", "Software Engineer, Internship - Production Infrastructure"),
    ("checkr", "Engineering Manager, Verifications"),
    ("checkr", "Chief of Staff"),
    ("checkr", "AI Conversation Designer"),
    ("wealthfront", "Fraud Operations Specialist"),
    ("wealthfront", "Senior Designer - Editorial, Creative"),
    ("duolingo", "Creative Director, Marketing"),
    ("duolingo", "Senior Data Science Manager, User Growth"),
    ("palantir", "Deployment Strategist"),
]


def _run_provider(fn, resume_text, description):
    t0 = time.monotonic()
    try:
        result = fn(resume_text, description)
        err = None
    except Exception as e:  # DailyQuotaExhausted etc. - record, don't crash the harness
        result, err = None, f"{type(e).__name__}: {e}"
    return result, round(time.monotonic() - t0, 1), err


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gemini-model", help="override GEMINI_MODEL for this run")
    args = ap.parse_args()
    if args.gemini_model:
        os.environ["GEMINI_MODEL"] = args.gemini_model

    from huntloop.skills_matching import match_skills as groq_match, MODEL_NAME as GROQ_MODEL
    from huntloop.skills_matching_gemini import match_skills as gemini_match, MODEL_NAME as GEMINI_MODEL

    print(f"\nGROQ baseline : {GROQ_MODEL}")
    print(f"GEMINI cand.  : {GEMINI_MODEL}\n")

    engine = create_engine(DATABASE_URL, echo=False)
    session = sessionmaker(bind=engine)()

    resume_row = session.execute(
        text("SELECT extracted_text FROM resume_versions WHERE is_active = true")
    ).first()
    if resume_row is None:
        logger.error("No active resume_versions row.")
        return
    resume_text = resume_row[0]

    out = []
    for job_num, (company, job_title) in enumerate(SAMPLE_JOBS):
        if job_num:
            time.sleep(GROQ_PACING_SECONDS)  # keep Groq under 8K TPM
        row = session.execute(
            text(
                """
                SELECT j.job_title, j.job_description,
                       round((1 - (r.embedding <=> j.embedding))::numeric, 4) AS score
                FROM job_postings j
                JOIN companies c ON j.company_id = c.id
                CROSS JOIN (SELECT embedding FROM resume_versions WHERE is_active = true) r
                WHERE c.name = :company AND j.job_title = :job_title
                LIMIT 1
                """
            ),
            {"company": company, "job_title": job_title},
        ).first()
        if row is None:
            print(f"!! no DB row for {company} / {job_title!r} - skipping")
            continue

        title, description, score = row
        groq_res, groq_dt, groq_err = _run_provider(groq_match, resume_text, description)
        gem_res, gem_dt, gem_err = _run_provider(gemini_match, resume_text, description)

        rec = {
            "company": company, "title": title,
            "score": float(score) if score is not None else None,
            "groq": {"latency_s": groq_dt, "error": groq_err, "result": groq_res},
            "gemini": {"latency_s": gem_dt, "error": gem_err, "result": gem_res},
        }
        out.append(rec)

        print(f"\n=== {company} — {title}  (score={score}) ===")
        for label, res, dt, err in (
            ("GROQ  ", groq_res, groq_dt, groq_err),
            ("GEMINI", gem_res, gem_dt, gem_err),
        ):
            if err:
                print(f"  {label} ({dt}s): ERROR {err}")
            elif res is None:
                print(f"  {label} ({dt}s): None (call failed - see warnings)")
            else:
                print(f"  {label} ({dt}s):")
                print(f"      matched: {res['matched_skills']}")
                print(f"      missing: {res['missing_skills']}")

    groq_lat = [r["groq"]["latency_s"] for r in out if r["groq"]["result"] is not None]
    gem_lat = [r["gemini"]["latency_s"] for r in out if r["gemini"]["result"] is not None]
    print(f"\n--- latency (successful calls) ---")
    if groq_lat:
        print(f"  Groq  : n={len(groq_lat)} mean={sum(groq_lat)/len(groq_lat):.1f}s")
    if gem_lat:
        print(f"  Gemini: n={len(gem_lat)} mean={sum(gem_lat)/len(gem_lat):.1f}s")

    dest = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scratch_gemini_validation.json",
    )
    with open(dest, "w") as f:
        json.dump({"groq_model": GROQ_MODEL, "gemini_model": GEMINI_MODEL, "jobs": out}, f, indent=2)
    print(f"\nwrote {dest}")
    session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    main()

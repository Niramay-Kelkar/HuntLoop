"""
Side-by-side validation harness for the Groq gpt-oss-120b capacity stage
(huntloop.skills_matching_groq_120b, 2026-09-04, see SESSIONS.md). NOT
part of the app pipeline, CI, or the daily cron.

Unlike every prior validation harness (Gemini, Mistral, Ollama), this is a
SAME-PROVIDER comparison: openai/gpt-oss-120b against the existing
production openai/gpt-oss-20b baseline, not against a different provider.
Both models are confirmed (2026-09-04, see SESSIONS.md) to hold
INDEPENDENT rate-limit buckets on the same Groq account/key, so calling
both in the same run does not risk starving the production 20b path's
real daily budget beyond this run's own 11 extra requests.

Runs match_skills() (single-job call path, same as every prior
validation) against the exact same 11 sample jobs as
scripts/validate_gemini_skills_match.py / validate_mistral_skills_match.py
- the 9 Step 4/5 samples plus the two known cases:

  - Duolingo "Senior Data Science Manager, User Growth" - the soft-match
    case (inferred ML / "data analysis and problem solving" should match,
    not drop to missing).
  - Palantir "Deployment Strategist" - the high-embedding-similarity /
    low-literal-overlap outlier that once produced a 53-item full-resume
    dump on Groq's 20b model.

For EACH job it calls BOTH models (20b baseline + 120b candidate) and
prints them together, so quality drift is visible directly. Reports
per-model wall-clock latency and any MAX_PLAUSIBLE_MATCHED_SKILLS
backstop triggers (visible as a WARNING + a None result).

    python scripts/validate_groq_120b_skills_match.py

Writes scratch_groq_120b_validation.json (gitignored).
"""
import json
import logging
import os
import sys
import time

# Each model's own live-confirmed TPM (8,000, see SESSIONS.md) governs a
# single ~2-4K-token call comfortably, but pacing is still applied between
# JOB iterations (affecting both models equally) to stay well clear of
# any transient throttling, same spirit as the Gemini/Mistral harnesses.
PACING_SECONDS = 20

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


def _run_model(fn, resume_text, description):
    t0 = time.monotonic()
    try:
        result = fn(resume_text, description)
        err = None
    except Exception as e:  # DailyQuotaExhausted etc. - record, don't crash the harness
        result, err = None, f"{type(e).__name__}: {e}"
    return result, round(time.monotonic() - t0, 1), err


def main():
    from huntloop.skills_matching import match_skills as m20b, MODEL_NAME as MODEL_20B
    from huntloop.skills_matching_groq_120b import match_skills as m120b, MODEL_NAME as MODEL_120B

    print(f"\nGROQ 20b baseline : {MODEL_20B}")
    print(f"GROQ 120b cand.   : {MODEL_120B}\n")

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
            time.sleep(PACING_SECONDS)
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
        r20b, dt20b, err20b = _run_model(m20b, resume_text, description)
        r120b, dt120b, err120b = _run_model(m120b, resume_text, description)

        rec = {
            "company": company, "title": title,
            "score": float(score) if score is not None else None,
            "groq_20b": {"latency_s": dt20b, "error": err20b, "result": r20b},
            "groq_120b": {"latency_s": dt120b, "error": err120b, "result": r120b},
        }
        out.append(rec)

        print(f"\n=== {company} — {title}  (score={score}) ===")
        for label, res, dt, err in (
            ("20b ", r20b, dt20b, err20b),
            ("120b", r120b, dt120b, err120b),
        ):
            if err:
                print(f"  {label} ({dt}s): ERROR {err}")
            elif res is None:
                print(f"  {label} ({dt}s): None (call failed - see warnings)")
            else:
                print(f"  {label} ({dt}s):")
                print(f"      matched: {res['matched_skills']}")
                print(f"      missing: {res['missing_skills']}")

    lat20 = [r["groq_20b"]["latency_s"] for r in out if r["groq_20b"]["result"] is not None]
    lat120 = [r["groq_120b"]["latency_s"] for r in out if r["groq_120b"]["result"] is not None]
    print("\n--- latency (successful calls) ---")
    if lat20:
        print(f"  20b : n={len(lat20)} mean={sum(lat20)/len(lat20):.1f}s")
    if lat120:
        print(f"  120b: n={len(lat120)} mean={sum(lat120)/len(lat120):.1f}s")

    dest = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scratch_groq_120b_validation.json",
    )
    with open(dest, "w") as f:
        json.dump({"model_20b": MODEL_20B, "model_120b": MODEL_120B, "jobs": out}, f, indent=2)
    print(f"\nwrote {dest}")
    session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    main()

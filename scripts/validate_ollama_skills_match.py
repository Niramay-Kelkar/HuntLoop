"""
Model-validation harness for the Ollama-backend experiment
(huntloop.skills_matching_ollama, 2026-08-29, see SESSIONS.md). NOT part
of the app pipeline, CI, or the daily cron - a one-off comparison tool.
Production skills-matching stays on Groq (huntloop.skills_matching);
this harness's findings are why (local models on this Intel/8GB machine
were not good enough - full writeup in SESSIONS.md).

Runs match_skills() (single-job, same call path Step 4/5's Groq
validation used) against the exact 9 sample jobs from
scripts/sample_skills_match.py PLUS the two specific known cases the
migration task calls out:

  - Duolingo "Senior Data Science Manager, User Growth" - the soft-match
    case Groq caught correctly ("data analysis and problem solving" /
    inferred ML skills matched rather than dropped to missing).
  - Palantir "Deployment Strategist" - the high-embedding-similarity /
    low-literal-skill-overlap outlier that produced a 53-item
    full-resume-dump on one Groq batch run.

Reports, per job: wall-clock latency, matched_skills, missing_skills.
Also samples system memory + this-process/ollama CPU during the run.

    python scripts/validate_ollama_skills_match.py [--model MODEL]
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time

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
    # The two known cases the migration task explicitly requires:
    ("duolingo", "Senior Data Science Manager, User Growth"),
    ("palantir", "Deployment Strategist"),
]


def _mem_snapshot() -> str:
    """Rough system memory + ollama RSS snapshot, macOS-friendly."""
    try:
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=10).stdout
        page_size = 16384
        free = inactive = 0
        for line in vm.splitlines():
            if "page size of" in line:
                page_size = int(line.split()[-2])
            if line.startswith("Pages free:"):
                free = int(line.split()[-1].rstrip("."))
            if line.startswith("Pages inactive:"):
                inactive = int(line.split()[-1].rstrip("."))
        avail_gb = (free + inactive) * page_size / 1e9
    except Exception:
        avail_gb = float("nan")

    ollama_rss_gb = float("nan")
    try:
        ps = subprocess.run(
            ["ps", "-Ao", "rss,comm"], capture_output=True, text=True, timeout=10
        ).stdout
        rss_kb = sum(
            int(l.split()[0]) for l in ps.splitlines()[1:] if "ollama" in l.lower()
        )
        ollama_rss_gb = rss_kb / 1e6
    except Exception:
        pass

    return f"sys_avail~{avail_gb:.1f}GB  ollama_rss~{ollama_rss_gb:.1f}GB"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="override OLLAMA_MODEL for this run")
    args = ap.parse_args()
    if args.model:
        os.environ["OLLAMA_MODEL"] = args.model

    # import AFTER possibly setting OLLAMA_MODEL (module reads it at import).
    # This is the experimental Ollama backend, kept separate from the
    # production Groq huntloop.skills_matching (see SESSIONS.md 2026-08-29).
    from huntloop.skills_matching_ollama import match_skills, MODEL_NAME

    engine = create_engine(DATABASE_URL, echo=False)
    session = sessionmaker(bind=engine)()

    print(f"\nMODEL: {MODEL_NAME}")
    print(f"BEFORE: {_mem_snapshot()}\n")

    resume_row = session.execute(
        text("SELECT extracted_text FROM resume_versions WHERE is_active = true")
    ).first()
    if resume_row is None:
        logger.error("No active resume_versions row.")
        return
    resume_text = resume_row[0]

    out = []
    total_start = time.monotonic()
    for company, job_title in SAMPLE_JOBS:
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
        t0 = time.monotonic()
        result = match_skills(resume_text, description)
        dt = time.monotonic() - t0

        rec = {
            "company": company,
            "title": title,
            "score": float(score) if score is not None else None,
            "latency_s": round(dt, 1),
            "result": result,
        }
        out.append(rec)
        print(f"\n=== {company} — {title}  (score={score}, {dt:.1f}s) ===")
        print(f"    {_mem_snapshot()}")
        if result is None:
            print("    RESULT: None (call failed - see warnings)")
        else:
            print(f"    matched_skills: {result['matched_skills']}")
            print(f"    missing_skills: {result['missing_skills']}")

    total = time.monotonic() - total_start
    n = len(out)
    print(f"\nTOTAL: {n} jobs in {total:.1f}s  (mean {total / n:.1f}s/job)" if n else "\nno jobs run")
    print(f"AFTER: {_mem_snapshot()}")

    dest = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scratch_ollama_validation.json",
    )
    with open(dest, "w") as f:
        json.dump({"model": MODEL_NAME, "total_s": round(total, 1), "jobs": out}, f, indent=2)
    print(f"\nwrote {dest}")
    session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    main()

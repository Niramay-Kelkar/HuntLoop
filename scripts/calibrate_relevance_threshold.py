"""
SUPERSEDED (2026-09-03): the embedding-similarity relevance gate this
script calibrated was replaced by a title-only blue-collar denylist -
EMBEDDING_SIMILARITY_THRESHOLD is no longer used for the is_relevant
decision (see huntloop-architecture-decisions.md's relevance-gate
redesign entry / SESSIONS.md). Kept as a historical artifact of the old
approach; not updated to keep working.

One-off calibration script: measures real cosine similarities between
huntloop.relevance_filter.REFERENCE_TEXT and a handful of real
job_postings rows already in the dataset with a known right answer, to
choose EMBEDDING_SIMILARITY_THRESHOLD from actual evidence rather than a
guess - same approach as every other threshold in this project (Phase
1's fuzzy-match threshold, the wage-unit filter, etc. - see CLAUDE.md).

Not part of the app's ongoing pipeline - run manually, once, to pick the
threshold (already done - see SESSIONS.md for the printed output this
produced and the resulting value baked into relevance_filter.py). Needs
a working torch/sentence-transformers install, same constraint as
scripts/backfill_embeddings.py - run inside the app Docker image:

    docker compose build app
    docker compose run --rm \\
      -e DATABASE_URL="postgresql+psycopg2://<user>:<password>@host.docker.internal:5432/<db>" \\
      app python scripts/calibrate_relevance_threshold.py
"""
import os
import sys

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import JobPosting, Company
from huntloop.embeddings import embed_texts
from huntloop.relevance_filter import REFERENCE_TEXT, cosine_similarity
from huntloop.settings import DATABASE_URL

# (company token, job_title, expected relevance) - real rows already in
# the dataset, called out explicitly in the task that asked for this
# filter.
KNOWN_EXAMPLES = [
    ("wealthfront", "Fraud Operations Specialist", False),
    ("checkr", "Chief of Staff", False),
    ("duolingo", "Creative Director, Marketing", False),
    ("palantir", "Software Engineer - Apollo Platform", True),
    ("palantir", "Software Engineer - Core Interfaces", True),
    ("palantir", "Software Engineer - Defense Applications", True),
    ("duolingo", "Senior Data Science Manager, User Growth", True),
    ("palantir", "Platform Engineer - Identity Infrastructure", True),
]


def main():
    engine = create_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(bind=engine)
    session = Session()

    reference_embedding = embed_texts([REFERENCE_TEXT])[0]

    print(f"{'expected':>9}  {'similarity':>10}  company / title")
    print("-" * 80)
    results = []
    for company_token, job_title, expected in KNOWN_EXAMPLES:
        row = (
            session.query(JobPosting)
            .join(Company, JobPosting.company_id == Company.id)
            .filter(Company.name == company_token, JobPosting.job_title == job_title)
            .first()
        )
        if row is None:
            print(f"  MISSING: {company_token} / {job_title}")
            continue
        text = f"{row.job_title}\n{row.job_description or ''}"
        job_embedding = embed_texts([text])[0]
        sim = cosine_similarity(reference_embedding, job_embedding)
        results.append((expected, sim))
        print(f"{str(expected):>9}  {sim:>10.4f}  {company_token} / {job_title}")

    session.close()

    relevant_sims = [s for e, s in results if e]
    irrelevant_sims = [s for e, s in results if not e]
    if relevant_sims and irrelevant_sims:
        print()
        print(f"known-relevant similarities:   min={min(relevant_sims):.4f} max={max(relevant_sims):.4f}")
        print(f"known-irrelevant similarities: min={min(irrelevant_sims):.4f} max={max(irrelevant_sims):.4f}")
        gap_low = min(relevant_sims)
        gap_high = max(irrelevant_sims)
        if gap_low > gap_high:
            print(f"Clean separation - any threshold in ({gap_high:.4f}, {gap_low:.4f}) cleanly splits these examples.")
        else:
            print("WARNING: no clean separation between known-relevant and known-irrelevant similarities.")


if __name__ == "__main__":
    main()

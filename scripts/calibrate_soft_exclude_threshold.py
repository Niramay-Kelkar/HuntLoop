"""
One-off calibration script for the SOFT-exclude rescue threshold added to
huntloop.relevance_filter 2026-08-30 (see SESSIONS.md).

Background: EXCLUDE_KEYWORDS used to be an absolute override. Real
evidence showed two entries - "customer success" and "solutions
consultant" - wrongly kill genuinely technical, customer-facing
engineering roles (Palantir "Forward Deployed Enablement Engineer -
Customer Success", Figma "Enterprise Solutions Consultant") while
correctly excluding real non-technical ones (Rubrik "Customer Success
Engineer" post-deployment support, "Customer Success Manager", etc.).

Those two are now SOFT excludes: a title matching them is only excluded
if its category-reference-text embedding similarity (the SAME signal
classify_relevance already uses - REFERENCE_TEXT vs title+description,
NOT the resume-vs-job embedding) is also below SOFT_EXCLUDE_RESCUE_THRESHOLD.

This script measures that similarity for EVERY real "customer success" /
"solutions consultant" titled job_postings row so the threshold can be
picked from the real distribution, split by actually reading the
descriptions - same discipline as scripts/calibrate_relevance_threshold.py.

Needs torch/sentence-transformers - run in the app Docker image:

    docker compose run --rm --build \\
      -e DATABASE_URL="postgresql+psycopg2://<user>:<pw>@host.docker.internal:5432/<db>" \\
      app python scripts/calibrate_soft_exclude_threshold.py
"""
import json
import os
import re
import sys

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from huntloop.embeddings import embed_texts
from huntloop.relevance_filter import REFERENCE_TEXT, cosine_similarity, SOFT_EXCLUDE_KEYWORDS
from huntloop.settings import DATABASE_URL

OUT_PATH = os.environ.get(
    "CALIBRATION_OUT",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scratch_soft_exclude_calibration.json"),
)


def _snippet(desc: str, n: int = 600) -> str:
    t = re.sub(r"<[^>]+>", " ", desc or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t[:n]


def main():
    engine = create_engine(DATABASE_URL, echo=False)
    session = sessionmaker(bind=engine)()

    like_clause = " OR ".join(f"lower(jp.job_title) LIKE :k{i}" for i in range(len(SOFT_EXCLUDE_KEYWORDS)))
    params = {f"k{i}": f"%{kw}%" for i, kw in enumerate(SOFT_EXCLUDE_KEYWORDS)}
    rows = session.execute(text(f"""
        SELECT jp.id, co.name AS company, jp.job_title, jp.job_description,
               jp.is_relevant
        FROM job_postings jp
        LEFT JOIN companies co ON co.id = jp.company_id
        WHERE {like_clause}
        ORDER BY jp.id
    """), params).fetchall()

    ref_emb = embed_texts([REFERENCE_TEXT])[0]

    out = []
    # batch the embedding calls
    B = 64
    for start in range(0, len(rows), B):
        chunk = rows[start:start + B]
        texts = [f"{r.job_title}\n{r.job_description or ''}" for r in chunk]
        embs = embed_texts(texts)
        for r, emb in zip(chunk, embs):
            sim = cosine_similarity(ref_emb, emb)
            out.append({
                "id": r.id, "company": r.company, "job_title": r.job_title,
                "is_relevant_now": r.is_relevant, "similarity": round(sim, 4),
                "snippet": _snippet(r.job_description),
            })

    out.sort(key=lambda d: d["similarity"])
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2)

    print(f"{len(out)} soft-exclude-titled rows; similarities "
          f"{out[0]['similarity']:.4f} .. {out[-1]['similarity']:.4f}")
    print(f"wrote {OUT_PATH}")
    for d in out:
        print(f"  sim={d['similarity']:.4f} rel_now={str(d['is_relevant_now']):<5} "
              f"{(d['company'] or '?'):<16} {d['job_title'][:70]}")

    session.close()


if __name__ == "__main__":
    main()

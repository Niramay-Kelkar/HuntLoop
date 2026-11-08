"""
The actual Modal App/Image/Function definitions for resume parsing +
embedding generation - deployed via
`modal deploy src/huntloop/modal_resume_functions.py` (re-run after any
change to this file). huntloop.modal_resume_processing (the orchestration
module huntloop.api.routers.resumes actually calls - budget ledger,
fallback, SQLAlchemy) is deliberately kept OUT of this file, and this
file is deliberately kept minimal - see below for why.

Modal imports this ENTIRE module inside the remote container just to
discover process_resume_remote/embed_text_remote (the import happens
before either function is actually invoked) - so every module-level
import here must succeed inside that container, built from `image`
below, not just locally. Confirmed live, the hard way, not assumed: the
first real deploy+invoke attempt put `from dotenv import load_dotenv`
and `from sqlalchemy.orm import Session` (needed only by the local
orchestration side, for env loading and the budget ledger) at this same
module's top level, which crash-looped the remote container with
`ModuleNotFoundError: No module named 'dotenv'` (then, after adding
python-dotenv to `image` and redeploying, 'sqlalchemy') - neither package
is used by, or installed for, the remote side at all. This is why the
split exists: this file's only module-level imports are `modal` itself
plus the pdfplumber/sentence-transformers imports needed by the function
bodies (both imported lazily inside those bodies anyway, to keep this
module importable even in environments without torch - see
huntloop.embeddings' own docstring), and `huntloop.modal_resume_processing`
imports only the two name constants from here, never anything that
would pull sqlalchemy/dotenv/db_models into this file.
"""
import tempfile

import modal

MODAL_APP_NAME = "huntloop-resume-processing"
PROCESS_RESUME_FUNCTION = "process_resume_remote"
EMBED_TEXT_FUNCTION = "embed_text_remote"
DEEP_DIVE_FUNCTION = "deep_dive_remote"
INVOCATION_TIMEOUT_SECONDS = 60

# The Deep-Dive is a single heavier LLM call with a three-provider
# failover chain behind it (huntloop.deep_dive) - a cold provider plus
# two fallthroughs can legitimately take minutes, where resume
# parsing/embedding is seconds. 600s is generous headroom over the
# ~120s-per-provider worst case rather than a measured figure.
DEEP_DIVE_TIMEOUT_SECONDS = 600

# Modal's own name for the Secret holding the LLM provider keys. The
# secret must contain GROQ_API_KEY and GEMINI_API_KEY; it is created out
# of band by the project owner (`modal secret create huntloop-llm ...`
# or the Modal dashboard) and no key value appears anywhere in this
# repo. This is the whole reason the Deep-Dive runs on Modal at all:
# the public demo API has no LLM keys in its own environment (see
# render.yaml), so the keys live only in Modal's secret store and are
# injected as env vars into this container, where huntloop.deep_dive
# reads them at call time.
LLM_SECRET_NAME = "huntloop-llm"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "pdfplumber==0.11.9",
        "sentence-transformers==6.0.0",
        # Needed by huntloop.deep_dive's provider calls (Groq SDK /
        # Gemini REST). Added to the Modal image ONLY - both are already
        # in requirements.txt and requirements-demo.txt for the API side,
        # which needs them for other reasons, so nothing else changes.
        "groq==1.6.0",
        "requests==2.34.2",
    )
    .add_local_python_source("huntloop")
)

app = modal.App(MODAL_APP_NAME)


@app.function(image=image, timeout=INVOCATION_TIMEOUT_SECONDS, name=PROCESS_RESUME_FUNCTION)
def process_resume_remote(pdf_bytes: bytes) -> dict:
    """Runs on Modal. Calls huntloop.resume_ingestion.extract_text() and
    huntloop.embeddings.embed_text() exactly as the original in-process
    upload path did - same functions, same order, same behavior."""
    from huntloop.resume_ingestion import extract_text

    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf_bytes)
        f.flush()
        extracted_text = extract_text(f.name)

    if not extracted_text.strip():
        # Same short-circuit the original inline path had via its 422
        # check in huntloop.api.routers.resumes - no point embedding
        # empty text, and the caller re-applies that same 422 check
        # against extracted_text itself.
        return {"extracted_text": extracted_text, "embedding": None}

    from huntloop.embeddings import embed_text

    embedding = embed_text(extracted_text)
    return {"extracted_text": extracted_text, "embedding": embedding}


@app.function(image=image, timeout=INVOCATION_TIMEOUT_SECONDS, name=EMBED_TEXT_FUNCTION)
def embed_text_remote(text: str) -> list[float]:
    """Runs on Modal. Embedding-only - used by PATCH /resumes/{id}/activate's
    backfill-missing-embedding branch, where the text is already
    extracted and stored and only the embedding needs computing."""
    from huntloop.embeddings import embed_text

    return embed_text(text)


@app.function(
    image=image,
    timeout=DEEP_DIVE_TIMEOUT_SECONDS,
    name=DEEP_DIVE_FUNCTION,
    secrets=[modal.Secret.from_name(LLM_SECRET_NAME)],
    # retries=0 is Modal's default, stated EXPLICITLY here because it is
    # load-bearing for this function specifically and must not be
    # "helpfully" raised later: huntloop.modal_deep_dive reserves budget
    # from the modal_usage ledger once per DISPATCH (see
    # huntloop.modal_resume_processing._reserve_invocation_slot). A
    # silent Modal-side retry would run the whole provider chain again -
    # real compute, real LLM quota - against a slot that was never
    # reserved, so the ledger would under-count exactly when something
    # is already going wrong. A failed Deep-Dive is instead surfaced as
    # `failed` on its resume_deep_dives row, and the user can retry it
    # explicitly (which reserves its own slot).
    retries=0,
)
def deep_dive_remote(payload: dict) -> dict:
    """Runs on Modal. The Resume Deep-Dive: one heavier LLM analysis of
    one resume against one job posting, via huntloop.deep_dive's
    three-provider chain reading GROQ_API_KEY/GEMINI_API_KEY from the
    `huntloop-llm` Modal Secret attached above.

    Takes plain data only (no DB handle, no ORM object, nothing that
    needs a connection string) and TOUCHES NO DATABASE - this container
    has neither sqlalchemy nor a DATABASE_URL, deliberately. The caller
    (huntloop.api.routers.deep_dive) is the only thing that writes the
    result anywhere, after it polls the call and re-validates the shape.

    Raises huntloop.deep_dive.AllDeepDiveProvidersFailed if every
    provider fails - deliberately allowed to propagate, so the polling
    caller records a real `failed` reason instead of a placeholder
    result."""
    from huntloop.deep_dive import generate_deep_dive

    return generate_deep_dive(
        resume_text=payload.get("resume_text", ""),
        job_title=payload.get("job_title", ""),
        company_name=payload.get("company_name", ""),
        job_description=payload.get("job_description", ""),
        matched_skills=payload.get("matched_skills"),
        missing_skills=payload.get("missing_skills"),
    )

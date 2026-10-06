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
INVOCATION_TIMEOUT_SECONDS = 60

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("pdfplumber==0.11.9", "sentence-transformers==6.0.0")
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

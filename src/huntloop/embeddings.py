"""
all-MiniLM-L6-v2 sentence-embedding model wrapper (see requirements.txt:
sentence-transformers, pinned). CPU-only, 384-dim output - small, fast,
free, matches the project's open-source/no-paid-API constraint. All text
is run through huntloop.text_cleaning.clean_text() before embedding, so
HTML markup (job descriptions) and pdfminer glyph artifacts (resume
text) never reach the model.

Only importable where sentence-transformers/torch are actually installed
and runnable. This project's local dev venv (macOS, Intel, Python 3.13)
cannot install a working torch build - PyPI's last macOS-x86_64 torch
wheel (2.2.2) only goes up to Python 3.12, confirmed by trying (see
SESSIONS.md, 2026-08-22). Embedding computation instead runs inside the
`app` Docker image (Linux, real torch wheels exist for cp313), pointed
at the host's real Postgres via DATABASE_URL=...@host.docker.internal -
see scripts/backfill_embeddings.py and README.md for the exact command.
"""
import logging

from sentence_transformers import SentenceTransformer

from huntloop.text_cleaning import clean_text

logger = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Confirmed against the model's own published config
# (1_Pooling/config.json: "word_embedding_dimension": 384 on
# https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2), not
# assumed. Must match huntloop.db_models.EMBEDDING_DIM.
EMBEDDING_DIM = 384

_model = None


def get_model() -> SentenceTransformer:
    """Lazily load the model once per process - loading it is the
    expensive part (downloads/reads the model files), not encoding."""
    global _model
    if _model is None:
        logger.info(f"Loading embedding model {MODEL_NAME} (CPU)...")
        _model = SentenceTransformer(MODEL_NAME, device="cpu")
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Clean and embed a batch of texts. Returns one EMBEDDING_DIM-length
    vector per input text, in the same order."""
    cleaned = [clean_text(t) for t in texts]
    model = get_model()
    embeddings = model.encode(cleaned, convert_to_numpy=True, show_progress_bar=False)
    return embeddings.tolist()


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]

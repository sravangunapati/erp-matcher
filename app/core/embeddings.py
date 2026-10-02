from functools import lru_cache
from pathlib import Path

import truststore
from fastembed import TextEmbedding

from app.config import settings

truststore.inject_into_ssl()

@lru_cache(maxsize=1)
def model() -> TextEmbedding:
    """Loaded once, on first use (~1 s after the first download)."""
    return TextEmbedding(settings.embedding_model, cache_dir=str(Path(settings.embedding_cache).expanduser()))


def embed(texts: list[str]) -> list[list[float]]:
    """Unit-length vectors, so a dot product is the cosine similarity."""
    return [v.tolist() for v in model().embed(texts)]
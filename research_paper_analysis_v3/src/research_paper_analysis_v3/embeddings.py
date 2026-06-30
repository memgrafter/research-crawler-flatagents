"""Remote embedding via llama.cpp on localhost.

Uses jina-embeddings-v5-text-nano served through llama-server's
OpenAI-compatible /v1/embeddings endpoint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np
from openai import AsyncOpenAI


@dataclass(frozen=True)
class EmbeddingConfig:
    """Embedding service configuration."""

    base_url: str = field(default_factory=lambda: os.environ.get(
        "EMBEDDING_BASE_URL",
        "http://localhost:8082/v1",
    ))
    model: str = "jina-embeddings-v5-text-nano"
    api_key: str = "llama.cpp"
    batch_size: int = 64
    timeout: float = 30.0


_client_cache: dict[str, AsyncOpenAI] = {}


def _get_client(cfg: EmbeddingConfig) -> AsyncOpenAI:
    """Cache client per base_url to avoid recreating HTTP pools."""
    if cfg.base_url not in _client_cache:
        _client_cache[cfg.base_url] = AsyncOpenAI(
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            timeout=cfg.timeout,
        )
    return _client_cache[cfg.base_url]


async def encode(
    texts: list[str],
    *,
    cfg: EmbeddingConfig | None = None,
) -> np.ndarray:
    """Encode texts into embedding vectors.

    Args:
        texts: List of strings to embed.
        cfg: Configuration; uses defaults if not provided.

    Returns:
        (N, D) float32 array where N = len(texts).
    """
    cfg = cfg or EmbeddingConfig()
    client = _get_client(cfg)

    embeddings: list[list[float]] = []
    for i in range(0, len(texts), cfg.batch_size):
        batch = texts[i : i + cfg.batch_size]
        resp = await client.embeddings.create(
            model=cfg.model,
            input=batch,
        )
        embeddings.extend([e.embedding for e in resp.data])

    return np.array(embeddings, dtype=np.float32)


async def max_similarity(
    terms: list[str],
    anchors: list[str],
    *,
    cfg: EmbeddingConfig | None = None,
) -> np.ndarray:
    """Compute max cosine similarity of each term against all anchors.

    Args:
        terms: Terms to score (e.g. mined n-grams).
        anchors: Anchor concepts (e.g. ML/LLM keywords).
        cfg: Configuration; uses defaults if not provided.

    Returns:
        1-D array of length len(terms) with max similarity per term.
    """
    cfg = cfg or EmbeddingConfig()
    term_emb = await encode(terms, cfg=cfg)
    anchor_emb = await encode(anchors, cfg=cfg)

    sims = term_emb @ anchor_emb.T  # jina vectors are L2-normalized
    return sims.max(axis=1)

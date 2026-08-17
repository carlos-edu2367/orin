"""Embeddings from a local Ollama instance.

This is the default: it keeps the promise that nothing leaves the machine, and
it adds nothing to the release. Every failure — no daemon, no model, a malformed
answer — is translated into ``EmbeddingUnavailable`` so the caller has exactly
one thing to handle.
"""
from __future__ import annotations

from typing import Sequence

import httpx

from ..models import EmbedderIdentity
from ..ports import EmbeddingUnavailable


DEFAULT_MODEL = "nomic-embed-text"
EMBED_TIMEOUT_SECONDS = 120


class OllamaEmbedder:
    def __init__(self, *, base_url: str, model: str = DEFAULT_MODEL, client: httpx.Client | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = client or httpx.Client(timeout=EMBED_TIMEOUT_SECONDS)
        self._dim = 0

    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity(embedder_id="ollama", model=self._model, dim=self._dim)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        payload = {"model": self._model, "input": list(texts)}
        try:
            response = self._client.post(f"{self._base_url}/api/embed", json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise EmbeddingUnavailable(f"ollama embeddings unavailable: {error}") from error
        vectors = body.get("embeddings") if isinstance(body, dict) else None
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingUnavailable("ollama returned an unusable embeddings payload")
        result = [[float(value) for value in vector] for vector in vectors]
        if result and result[0]:
            self._dim = len(result[0])
        return result


__all__ = ["DEFAULT_MODEL", "OllamaEmbedder"]

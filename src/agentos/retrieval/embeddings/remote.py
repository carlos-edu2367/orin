"""Embeddings from an OpenAI-shaped remote endpoint.

Opt-in only. Choosing this sends indexed file content to a third party, which is
why the secret denylist in ``filters.py`` runs before the chunker rather than
after retrieval. The key travels in a header and never appears in a URL or in an
error message.
"""
from __future__ import annotations

from typing import Sequence

import httpx

from ..models import EmbedderIdentity
from ..ports import EmbeddingUnavailable


DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
EMBED_TIMEOUT_SECONDS = 60


class RemoteEmbedder:
    def __init__(self, *, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL, api_key: str, client: httpx.Client | None = None) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must be a non-blank string")
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=EMBED_TIMEOUT_SECONDS)
        self._dim = 0

    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity(embedder_id="remote", model=self._model, dim=self._dim)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            response = self._client.post(
                f"{self._base_url}/embeddings",
                json={"model": self._model, "input": list(texts)},
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise EmbeddingUnavailable(f"remote embeddings unavailable: {type(error).__name__}") from None
        entries = body.get("data") if isinstance(body, dict) else None
        if not isinstance(entries, list) or len(entries) != len(texts):
            raise EmbeddingUnavailable("the remote endpoint returned an unusable embeddings payload")
        ordered = sorted(entries, key=lambda entry: int(entry.get("index", 0)))
        result = [[float(value) for value in entry.get("embedding", [])] for entry in ordered]
        if any(not vector for vector in result):
            raise EmbeddingUnavailable("the remote endpoint returned an empty embedding")
        self._dim = len(result[0])
        return result


__all__ = ["DEFAULT_BASE_URL", "DEFAULT_MODEL", "RemoteEmbedder"]

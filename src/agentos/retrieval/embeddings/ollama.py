"""Embeddings from a local Ollama instance.

This is the default: it keeps the promise that nothing leaves the machine, and
it adds nothing to the release. Every failure — no daemon, no model, a malformed
answer — is translated into ``EmbeddingUnavailable`` so the caller has exactly
one thing to handle.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence

import httpx

from ..models import EmbedderIdentity
from ..ports import EmbeddingUnavailable


DEFAULT_MODEL = "nomic-embed-text"
EMBED_TIMEOUT_SECONDS = 30.0
EMBED_CONNECT_TIMEOUT_SECONDS = 3.0
EMBED_TIMEOUT = httpx.Timeout(EMBED_TIMEOUT_SECONDS, connect=EMBED_CONNECT_TIMEOUT_SECONDS)
MODEL_NOT_FOUND_COOLDOWN_SECONDS = 300.0
TRANSIENT_FAILURE_COOLDOWN_SECONDS = 60.0


class OllamaEmbedder:
    def __init__(
        self,
        *,
        base_url: str,
        model: str = DEFAULT_MODEL,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = client or httpx.Client(timeout=EMBED_TIMEOUT)
        self._clock = clock
        self._dim = 0
        self._unavailable_until = 0.0
        self._last_reason = ""

    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity(embedder_id="ollama", model=self._model, dim=self._dim)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        now = self._clock()
        if now < self._unavailable_until:
            raise EmbeddingUnavailable(
                self._last_reason or "ollama embeddings unavailable (cooldown active)"
            )
        payload = {"model": self._model, "input": list(texts)}
        try:
            response = self._client.post(f"{self._base_url}/api/embed", json=payload)
            if response.status_code == 404:
                reason = f"ollama model '{self._model}' not found (HTTP 404)"
                self._unavailable_until = self._clock() + MODEL_NOT_FOUND_COOLDOWN_SECONDS
                self._last_reason = reason
                raise EmbeddingUnavailable(reason)
            if response.status_code >= 500:
                reason = f"ollama embeddings server error (HTTP {response.status_code})"
                self._unavailable_until = self._clock() + TRANSIENT_FAILURE_COOLDOWN_SECONDS
                self._last_reason = reason
                raise EmbeddingUnavailable(reason)
            response.raise_for_status()
            body = response.json()
        except EmbeddingUnavailable:
            raise
        except httpx.HTTPStatusError as error:
            raise EmbeddingUnavailable(f"ollama embeddings unavailable: {error}") from error
        except httpx.HTTPError as error:
            reason = f"ollama embeddings unavailable: {error}"
            self._unavailable_until = self._clock() + TRANSIENT_FAILURE_COOLDOWN_SECONDS
            self._last_reason = reason
            raise EmbeddingUnavailable(reason) from error
        except ValueError as error:
            raise EmbeddingUnavailable(f"ollama embeddings unavailable: {error}") from error

        vectors = body.get("embeddings") if isinstance(body, dict) else None
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingUnavailable("ollama returned an unusable embeddings payload")
        result = [[float(value) for value in vector] for vector in vectors]
        self._unavailable_until = 0.0
        self._last_reason = ""
        if result and result[0]:
            self._dim = len(result[0])
        return result


__all__ = ["DEFAULT_MODEL", "EMBED_TIMEOUT", "OllamaEmbedder"]

"""Composition boundaries for retrieval.

``EmbeddingPort`` deliberately knows nothing about code or files: it maps text
to vectors. Memory consolidation and document retrieval can adopt it later
without touching this module.
"""
from __future__ import annotations

from typing import Protocol, Sequence

from .models import Chunk, EmbedderIdentity


class EmbeddingUnavailable(RuntimeError):
    """No usable embedder right now. The caller degrades to lexical search."""


class EmbeddingPort(Protocol):
    @property
    def identity(self) -> EmbedderIdentity: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class Chunker(Protocol):
    def split(self, path: str, text: str) -> list[Chunk]: ...


__all__ = ["Chunker", "EmbeddingPort", "EmbeddingUnavailable"]

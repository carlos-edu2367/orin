"""The embedder that admits it cannot embed.

Having a real object here rather than ``None`` means the service has exactly one
degradation path: catch ``EmbeddingUnavailable``. "No embedder configured" and
"Ollama is down" then behave identically, and only one branch needs testing.
"""
from __future__ import annotations

from typing import Sequence

from ..models import EmbedderIdentity
from ..ports import EmbeddingUnavailable


class LexicalOnlyEmbedder:
    @property
    def identity(self) -> EmbedderIdentity:
        return EmbedderIdentity(embedder_id="lexical", model="none", dim=0)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingUnavailable("no embedder is configured for this installation")


__all__ = ["LexicalOnlyEmbedder"]

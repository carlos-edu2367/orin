from __future__ import annotations

import pytest

from agentos.retrieval.embeddings.lexical import LexicalOnlyEmbedder
from agentos.retrieval.ports import EmbeddingUnavailable


def test_the_lexical_embedder_identifies_itself_with_no_dimensions() -> None:
    identity = LexicalOnlyEmbedder().identity

    assert identity.embedder_id == "lexical"
    assert identity.dim == 0


def test_embedding_always_refuses_so_the_caller_degrades() -> None:
    with pytest.raises(EmbeddingUnavailable):
        LexicalOnlyEmbedder().embed(["anything"])

from __future__ import annotations

import httpx
import pytest

from agentos.retrieval.embeddings.ollama import OllamaEmbedder
from agentos.retrieval.ports import EmbeddingUnavailable


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_it_posts_a_batch_and_returns_one_vector_per_text() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"embeddings": [[1.0, 0.0], [0.0, 1.0]]})

    embedder = OllamaEmbedder(base_url="http://127.0.0.1:11434", model="nomic-embed-text", client=_client(handler))

    assert embedder.embed(["alpha", "beta"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert seen["url"] == "http://127.0.0.1:11434/api/embed"
    assert "nomic-embed-text" in str(seen["body"])


def test_the_dimension_is_learned_from_the_first_response() -> None:
    embedder = OllamaEmbedder(
        base_url="http://127.0.0.1:11434", model="nomic-embed-text",
        client=_client(lambda request: httpx.Response(200, json={"embeddings": [[1.0, 0.0, 0.0]]})),
    )

    assert embedder.identity.dim == 0
    embedder.embed(["alpha"])
    assert embedder.identity.dim == 3


def test_a_transport_failure_becomes_embedding_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    embedder = OllamaEmbedder(base_url="http://127.0.0.1:11434", model="nomic-embed-text", client=_client(handler))

    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["alpha"])


def test_a_missing_model_becomes_embedding_unavailable() -> None:
    embedder = OllamaEmbedder(
        base_url="http://127.0.0.1:11434", model="absent",
        client=_client(lambda request: httpx.Response(404, json={"error": "model not found"})),
    )

    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["alpha"])


def test_a_response_with_the_wrong_count_becomes_embedding_unavailable() -> None:
    embedder = OllamaEmbedder(
        base_url="http://127.0.0.1:11434", model="nomic-embed-text",
        client=_client(lambda request: httpx.Response(200, json={"embeddings": [[1.0, 0.0]]})),
    )

    with pytest.raises(EmbeddingUnavailable):
        embedder.embed(["alpha", "beta"])

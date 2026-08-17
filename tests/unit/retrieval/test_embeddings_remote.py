from __future__ import annotations

import httpx
import pytest

from agentos.retrieval.embeddings.remote import RemoteEmbedder
from agentos.retrieval.ports import EmbeddingUnavailable


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_it_sends_the_key_in_a_header_and_never_in_the_url() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": [{"embedding": [1.0, 0.0]}]})

    embedder = RemoteEmbedder(base_url="https://api.openai.com/v1", model="text-embedding-3-small", api_key="secret-key", client=_client(handler))
    embedder.embed(["alpha"])

    assert seen["authorization"] == "Bearer secret-key"
    assert "secret-key" not in str(seen["url"])


def test_embeddings_are_returned_in_request_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [
            {"index": 1, "embedding": [0.0, 1.0]},
            {"index": 0, "embedding": [1.0, 0.0]},
        ]})

    embedder = RemoteEmbedder(base_url="https://api.openai.com/v1", model="text-embedding-3-small", api_key="k", client=_client(handler))

    assert embedder.embed(["alpha", "beta"]) == [[1.0, 0.0], [0.0, 1.0]]


def test_an_http_error_becomes_embedding_unavailable_without_leaking_the_key() -> None:
    embedder = RemoteEmbedder(
        base_url="https://api.openai.com/v1", model="text-embedding-3-small", api_key="secret-key",
        client=_client(lambda request: httpx.Response(401, json={"error": "bad key"})),
    )

    with pytest.raises(EmbeddingUnavailable) as raised:
        embedder.embed(["alpha"])

    assert "secret-key" not in str(raised.value)

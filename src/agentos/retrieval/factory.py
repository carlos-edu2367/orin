"""The one place that decides how retrieval is wired for this installation.

Configuration is read from the environment, in the same style as
``search_client_from_environment``. Anything unrecognised or incomplete resolves
to the lexical embedder rather than to an error: retrieval degrades, it does not
break a turn.
"""
from __future__ import annotations

import os
from pathlib import Path
import re

from agentos.installation.paths import orin_paths
from agentos.provider_catalog.ollama import DEFAULT_OLLAMA_BASE_URL, normalize_ollama_base_url

from .chunking import HeuristicChunker
from .embeddings.lexical import LexicalOnlyEmbedder
from .embeddings.ollama import DEFAULT_MODEL as OLLAMA_MODEL, OllamaEmbedder
from .embeddings.remote import DEFAULT_BASE_URL as REMOTE_BASE_URL, DEFAULT_MODEL as REMOTE_MODEL, RemoteEmbedder
from .indexer import ProjectIndexer
from .ports import EmbeddingPort
from .service import RetrievalService
from .store import SqliteChunkStore


EMBEDDER_VARIABLE = "ORIN_RETRIEVAL_EMBEDDER"
MODEL_VARIABLE = "ORIN_RETRIEVAL_MODEL"
BASE_URL_VARIABLE = "ORIN_RETRIEVAL_BASE_URL"
API_KEY_VARIABLE = "ORIN_RETRIEVAL_API_KEY"

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def build_embedder() -> EmbeddingPort:
    choice = os.getenv(EMBEDDER_VARIABLE, "ollama").strip().lower() or "ollama"
    if choice == "ollama":
        # Reuses provider_catalog's own Ollama default and normalization
        # rather than defining a second, divergent one: a pasted base URL
        # with a trailing /v1 or /api (what users copy out of the Ollama
        # docs, or out of configuring the chat provider) would otherwise
        # silently break every embedding request.
        try:
            base_url = normalize_ollama_base_url(os.getenv(BASE_URL_VARIABLE, "").strip() or DEFAULT_OLLAMA_BASE_URL)
        except ValueError:
            return LexicalOnlyEmbedder()
        return OllamaEmbedder(
            base_url=base_url,
            model=os.getenv(MODEL_VARIABLE, "").strip() or OLLAMA_MODEL,
        )
    if choice == "remote":
        key = os.getenv(API_KEY_VARIABLE, "").strip()
        if not key:
            return LexicalOnlyEmbedder()
        return RemoteEmbedder(
            base_url=os.getenv(BASE_URL_VARIABLE, "").strip() or REMOTE_BASE_URL,
            model=os.getenv(MODEL_VARIABLE, "").strip() or REMOTE_MODEL,
            api_key=key,
        )
    return LexicalOnlyEmbedder()


def index_path_for(workspace_id: str, *, data_root: Path | None = None) -> Path:
    """One index file per project, named safely for every supported filesystem."""
    root = Path(data_root) if data_root is not None else orin_paths().data
    return root / "retrieval" / f"{_UNSAFE.sub('_', workspace_id)}.db"


def retrieval_service_for(*, workspace_id: str, local_root: str | None, data_root: Path | None = None) -> RetrievalService | None:
    """Build the service, or return None when this conversation has no project folder.

    A managed workspace holds the artefacts of one conversation, not a codebase.
    Indexing it would cost work and return noise, so retrieval is offered only
    for a bound local folder.
    """
    if not isinstance(local_root, str) or not local_root.strip():
        return None
    root = Path(local_root.strip())
    if not root.is_dir():
        return None
    embedder = build_embedder()
    store = SqliteChunkStore(index_path_for(workspace_id, data_root=data_root), embedder.identity)
    indexer = ProjectIndexer(root=root, store=store, chunker=HeuristicChunker(), embedder=embedder)
    return RetrievalService(store=store, indexer=indexer, embedder=embedder)


__all__ = [
    "API_KEY_VARIABLE", "BASE_URL_VARIABLE", "EMBEDDER_VARIABLE", "MODEL_VARIABLE",
    "build_embedder", "index_path_for", "retrieval_service_for",
]

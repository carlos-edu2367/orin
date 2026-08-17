from __future__ import annotations

from pathlib import Path

import pytest

from agentos.retrieval.embeddings.lexical import LexicalOnlyEmbedder
from agentos.retrieval.embeddings.ollama import OllamaEmbedder
from agentos.retrieval.embeddings.remote import RemoteEmbedder
from agentos.retrieval.factory import build_embedder, index_path_for, retrieval_service_for


def test_ollama_is_the_default_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORIN_RETRIEVAL_EMBEDDER", raising=False)

    assert isinstance(build_embedder(), OllamaEmbedder)


def test_the_remote_embedder_is_selected_explicitly_and_needs_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIN_RETRIEVAL_EMBEDDER", "remote")
    monkeypatch.setenv("ORIN_RETRIEVAL_API_KEY", "secret")

    assert isinstance(build_embedder(), RemoteEmbedder)

    monkeypatch.delenv("ORIN_RETRIEVAL_API_KEY")
    assert isinstance(build_embedder(), LexicalOnlyEmbedder)


def test_an_unknown_embedder_name_falls_back_to_lexical(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIN_RETRIEVAL_EMBEDDER", "nonsense")

    assert isinstance(build_embedder(), LexicalOnlyEmbedder)


def test_the_index_path_is_derived_from_the_workspace_id(tmp_path: Path) -> None:
    path = index_path_for("workspace:project_abc123", data_root=tmp_path)

    assert path.parent == tmp_path / "retrieval"
    assert path.suffix == ".db"
    assert ":" not in path.name


def test_a_service_is_only_built_for_an_existing_local_root(tmp_path: Path) -> None:
    assert retrieval_service_for(workspace_id="workspace:p", local_root=None, data_root=tmp_path) is None
    assert retrieval_service_for(workspace_id="workspace:p", local_root=str(tmp_path / "missing"), data_root=tmp_path) is None

    project = tmp_path / "project"
    project.mkdir()
    assert retrieval_service_for(workspace_id="workspace:p", local_root=str(project), data_root=tmp_path) is not None

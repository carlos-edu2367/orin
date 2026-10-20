"""W6 Task 8: `orin code` sessions (UUIDs for conversations) and model selection."""
from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path

import pytest

from agentos.cli_code.errors import CliRuntimeError
from agentos.cli_code.selection import ModelCatalog, Selection, choose_selection, resolve_model
from agentos.cli_code.sessions import SessionIndex, folder_key, is_uuid


def test_sessions_are_recorded_found_and_touched(tmp_path: Path) -> None:
    index = SessionIndex(tmp_path / "data")
    project = tmp_path / "project"
    project.mkdir()
    first = index.record(conversation_id="chat_1", folder=project, name="review")
    assert is_uuid(first.session_id) and first.folder == folder_key(project)
    chosen = str(uuid.uuid4())
    second = index.record(conversation_id="chat_2", folder=project, session_id=chosen.upper())
    assert second.session_id == chosen  # Claude Code ids are lower-case UUIDs
    assert index.find(chosen) == second and index.find("chat_1") == first and index.find("review") == first
    assert index.latest_for(project).conversation_id == "chat_2"
    touched = index.record(conversation_id="chat_1", folder=project, session_id=first.session_id)
    assert touched.name == "review" and touched.updated_at >= first.updated_at
    assert index.latest_for(project).conversation_id == "chat_1"
    assert index.find("nothing") is None and index.latest_for(tmp_path) is None


def test_a_session_id_cannot_move_to_another_conversation(tmp_path: Path) -> None:
    index = SessionIndex(tmp_path)
    taken = index.record(conversation_id="chat_1", folder=tmp_path)
    with pytest.raises(ValueError):
        index.record(conversation_id="chat_2", folder=tmp_path, session_id=taken.session_id)


def test_a_broken_index_reads_as_empty(tmp_path: Path) -> None:
    index = SessionIndex(tmp_path)
    index.path.write_text("{not json", encoding="utf-8")
    assert index.load() == []
    index.record(conversation_id="chat_1", folder=tmp_path)
    assert len(index.load()) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_index_is_owner_only(tmp_path: Path) -> None:
    index = SessionIndex(tmp_path)
    index.record(conversation_id="chat_1", folder=tmp_path)
    assert stat.S_IMODE(index.path.stat().st_mode) == 0o600


def test_is_uuid() -> None:
    assert is_uuid(str(uuid.uuid4()))
    assert is_uuid("0B3F6C2E-7D7B-4F39-9C9E-1F1F1F1F1F1F")
    assert not is_uuid("chat_1") and not is_uuid("") and not is_uuid("0b3f6c2e7d7b4f399c9e1f1f1f1f1f1f")


class _Api:
    def __init__(self, catalogs: dict[str, list[str]]) -> None:
        self.catalogs = catalogs
        self.calls: list[str] = []

    def get(self, path: str, **params: str) -> object:
        self.calls.append(path)
        provider = path.split("/")[3]
        if provider not in self.catalogs:
            raise CliRuntimeError("404")
        if path.endswith("/models"):
            return {"items": [{"model_id": item} for item in self.catalogs[provider]]}
        return {"provider": provider, "enabled": True}


CATALOGS = {"ollama": ["qwen3:8b", "llama3"], "openrouter": ["anthropic/claude-sonnet-4.5", "llama3"]}


@pytest.mark.parametrize(("value", "expected"), [
    ("ollama/qwen3:8b", Selection("ollama", "qwen3:8b")),
    ("ollama:qwen3:8b", Selection("ollama", "qwen3:8b")),
    ("qwen3:8b", Selection("ollama", "qwen3:8b")),
    ("anthropic/claude-sonnet-4.5", Selection("openrouter", "anthropic/claude-sonnet-4.5")),
    ("openrouter/anthropic/claude-sonnet-4.5", Selection("openrouter", "anthropic/claude-sonnet-4.5")),
])
def test_models_resolve_exactly(value: str, expected: Selection) -> None:
    assert resolve_model(ModelCatalog(_Api(CATALOGS)), value) == expected


def test_ambiguous_unknown_and_disabled_models_are_errors() -> None:
    catalog = ModelCatalog(_Api(CATALOGS))
    with pytest.raises(CliRuntimeError, match="choose one with --provider"):
        resolve_model(catalog, "llama3")
    assert resolve_model(catalog, "llama3", "ollama") == Selection("ollama", "llama3")
    with pytest.raises(CliRuntimeError, match="enabled providers: openrouter, ollama"):
        resolve_model(catalog, "gpt-9")
    with pytest.raises(CliRuntimeError, match="not set up"):
        resolve_model(catalog, "gpt-4o", "openai")
    with pytest.raises(CliRuntimeError, match="unknown provider"):
        resolve_model(catalog, "x", "nowhere")


def test_the_catalog_is_asked_once_per_provider() -> None:
    api = _Api(CATALOGS)
    catalog = ModelCatalog(api)
    resolve_model(catalog, "qwen3:8b")
    resolve_model(catalog, "qwen3:8b")
    assert api.calls.count("/v1/providers/ollama/models") == 1


def test_choosing_without_a_model_flag() -> None:
    catalog = ModelCatalog(_Api(CATALOGS))
    remembered = Selection("ollama", "llama3")
    assert choose_selection(catalog, model=None, provider=None, remembered=remembered, configured="qwen3:8b") == remembered
    assert choose_selection(catalog, model=None, provider=None, remembered=None, configured="qwen3:8b") == Selection("ollama", "qwen3:8b")
    assert choose_selection(catalog, model="ollama/llama3", provider=None, remembered=None, configured=None) == Selection("ollama", "llama3")
    with pytest.raises(CliRuntimeError, match="choose a model"):
        choose_selection(catalog, model=None, provider=None, remembered=None, configured=None)

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy import create_engine

from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.session import TurnSession
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import metadata
from agentos.settings.paths import orin_user_dir


class Store:
    def __init__(self) -> None:
        self.turn = {
            "turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e", "provider": "openrouter",
            "model_id": "m", "user_message_id": "um", "assistant_message_id": "am",
        }
        self.snapshots: dict[str, dict] = {}

    def load(self, turn_id):
        return self.turn

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "go"}]

    def lifecycle(self, turn, state, **payload):
        pass

    def delta(self, turn, text):
        pass

    def finish(self, turn, *, failed=False, code=None):
        pass

    def record(self, *args, **kwargs):
        pass

    def main_agent_id(self, turn):
        return "agent:c:main"

    def prompt_snapshot(self, conversation_id):
        return self.snapshots.get(conversation_id)

    def save_prompt_snapshot(self, conversation_id, *, user_id, snapshot_key, content):
        self.snapshots[conversation_id] = {
            "snapshot_key": snapshot_key, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(), "content": content,
        }


class Agents:
    def list(self):
        return []


def _runtime(tmp_path: Path, store: Store, **turn) -> AgenticTurnRuntime:
    session = TurnSession(
        turn={**store.turn, "code_mode": "code", **turn}, store=store, agents_store=Agents(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False,
    )
    return session.build_runtime()


def _write_user_settings(data: dict) -> None:
    path = orin_user_dir() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_the_first_turn_stores_the_prompt_and_later_turns_reuse_it(tmp_path: Path) -> None:
    store = Store()
    first = _runtime(tmp_path, store)
    assert store.snapshots["c"]["content"] == first.system_prompt
    stored = dict(store.snapshots["c"])
    stored["content"] = "STORED PROMPT"
    stored["sha256"] = hashlib.sha256(b"STORED PROMPT").hexdigest()
    store.snapshots["c"] = stored
    assert _runtime(tmp_path, store).system_prompt == "STORED PROMPT"


def test_a_corrupted_snapshot_is_rendered_again(tmp_path: Path) -> None:
    store = Store()
    rendered = _runtime(tmp_path, store).system_prompt
    store.snapshots["c"]["content"] = "TAMPERED"
    assert _runtime(tmp_path, store).system_prompt == rendered
    assert store.snapshots["c"]["content"] == rendered


def test_a_changed_key_is_rendered_again(tmp_path: Path) -> None:
    store = Store()
    _runtime(tmp_path, store)
    store.snapshots["c"]["snapshot_key"] = "old-version"
    store.snapshots["c"]["content"] = "OLD"
    store.snapshots["c"]["sha256"] = hashlib.sha256(b"OLD").hexdigest()
    assert _runtime(tmp_path, store).system_prompt != "OLD"


def test_the_snapshot_can_be_turned_off(tmp_path: Path) -> None:
    _write_user_settings({"systemPromptSnapshot": "off"})
    store = Store()
    _runtime(tmp_path, store)
    assert store.snapshots == {}


def test_chat_mode_has_no_snapshot(tmp_path: Path) -> None:
    store = Store()
    TurnSession(
        turn=dict(store.turn), store=store, agents_store=Agents(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False,
    ).build_runtime()
    assert store.snapshots == {}


def test_excluded_dynamic_sections_travel_with_the_first_user_message(tmp_path: Path) -> None:
    _write_user_settings({"excludeDynamicSystemPromptSections": True})
    runtime = _runtime(tmp_path, Store())
    assert "# Session" not in runtime.volatile_prompt
    assert "# Session" in runtime.first_user_context
    window = runtime._request_messages([{"role": "user", "content": "go"}])
    conversation = [item for item in window if item.get("role") != "system"]
    assert "<system-reminder>" in str(conversation[0]["content"]) and "# Session" in str(conversation[0]["content"])
    assert conversation[1]["content"] == "go"


def test_store_round_trip() -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    assert store.prompt_snapshot("c") is None
    store.save_prompt_snapshot("c", user_id="u", snapshot_key="k1", content="one")
    store.save_prompt_snapshot("c", user_id="u", snapshot_key="k2", content="two")
    saved = store.prompt_snapshot("c")
    assert saved == {"snapshot_key": "k2", "sha256": hashlib.sha256(b"two").hexdigest(), "content": "two"}
    store.clear_prompt_snapshot("c")
    assert store.prompt_snapshot("c") is None


def test_git_status_appears_only_for_a_trusted_local_workspace(tmp_path: Path, monkeypatch) -> None:
    import agentos.code_mode.git_context as git_context
    from agentos.permissions.trust import WorkspaceTrustStore

    calls: list[str] = []
    monkeypatch.setattr(git_context, "git_status_summary", lambda root, **kw: calls.append(str(root)) or "## main")
    folder = tmp_path / "project"
    folder.mkdir()
    store = Store()
    untrusted = _runtime(tmp_path, store, workspace_root_path=str(folder), conversation_id="c1")
    assert "# Git status" not in untrusted.volatile_prompt and calls == []
    WorkspaceTrustStore().trust("u", folder)
    trusted = _runtime(tmp_path, store, workspace_root_path=str(folder), conversation_id="c2")
    assert "## main" in trusted.volatile_prompt and len(calls) == 1

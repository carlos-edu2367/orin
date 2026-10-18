from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.agentic.reminders import ReminderQueue
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.memory_files import MemoryFiles
from agentos.settings.paths import orin_managed_dir, orin_user_dir


class Store:
    def __init__(self) -> None:
        self.turn = {
            "turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e", "provider": "openrouter",
            "model_id": "m", "user_message_id": "um", "assistant_message_id": "am",
        }
        self.snapshots: dict[str, dict] = {}

    def load(self, turn_id): return self.turn
    def history_for_turn(self, turn): return [{"role": "user", "content": "go"}]
    def lifecycle(self, turn, state, **payload): pass
    def delta(self, turn, text): pass
    def finish(self, turn, *, failed=False, code=None): pass
    def record(self, *args, **kwargs): pass
    def main_agent_id(self, turn): return "agent:c:main"

    def prompt_snapshot(self, conversation_id):
        return self.snapshots.get(conversation_id)

    def save_prompt_snapshot(self, conversation_id, *, user_id, snapshot_key, content):
        self.snapshots[conversation_id] = {"snapshot_key": snapshot_key, "sha256": hashlib.sha256(content.encode()).hexdigest(), "content": content}


class Agents:
    def list(self): return []


def _session(tmp_path: Path, store: Store | None = None, bus: LifecycleBus | None = None, **turn) -> TurnSession:
    store = store or Store()
    return TurnSession(
        turn={**store.turn, **turn}, store=store, agents_store=Agents(), memory_store=None,
        provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False, lifecycle=bus,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_chat_mode_carries_every_layer_and_reports_each_file(tmp_path: Path) -> None:
    bus = LifecycleBus()
    events = []
    bus.subscribe(events.append)
    session = _session(tmp_path, bus=bus)
    root = session.workspace.root
    _write(root / "CLAUDE.md", "project rule")
    _write(root / ".claude" / "rules" / "a.md", "rule a")
    _write(root / "CLAUDE.local.md", "local rule")
    _write(orin_user_dir() / "ORIN.md", "user rule")
    runtime = session.build_runtime()
    for text in ("project rule", "rule a", "local rule", "user rule"):
        assert text in runtime.volatile_prompt
    assert "## Convenções do workspace" in runtime.volatile_prompt
    loaded = [dict(event.payload) for event in events if event.name is HookEvent.INSTRUCTIONS_LOADED]
    assert {(item["memory_type"], Path(item["file_path"]).name) for item in loaded} == {
        ("Project", "CLAUDE.md"), ("Project", "a.md"), ("Local", "CLAUDE.local.md"), ("User", "ORIN.md"),
    }
    assert all(item["load_reason"] == "session_start" for item in loaded)
    assert runtime.memory_prompt_tokens > 0


def test_code_mode_snapshots_memory_with_the_stable_prompt(tmp_path: Path) -> None:
    store = Store()
    session = _session(tmp_path, store=store, code_mode="code")
    _write(session.workspace.root / "CLAUDE.md", "first version")
    first = session.build_runtime()
    assert "first version" in first.system_prompt and "first version" not in first.volatile_prompt
    _write(session.workspace.root / "CLAUDE.md", "second version")
    again = _session(tmp_path, store=store, code_mode="code").build_runtime()
    assert "first version" in again.system_prompt  # applies after the next compaction, like Claude Code


def test_excludes_and_managed_text_come_from_settings(tmp_path: Path) -> None:
    _write(orin_user_dir() / "settings.json", json.dumps({"claudeMdExcludes": ["**/CLAUDE.local.md"]}))
    _write(orin_managed_dir() / "managed-settings.json", json.dumps({"claudeMd": "company policy"}))
    session = _session(tmp_path)
    _write(session.workspace.root / "CLAUDE.local.md", "should be excluded")
    runtime = session.build_runtime()
    assert "company policy" in runtime.volatile_prompt and "should be excluded" not in runtime.volatile_prompt


def test_reading_a_file_brings_in_its_directory_rules(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-memory")
    _write(workspace.root / "pkg" / "CLAUDE.md", "pkg rules")
    _write(workspace.root / "pkg" / "mod.py", "x = 1\n")
    bus = LifecycleBus()
    events = []
    bus.subscribe(events.append)
    queue = ReminderQueue()
    toolset = AgentToolset(workspace, lifecycle=bus, reminders=queue)
    toolset.memory_tracker = MemoryFiles(workspace.root, trusted=False)
    assert toolset.invoke("read_file", {"path": "pkg/mod.py"}).status == "succeeded"
    texts = [item.text for item in queue.drain()]
    assert len(texts) == 1 and "pkg rules" in texts[0]
    loaded = [dict(event.payload) for event in events if event.name is HookEvent.INSTRUCTIONS_LOADED]
    assert loaded and loaded[0]["load_reason"] == "nested_traversal" and loaded[0]["trigger_file_path"].endswith("mod.py")
    toolset.invoke("read_file", {"path": "pkg/mod.py", "offset": 1, "limit": 5})
    assert queue.drain() == []

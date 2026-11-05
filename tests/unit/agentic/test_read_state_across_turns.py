from __future__ import annotations

from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.read_state import (
    MAX_CARRIED_READS,
    ConversationReadStates,
    copy_read_state,
    prepare_carried_state,
)
from agentos.agentic.session import TurnSession
from agentos.agentic.tools._shared import ReadFileRecord
from agentos.agentic.workspace import ConversationWorkspace


def test_the_registry_keeps_one_state_per_conversation_and_evicts_the_oldest() -> None:
    states = ConversationReadStates(max_conversations=2)
    first = states.state_for("u", "c1")
    assert states.state_for("u", "c1") is first
    assert states.state_for("u", "c2") is not first
    states.state_for("u", "c3")
    assert states.state_for("u", "c1") is not first


def _code_toolset(workspace: ConversationWorkspace, state: dict) -> AgentToolset:
    return AgentToolset(workspace, code_mode_active=True, permission_mode="full_autonomy", read_file_state=state)


def test_a_carried_state_drops_changed_files_and_releases_stubs(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "same.txt").write_bytes(b"a\n")
    (workspace.root / "changed.txt").write_bytes(b"a\n")
    state: dict = {}
    turn_one = _code_toolset(workspace, state)
    turn_one.invoke("read_file", {"path": "same.txt"})
    turn_one.invoke("read_file", {"path": "changed.txt"})
    (workspace.root / "changed.txt").write_bytes(b"b\n")
    assert prepare_carried_state(state, workspace) == 1
    assert set(state) == {"same.txt"} and state["same.txt"].explicit_read is False


def test_the_next_turn_may_edit_a_file_read_in_the_previous_turn(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "a.txt").write_bytes(b"one\n")
    state: dict = {}
    _code_toolset(workspace, state).invoke("read_file", {"path": "a.txt"})
    prepare_carried_state(state, workspace)
    turn_two = _code_toolset(workspace, state)
    assert turn_two.invoke("read_file", {"path": "a.txt"}).data["tool_output"]["type"] == "text"
    assert turn_two.invoke("edit_file", {"path": "a.txt", "old_text": "one", "new_text": "two"}).status == "succeeded"


def _session(tmp_path: Path, **kwargs) -> TurnSession:
    class Store:
        def main_agent_id(self, turn): return "agent:c1:main"
        def history_for_turn(self, turn): return []
        def record(self, *args, **kw): return None

    class Agents:
        def list(self): return []

    return TurnSession(
        turn={"turn_id": "t1", "conversation_id": "c1", "user_id": "local-user", "execution_id": "e1",
              "code_mode": "code", "provider": "ollama", "model_id": "m"},
        store=Store(), agents_store=Agents(), memory_store=None, provider_factory=lambda: None,
        workspace_root=tmp_path, cancelled=lambda _turn: False, **kwargs,
    )


def test_turn_session_uses_the_injected_state(tmp_path: Path) -> None:
    state: dict = {}
    assert _session(tmp_path, read_file_state=state)._read_file_state is state


def test_a_subagent_gets_a_copy_of_the_parent_read_state(tmp_path: Path) -> None:
    session = _session(tmp_path)
    (session.workspace.root / "shared.py").write_bytes(b"x = 1\n")
    (session.workspace.root / "other.py").write_bytes(b"y = 2\n")
    session._toolset(subagents=True, share_read_state=True).invoke("read_file", {"path": "shared.py"})
    sub = session._toolset(subagents=False, inherit_context=True)
    assert sub._read_file_state is not session._read_file_state
    assert set(sub._read_file_state) == {"shared.py"}
    sub.invoke("read_file", {"path": "other.py"})
    assert "other.py" not in session._read_file_state


def test_carried_state_caps_at_max_carried_reads_and_drops_missing_or_oversized(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "kept.txt").write_bytes(b"ok\n")
    state: dict[str, object] = {}
    _code_toolset(workspace, state).invoke("read_file", {"path": "kept.txt"})
    # Add missing file and > 8 MiB file and excess older entries
    (workspace.root / "huge.bin").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    state["missing.txt"] = ReadFileRecord(mtime_ns=1, sha256="deadbeef", explicit_read=True, read_seq=999_999)
    state["huge.bin"] = ReadFileRecord(mtime_ns=1, sha256="deadbeef", explicit_read=True, read_seq=999_998)
    for idx in range(MAX_CARRIED_READS + 5):
        state[f"old-{idx}.txt"] = ReadFileRecord(mtime_ns=1, sha256="abc", explicit_read=True, read_seq=-1000 - idx)
    # Bump kept.txt read_seq so it stays within top MAX_CARRIED_READS
    state["kept.txt"].read_seq = 1_000_000  # type: ignore[attr-defined]
    removed = prepare_carried_state(state, workspace)
    assert removed == (MAX_CARRIED_READS + 5) + 2
    assert set(state) == {"kept.txt"}
    assert state["kept.txt"].explicit_read is False  # type: ignore[attr-defined]


def test_copy_read_state_clones_records_independently() -> None:
    original = {"a.py": ReadFileRecord(mtime_ns=10, sha256="h1", explicit_read=True, read_seq=1)}
    cloned = copy_read_state(original)
    assert cloned is not original
    assert cloned["a.py"] is not original["a.py"]
    cloned["a.py"].explicit_read = False  # type: ignore[attr-defined]
    assert original["a.py"].explicit_read is True

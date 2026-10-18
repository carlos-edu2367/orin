from __future__ import annotations

import os
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.lifecycle import HookEvent, LifecycleBus
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace


def _toolset(tmp_path: Path) -> tuple[AgentToolset, Path]:
    workspace = ConversationWorkspace(tmp_path, "conv-changed")
    (workspace.root / "a.txt").write_text("one\n", encoding="utf-8")
    (workspace.root / "b.txt").write_text("two\n", encoding="utf-8")
    toolset = AgentToolset(workspace, code_mode_active=True, permission_mode="full_autonomy")
    assert toolset.invoke("read_file", {"path": "a.txt"}).status == "succeeded"
    assert toolset.invoke("read_file", {"path": "b.txt"}).status == "succeeded"
    return toolset, workspace.root


def _bump(path: Path) -> None:
    stamp = path.stat().st_mtime + 10
    os.utime(path, (stamp, stamp))


def test_an_outside_change_is_reported_once(tmp_path: Path) -> None:
    toolset, root = _toolset(tmp_path)
    assert toolset.detect_external_changes() == []
    (root / "a.txt").write_text("changed outside\n", encoding="utf-8")
    _bump(root / "a.txt")
    changes = toolset.detect_external_changes()
    assert [(item[0], item[2]) for item in changes] == [("a.txt", "change")] and Path(changes[0][1]).is_absolute()
    assert toolset.detect_external_changes() == []


def test_an_unchanged_stamp_is_not_hashed_again(tmp_path: Path, monkeypatch) -> None:
    """W3-R19: a changed file is hashed once; later requests that see the same stamp read nothing."""
    toolset, root = _toolset(tmp_path)
    reads: list[int] = []
    original = Path.read_bytes

    def counting(self):
        data = original(self)
        reads.append(len(data))
        return data

    monkeypatch.setattr(Path, "read_bytes", counting)
    (root / "a.txt").write_text("changed outside\n", encoding="utf-8")
    _bump(root / "a.txt")
    assert [(item[0], item[2]) for item in toolset.detect_external_changes()] == [("a.txt", "change")]
    assert toolset.detect_external_changes() == [] and toolset.detect_external_changes() == []
    assert len(reads) == 1


def test_a_growing_large_log_is_not_read_in_full_on_every_request(tmp_path: Path, monkeypatch) -> None:
    """W3-R19: a log over 8 MiB the model read once is reported when it moves, and never read in full again."""
    workspace = ConversationWorkspace(tmp_path, "conv-large")
    log = workspace.root / "server.log"
    log.write_bytes(b"line\n" * 2_500_000)  # 10 MB
    toolset = AgentToolset(workspace, code_mode_active=True, permission_mode="full_autonomy")
    assert toolset.invoke("read_file", {"path": "server.log", "offset": 1, "limit": 20}).status == "succeeded"
    reads: list[int] = []
    original = Path.read_bytes

    def counting(self):
        data = original(self)
        reads.append(len(data))
        return data

    monkeypatch.setattr(Path, "read_bytes", counting)
    reported: list[tuple[str, str]] = []
    for _ in range(3):
        with log.open("ab") as handle:
            handle.write(b"another line\n")
        _bump(log)
        reported += [(item[0], item[2]) for item in toolset.detect_external_changes()]
    assert reported == [("server.log", "change")]
    assert len(reads) <= 1


def test_a_deleted_file_is_reported(tmp_path: Path) -> None:
    toolset, root = _toolset(tmp_path)
    (root / "b.txt").unlink()
    assert [(item[0], item[2]) for item in toolset.detect_external_changes()] == [("b.txt", "unlink")]


def test_a_touch_or_an_own_write_is_not_a_change(tmp_path: Path) -> None:
    toolset, root = _toolset(tmp_path)
    _bump(root / "a.txt")
    assert toolset.detect_external_changes() == []
    assert toolset.invoke("write_file", {"path": "b.txt", "content": "mine\n"}).status == "succeeded"
    assert toolset.detect_external_changes() == []


class Store:
    turn = {"turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e", "provider": "openrouter",
            "model_id": "m", "user_message_id": "um", "assistant_message_id": "am"}

    def load(self, turn_id): return self.turn
    def history_for_turn(self, turn): return [{"role": "user", "content": "go"}]
    def lifecycle(self, turn, state, **payload): pass
    def delta(self, turn, text): pass
    def finish(self, turn, *, failed=False, code=None): pass


class FakeToolset:
    def __init__(self) -> None:
        self.reported = False

    def schemas(self, allowed=None): return []
    def is_read_only(self, name): return True
    def argument_names(self, name): return frozenset({"path"})
    def invoke(self, name, arguments): return ToolOutcome("succeeded", "ok", "content")

    def detect_external_changes(self):
        if self.reported:
            return []
        self.reported = True
        return [("src/a.py", "C:/w/src/a.py", "change")]


class ToolThenText:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def stream(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            return iter([
                NormalizedStreamItem(StreamKind.TOOL_CALL, 1, tool_call_id="c1", tool_name="read_file", arguments_delta='{"path": "x"}'),
                NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
            ])
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text="done"), NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop")])


def test_the_runtime_emits_file_changed_and_tells_the_model() -> None:
    bus = LifecycleBus()
    events = []
    bus.subscribe(events.append)
    provider = ToolThenText()
    AgenticTurnRuntime(store=Store(), provider=provider, toolset=FakeToolset(), lifecycle=bus,
                       limits=AgenticLimits(max_iterations=None, max_actions=None)).run("t")
    changed = [dict(event.payload) for event in events if event.name is HookEvent.FILE_CHANGED]
    assert changed == [{**changed[0], "file_path": "C:/w/src/a.py", "event": "change"}]
    assert any("src/a.py changed on disk" in str(m.get("content")) for m in provider.requests[0]["messages"])

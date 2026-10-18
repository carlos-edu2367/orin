from __future__ import annotations

import os
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.compaction import COMPACTION_SECTIONS
from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace


def test_reattachment_follows_read_order_not_file_times(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-reattach")
    toolset = AgentToolset(workspace, code_mode_active=True)
    names = ["a", "b", "c", "d", "e", "f"]
    for name in names:
        (workspace.root / f"{name}.py").write_text(f"{name.upper()} = 1\n", encoding="utf-8")
    for name in names:
        assert toolset.invoke("read_file", {"path": f"{name}.py"}).status == "succeeded"
    newest = (workspace.root / "a.py").stat().st_mtime + 100
    os.utime(workspace.root / "a.py", (newest, newest))  # touched last, read first
    block = toolset.post_compact_enrichment()
    for name in ("b", "c", "d", "e", "f"):
        assert f"`{name}.py`" in block
    assert "`a.py`" not in block


class Store:
    def __init__(self) -> None:
        self.cleared: list[str] = []

    def clear_prompt_snapshot(self, conversation_id):
        self.cleared.append(conversation_id)


class Summarizer:
    def stream(self, request):
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text="\n".join(f"### {s}\n- d" for s in COMPACTION_SECTIONS))])


def test_a_compaction_restarts_the_snapshot_and_the_session() -> None:
    bus = LifecycleBus()
    events = []
    bus.subscribe(events.append)
    bus.subscribe(lambda event: LifecycleDecision(additional_context=["re-check the build"]) if event.name is HookEvent.SESSION_START else None)
    store = Store()
    runtime = AgenticTurnRuntime(
        store=store, provider=Summarizer(), lifecycle=bus, system_prompt="OLD",
        limits=AgenticLimits(max_context_tokens=1_000), prompt_renderer=lambda: "NEW PROMPT",
    )
    messages = [{"role": "user", "content": f"step {index} " + "x" * 600} for index in range(10)]
    messages.append({"role": "user", "content": "now"})
    runtime._pinned_index = len(messages) - 1
    assert runtime._run_compaction(messages, {"turn_id": "t", "conversation_id": "c"}) is True
    assert store.cleared == ["c"] and runtime.system_prompt == "NEW PROMPT"
    starts = [dict(event.payload) for event in events if event.name is HookEvent.SESSION_START]
    assert [item["source"] for item in starts] == ["compact"]
    assert [item.text for item in runtime.reminders.drain()] == ["re-check the build"]

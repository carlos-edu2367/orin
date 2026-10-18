from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.runtime import (
    CODE_COMPACTION_SECTIONS,
    AgenticLimits,
    AgenticTurnRuntime,
    strip_analysis_block,
)
from agentos.agentic.session import TurnSession
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode import ToolPermissionBroker


class _FakeConversationStore:
    def __init__(self, history: list[dict[str, Any]] | None = None) -> None:
        self.events: list[tuple[AgentActivityEventType, str, dict[str, Any]]] = []
        self._history = list(history or [{"role": "user", "content": "Investigate the auth module"}])
        self._agents: dict[tuple[str, str], dict[str, Any]] = {}

    def record(
        self,
        turn: Mapping[str, Any],
        event_type: AgentActivityEventType,
        summary: str,
        payload: dict[str, Any] | None = None,
        *,
        agent_id: str | None = None,
        parent_agent_id: str | None = None,
    ) -> None:
        self.events.append((event_type, summary, dict(payload or {})))

    def emit_activity(
        self,
        conversation_id: str,
        turn_id: str | None,
        *,
        agent_id: str,
        parent_agent_id: str | None = None,
        event_type: AgentActivityEventType,
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.events.append((event_type, summary, dict(payload or {})))

    def history_for_turn(self, turn: Mapping[str, Any]) -> list[dict[str, Any]]:
        return list(self._history)

    def create_agent(self, conversation_id: str, **kwargs: Any) -> dict[str, Any]:
        name = str(kwargs["name"])
        record = {
            "agent_id": f"agent:{name}",
            "conversation_id": conversation_id,
            "name": name,
            "role": str(kwargs.get("role") or name),
            "instructions": str(kwargs.get("instructions") or ""),
            "model_id": str(kwargs.get("model_id") or "claude-sonnet-4-6"),
            "provider": str(kwargs.get("provider") or "anthropic"),
            "parent_agent_id": kwargs.get("parent_agent_id"),
            "state": "idle",
        }
        self._agents[(conversation_id, name)] = record
        return record

    def get_agent_by_name(self, conversation_id: str, name: str) -> dict[str, Any] | None:
        return self._agents.get((conversation_id, name))

    def list_agents(self, conversation_id: str) -> list[dict[str, Any]]:
        return [rec for (cid, _), rec in self._agents.items() if cid == conversation_id]

    def update_agent_state(self, conversation_id: str, agent_id: str, state: str) -> None:
        for rec in self._agents.values():
            if rec["agent_id"] == agent_id:
                rec["state"] = state

    def record_agent_usage(self, conversation_id: str, agent_id: str, **kwargs: Any) -> None:
        pass


class _SubagentInspectingProvider:
    """Captures every subagent provider request (messages and published tool names)."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def stream(self, request: Mapping[str, Any]):
        req_copy = {
            "turn_id": request.get("turn_id"),
            "messages": list(request.get("messages") or []),
            "tools": [
                str((t.get("function") or {}).get("name") or "")
                for t in (request.get("tools") or [])
                if isinstance(t, Mapping)
            ],
        }
        self.requests.append(req_copy)
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text="Subagent completed task concisely.")])


def test_run_command_emits_live_tool_progressed_and_persists_cwd(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-stream")
    subdir = workspace.root / "packages" / "core"
    subdir.mkdir(parents=True, exist_ok=True)
    (subdir / "marker.txt").write_text("found-in-subdir", encoding="utf-8")

    progressed_events: list[tuple[str, dict[str, Any]]] = []
    toolset = AgentToolset(
        workspace,
        on_tool_progress=lambda tool_name, payload: progressed_events.append((tool_name, dict(payload))),
    )

    py_cmd = f'"{sys.executable}" -c "import time; print(\'chunk-1\', flush=True); time.sleep(0.3); print(\'chunk-2\', flush=True)"'
    outcome = toolset.invoke("run_command", {"command": py_cmd}, call_id="call-live-1")
    assert outcome.status == "succeeded"
    assert "chunk-1" in outcome.content
    assert "chunk-2" in outcome.content
    assert len(progressed_events) >= 1
    tool_name, last_payload = progressed_events[-1]
    assert tool_name == "run_command"
    assert last_payload["invocation_id"] == "call-live-1"
    assert "chunk-1" in last_payload["output_tail"]

    # Persistent cwd across run_command invocations in the same session
    cd_outcome = toolset.invoke("run_command", {"command": "cd packages/core"}, call_id="call-cd")
    assert cd_outcome.status == "succeeded"
    assert toolset._current_cwd == subdir.resolve()

    verify_cwd = toolset.invoke(
        "run_command",
        {"command": f'"{sys.executable}" -c "import pathlib; print(pathlib.Path(\'marker.txt\').read_text())"'},
        call_id="call-check-cwd",
    )
    assert verify_cwd.status == "succeeded"
    assert "found-in-subdir" in verify_cwd.content


def test_run_command_notifies_when_tracked_background_process_exits(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-bg-exit")
    logs_dir = workspace.root / ".orin" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    # Write a fake exited PID entry into .orin/logs/processes.json
    dead_pid = 999_991
    registry = [
        {
            "pid": dead_pid,
            "command": "npm run dev",
            "log_path": ".orin/logs/bg_dead.log",
        }
    ]
    (logs_dir / "processes.json").write_text(json.dumps(registry), encoding="utf-8")

    toolset = AgentToolset(workspace)
    outcome = toolset.invoke(
        "run_command",
        {"command": f'"{sys.executable}" -c "print(\'hello\')"'},
        call_id="call-fg",
    )
    assert outcome.status == "succeeded"
    assert "<system-reminder>" in outcome.content
    assert str(dead_pid) in outcome.content
    assert "npm run dev" in outcome.content


def test_builtin_subagents_explore_plan_general_and_fork(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-subagents")
    parent_history = [
        {"role": "user", "content": "We are refactoring src/auth.py to use JWTs."},
        {"role": "assistant", "content": "I have inspected src/auth.py and found legacy session tokens."},
        {"role": "user", "content": "Now run subagents to explore and fork."},
    ]
    store = _FakeConversationStore(history=parent_history)
    provider = _SubagentInspectingProvider()
    session = TurnSession(
        store=store,
        agents_store=None,
        memory_store=None,
        provider_factory=lambda: provider,
        workspace_root=tmp_path,
        turn={
            "turn_id": "turn-sub",
            "conversation_id": "conv-subagents",
            "provider": "anthropic",
            "model_id": "claude-sonnet-4-6",
            "code_mode": "code",
            "assistant_message_id": "msg-sub",
        },
    )

    # 1. `explore` is strictly read-only: no write/edit/run_command tools published
    explore_res = session._run_subagent(
        "explore",
        "Find all usages of legacy session tokens",
        description="Search session tokens",
    )
    assert explore_res.status == "succeeded"
    assert "Subagent completed task concisely." in explore_res.content
    explore_req = provider.requests[-1]
    explore_tools = set(explore_req["tools"])
    assert "Read" in explore_tools
    assert "Grep" in explore_tools
    assert "Write" not in explore_tools
    assert "Edit" not in explore_tools
    assert "Bash" not in explore_tools

    # Also verify read_only_mode blocks direct invocation of mutating tools on AgentToolset
    ro_toolset = AgentToolset(workspace, read_only_mode=True)
    blocked = ro_toolset.invoke("write_file", {"path": "x.txt", "content": "nope"})
    assert blocked.status == "failed"
    assert blocked.error_code == "READ_ONLY_SUBAGENT"

    # 2. `plan` is also strictly read-only
    plan_res = session._run_subagent("plan", "Design the JWT migration plan", description="Architecture plan")
    assert plan_res.status == "succeeded"
    plan_req = provider.requests[-1]
    plan_tools = set(plan_req["tools"])
    assert "Write" not in plan_tools
    assert "Edit" not in plan_tools
    assert "Bash" not in plan_tools

    # 3. `general` has full subagent toolset (including write_file / run_command)
    general_res = session._run_subagent("general", "Implement helper module", description="General worker")
    assert general_res.status == "succeeded"
    general_req = provider.requests[-1]
    general_tools = set(general_req["tools"])
    assert "Write" in general_tools
    assert "Bash" in general_tools

    # 4. `fork` inherits the parent's conversation history so prompt cache prefix is preserved
    fork_res = session._run_subagent("fork", "Run heavy verification on the auth refactor", description="Forked verifier")
    assert fork_res.status == "succeeded"
    fork_req = provider.requests[-1]
    fork_user_contents = [
        str(msg.get("content") or "")
        for msg in fork_req["messages"]
        if isinstance(msg, Mapping) and msg.get("role") == "user"
    ]
    assert any("We are refactoring src/auth.py to use JWTs." in text for text in fork_user_contents)
    assert any("<fork-directive>" in text for text in fork_user_contents)

    # 5. PLAN_MODE permissions allow `explore` and `plan` subagents without user prompt, but deny `general`/`fork`
    broker = ToolPermissionBroker(workspace)
    assert broker.evaluate("run_subagent", {"subagent_type": "explore", "prompt": "find files"}, mode="plan_mode") == "allow"
    assert broker.evaluate("run_subagent", {"subagent_type": "plan", "prompt": "design"}, mode="plan_mode") == "allow"
    assert broker.evaluate("run_subagent", {"subagent_type": "general", "prompt": "mutate"}, mode="plan_mode") == "deny"


def test_todo_write_updates_state_and_emits_code_mode_stage_event(tmp_path: Path) -> None:
    store = _FakeConversationStore()
    provider = _SubagentInspectingProvider()
    session = TurnSession(
        store=store,
        agents_store=None,
        memory_store=None,
        provider_factory=lambda: provider,
        workspace_root=tmp_path,
        turn={
            "turn_id": "turn-todo",
            "conversation_id": "conv-todo",
            "provider": "anthropic",
            "model_id": "claude-sonnet-4-6",
            "code_mode": "code",
            "assistant_message_id": "msg-todo",
        },
    )

    outcome = session._toolset(subagents=True).invoke(
        "todo_write",
        {
            "todos": [
                {"id": "1", "content": "Inspect existing tests", "status": "completed", "active_form": "Inspecting tests"},
                {"id": "2", "content": "Implement streaming command", "status": "in_progress", "active_form": "Implementing streaming command"},
                {"id": "3", "content": "Run full test suite", "status": "pending", "active_form": "Running test suite"},
            ]
        },
        call_id="call-todo-1",
    )
    assert outcome.status == "succeeded"
    assert outcome.payload["in_progress"] == 1
    assert outcome.payload["completed"] == 1
    assert outcome.payload["pending"] == 1
    assert len(session._todos_state) == 3

    # Emit tool_finished lifecycle and verify CODE_MODE_STAGE_CHANGED carries the todos list
    session.emit_lifecycle(
        session.turn,
        "tool_finished",
        tool_name="todo_write",
        invocation_id="call-todo-1",
        status="succeeded",
        summary=outcome.summary,
        tool_arguments={"todos": session._todos_state},
    )
    stage_events = [
        (summary, payload)
        for event_type, summary, payload in store.events
        if event_type is AgentActivityEventType.CODE_MODE_STAGE_CHANGED and payload.get("todos")
    ]
    assert len(stage_events) == 1
    stage_summary, stage_payload = stage_events[0]
    assert "1/3" in stage_summary
    assert len(stage_payload["todos"]) == 3
    assert stage_payload["todos"][1]["status"] == "in_progress"
    assert stage_payload["todos"][1]["active_form"] == "Implementing streaming command"


class _NineSectionProvider:
    """Returns a 9-section summary wrapped with an <analysis> scratchpad block."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def stream(self, request: Mapping[str, Any]):
        self.requests.append(dict(request))
        sections_text = "\n".join(
            f"### {section}\n- detail for {section}" for section in CODE_COMPACTION_SECTIONS
        )
        raw = (
            "<analysis>\nLet me review the chronological steps before writing the summary.\n"
            "Secret scratchpad thought that should be stripped.\n</analysis>\n\n"
            + sections_text
        )
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text=raw)])


def test_three_tier_compaction_and_top_5_file_rehydration(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "conv-compact")
    todos_state: list[dict[str, Any]] = []
    toolset = AgentToolset(workspace, code_mode_active=True, todos_state=todos_state)

    # Create 6 files and read them in order so the 5 most recent are file_1..file_5
    for idx in range(6):
        p = workspace.root / f"module_{idx}.py"
        p.write_text(f"VALUE_{idx} = {idx}\n", encoding="utf-8")
        res = toolset.invoke("read_file", {"path": f"module_{idx}.py"})
        assert res.status == "succeeded"

    # Also write an active plan in .orin/plans/ and record todos
    plan_res = toolset.invoke(
        "write_file",
        {"path": ".orin/plans/refactor.md", "content": "# Plan\n1. Update modules\n"},
    )
    assert plan_res.status == "succeeded"
    toolset.invoke(
        "todo_write",
        {
            "todos": [
                {"id": "t1", "content": "Update module_5", "status": "in_progress", "active_form": "Updating module_5"},
            ]
        },
    )

    # 1. Test Level 1 (_microcompact) directly on large tool results (> 3000 chars)
    provider = _NineSectionProvider()
    runtime = AgenticTurnRuntime(
        store=object(),
        provider=provider,
        toolset=toolset,
        system_prompt="code system prompt",
        limits=AgenticLimits(max_context_tokens=1_200),
        context_reporting=False,
    )
    large_messages: list[dict[str, object]] = []
    for i in range(6):
        large_messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": f'{{"path": "module_{i}.py"}}'}}
                ],
            }
        )
        large_messages.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "content": f"header_{i}\n" + ("z" * 4_500),
            }
        )
    large_messages.append({"role": "user", "content": "Finish updating module_5"})
    runtime._pinned_index = len(large_messages) - 1

    shrunk_count = runtime._microcompact(large_messages, keep_recent_units=4)
    assert shrunk_count == 2
    assert "[microcompacted:" in str(large_messages[1]["content"])
    assert "[microcompacted:" in str(large_messages[3]["content"])

    # 2. Test Level 2 (9-section autocompact + <analysis> stripping + Top-5 file rehydration)
    bulky_messages: list[dict[str, object]] = [
        {"role": "user", "content": f"step {index} " + ("x" * 600)} for index in range(10)
    ]
    bulky_messages.append({"role": "user", "content": "Finish updating module_5"})
    runtime._pinned_index = len(bulky_messages) - 1

    runtime._maybe_compact(bulky_messages, {"turn_id": "turn-c2", "code_mode": "code"}, [])

    assert runtime._compaction_count == 1
    compacted_msg = next(
        item for item in bulky_messages if str(item.get("content", "")).startswith("## Contexto compactado")
    )
    compacted_text = str(compacted_msg["content"])

    # Verify <analysis> scratchpad was stripped
    assert "<analysis>" not in compacted_text
    assert "Secret scratchpad thought" not in compacted_text
    assert strip_analysis_block("<analysis>hidden</analysis>Visible") == "Visible"

    # Verify all 9 Claude Code compaction sections are present
    for section in CODE_COMPACTION_SECTIONS:
        assert f"### {section}" in compacted_text

    # Verify Post-Compact Re-hydration: Top-5 most recent files, active plan, and active todos
    assert "### Recently Accessed Files (Re-hydrated)" in compacted_text
    assert "### Current Todo List" in compacted_text
    assert "### Active Plan (`.orin/plans/refactor.md`)" in compacted_text
    assert "Update module_5" in compacted_text
    assert "module_5.py" in compacted_text
    assert "module_4.py" in compacted_text
    assert "module_3.py" in compacted_text
    assert "module_2.py" in compacted_text
    # module_0.py was the oldest of the 7 touched files (module_0..5 + refactor.md), so it is outside top 5
    assert "#### `module_0.py`" not in compacted_text


def test_reactive_compact_recovers_from_context_length_exceeded() -> None:
    class _OverflowThenSucceedProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request: Mapping[str, Any]):
            self.calls += 1
            turn_id = str(request.get("turn_id") or "")
            if turn_id.endswith(":context-compaction"):
                sections_text = "\n".join(
                    f"### {section}\n- compacted" for section in CODE_COMPACTION_SECTIONS
                )
                return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text=sections_text)])
            if self.calls == 1:
                raise RuntimeError("Error 400: context_length_exceeded - prompt is too long")
            return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text="Recovered after reactive compaction!")])

    class _MinimalRuntimeStore:
        def __init__(self) -> None:
            self.deltas: list[str] = []
            self.finished_code: str | None = None

        def history_for_turn(self, turn: Mapping[str, Any]) -> list[dict[str, Any]]:
            msgs: list[dict[str, Any]] = [
                {"role": "user", "content": f"history {i} " + ("a" * 500)} for i in range(8)
            ]
            msgs.append({"role": "user", "content": "Final question"})
            return msgs

        def lifecycle(self, turn: Mapping[str, Any], state: str, **payload: Any) -> None:
            pass

        def effect_started(self, turn: Mapping[str, Any], **kwargs: Any) -> str:
            return "eff-1"

        def effect_finished(self, turn: Mapping[str, Any], effect_id: str, **kwargs: Any) -> None:
            pass

        def delta(self, turn: Mapping[str, Any], text: str) -> None:
            self.deltas.append(text)

        def finish(self, turn: Mapping[str, Any], *, code: str | None = None) -> None:
            self.finished_code = code

    store = _MinimalRuntimeStore()
    provider = _OverflowThenSucceedProvider()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        limits=AgenticLimits(max_context_tokens=50_000, max_provider_retries=0),
    )
    result = runtime.run(
        "turn-reactive",
        turn={
            "turn_id": "turn-reactive",
            "code_mode": "code",
            "assistant_message_id": "msg-reactive-1",
        },
    )
    assert result.state == "completed"
    assert "".join(store.deltas) == "Recovered after reactive compaction!"
    assert runtime._compaction_count >= 1

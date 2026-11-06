from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from agentos.agentic.agent_tools import (
    AgentToolset,
    ConversationWorkspace,
    ToolOutcome,
)
from agentos.agentic.native_turn import RuntimeToolExecutor
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.providers.claude_cli.adapter import ClaudeCliAdapter
from agentos.providers.cli_tools_mcp.types import ToolCallRequest


def _make_runtime(tmp_path: Path) -> tuple[AgenticTurnRuntime, dict[str, object]]:
    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "c1"),
        code_mode_active=True,
        public_tool_names=True,
        run_subagent=lambda **_: ToolOutcome("succeeded", "ok", "ok", {}),
        check_subagent=lambda **_: ToolOutcome("succeeded", "ok", "ok", {}),
        retrieval=object(),
    )
    rt = AgenticTurnRuntime(
        store=object(),  # type: ignore[arg-type]
        provider=object(),  # type: ignore[arg-type]
        actions=object(),  # type: ignore[arg-type]
        limits=AgenticLimits(
            deadline=timedelta(minutes=5),
            max_iterations=5,
            max_actions=10,
        ),
        toolset=toolset,
    )
    turn: dict[str, object] = {
        "turn_id": "t1",
        "conversation_mode": "code",
        "code_mode": "code",
    }
    return rt, turn


def test_list_tools_grows_after_tool_search_when_deferral_allowed(
    tmp_path: Path,
) -> None:
    rt, turn = _make_runtime(tmp_path)
    ex = RuntimeToolExecutor(rt, turn, allow_deferral=True)
    initial = {s.name for s in ex.list_tools()}
    assert "NotebookEdit" not in initial
    assert "ToolSearch" in initial

    res = ex.call_tool(
        ToolCallRequest(
            name="ToolSearch",
            arguments={"query": "select:NotebookEdit"},
        )
    )
    assert not res.is_error
    assert res.tools_changed is True
    after = {s.name for s in ex.list_tools()}
    assert "NotebookEdit" in after


def test_list_tools_contains_all_tools_when_deferral_disabled(
    tmp_path: Path,
) -> None:
    rt, turn = _make_runtime(tmp_path)
    ex = RuntimeToolExecutor(rt, turn, allow_deferral=False)
    names_listed = {s.name for s in ex.list_tools()}
    assert "NotebookEdit" in names_listed


def test_claude_cli_adapter_advertises_tool_list_changed() -> None:
    adapter = ClaudeCliAdapter()
    assert adapter.capabilities.tool_list_changed is True

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


def test_withheld_and_offered_tools_cannot_be_bypassed_via_deferred_or_tool_search(
    tmp_path: Path,
) -> None:
    """Regression test for Red Team #3:
    withheld_tools and offered_tools must block deferred_tool_names, ToolSearch, is_known_tool, and call_tool.
    """
    import json

    rt, turn = _make_runtime(tmp_path)
    rt.withheld_tools = {"notebook_edit"}
    ex = RuntimeToolExecutor(rt, turn, allow_deferral=True)

    assert ex.is_known_tool("NotebookEdit") is False
    assert ex.is_known_tool("notebook_edit") is False

    direct = ex.call_tool(
        ToolCallRequest(
            name="NotebookEdit",
            arguments={"notebook_path": "a.ipynb", "new_source": "x=1"},
        )
    )
    assert direct.is_error is True
    assert "Unknown tool" in direct.text

    search_res = ex.call_tool(
        ToolCallRequest(
            name="ToolSearch",
            arguments={"query": "select:NotebookEdit"},
        )
    )
    assert not search_res.is_error
    payload = json.loads(search_res.text)
    assert payload["count"] == 0
    assert payload["activated"] == []
    assert payload["already_available"] == []
    assert "NotebookEdit" in payload["not_found"]
    assert "notebook_edit" not in rt.toolset.deferred_tool_names
    assert "notebook_edit" not in rt.toolset.discovered_tool_names

    # Now test offered_tools whitelist
    rt2, turn2 = _make_runtime(tmp_path)
    rt2.offered_tools = {"read_file", "search_mcp_tools"}
    ex2 = RuntimeToolExecutor(rt2, turn2, allow_deferral=True)
    assert ex2.is_known_tool("Write") is False
    assert ex2.is_known_tool("NotebookEdit") is False
    assert ex2.is_known_tool("Read") is True

    direct_write = ex2.call_tool(
        ToolCallRequest(
            name="Write",
            arguments={"file_path": "x.txt", "content": "hi"},
        )
    )
    assert direct_write.is_error is True
    assert "Unknown tool" in direct_write.text

    search_write = ex2.call_tool(
        ToolCallRequest(
            name="ToolSearch",
            arguments={"query": "select:Write,NotebookEdit"},
        )
    )
    payload2 = json.loads(search_write.text)
    assert payload2["count"] == 0
    assert set(payload2["not_found"]) == {"Write", "NotebookEdit"}


from __future__ import annotations

from agentos.agentic.agent_tools import AgentToolset, ConversationWorkspace, ToolOutcome
from agentos.agentic.runtime import AgenticTurnRuntime


def test_first_context_of_a_code_turn_spends_under_4000_tool_tokens(tmp_path) -> None:
    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "c1"),
        code_mode_active=True,
        public_tool_names=True,
        run_subagent=lambda **_: ToolOutcome("succeeded", "ok", "ok", {}),
        check_subagent=lambda **_: ToolOutcome("succeeded", "ok", "ok", {}),
        retrieval=object(),
    )
    runtime = AgenticTurnRuntime(
        store=object(),
        provider=object(),
        toolset=toolset,
        tool_kinds={d.name: d.kind for d in toolset.definitions()},
    )
    schemas = runtime._names.schemas_out(runtime._tool_schemas({"code_mode": "code"}))
    usage = runtime._context_usage([], [], schemas)
    assert usage["tools_tokens"] < 4000
    assert {s["function"]["name"] for s in schemas} >= {"Read", "Edit", "Bash", "Grep", "Agent"}

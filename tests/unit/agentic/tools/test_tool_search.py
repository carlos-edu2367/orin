"""W4: ToolSearch's select: and +term forms."""
from __future__ import annotations

from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset, ToolDefinition
from agentos.agentic.tools.mcp.admin import ToolSearchOutput
from agentos.agentic.workspace import ConversationWorkspace


class _Provider:
    def definitions(self):
        names = ("mcp__github__create_issue", "mcp__github__list_issues", "mcp__slack__send_message")
        return tuple(
            ToolDefinition(name, f"{name} description", {"type": "object", "properties": {}}, lambda **_: {"content": "ok"}, "mcp")
            for name in names
        )


def _toolset(tmp_path: Path) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "c1"), mcp_provider=_Provider())


def test_select_loads_exactly_the_named_tools(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    outcome = toolset.invoke("ToolSearch", {"query": "select:mcp__github__create_issue,SEND_MESSAGE,Read,nope"})
    output = outcome.data["tool_output"]
    ToolSearchOutput.model_validate(output)
    assert output["matches"] == ["mcp__github__create_issue", "mcp__slack__send_message"]
    assert output["already_available"] == ["Read"] and output["not_found"] == ["nope"]
    assert {"mcp__github__create_issue", "mcp__slack__send_message"} <= toolset.active_mcp_tool_names


def test_plus_terms_are_required_in_the_name(tmp_path: Path) -> None:
    output = _toolset(tmp_path).invoke("ToolSearch", {"query": "+github issue"}).data["tool_output"]
    assert output["matches"] and all("github" in name for name in output["matches"])

import json


def test_select_loads_a_deferred_native_tool(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, public_tool_names=True)
    output = toolset.invoke("ToolSearch", {"query": "select:WebFetch,Read,nope"}).data["tool_output"]
    assert output["matches"] == ["WebFetch"]
    assert output["already_available"] == ["Read"] and output["not_found"] == ["nope"]
    assert "fetch_url" in toolset.discovered_tool_names


def test_keyword_search_returns_schemas_of_native_tools(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, public_tool_names=True)
    outcome = toolset.invoke("ToolSearch", {"query": "web page fetch"})
    payload = json.loads(outcome.content)
    tool = next(item for item in payload["tools"] if item["name"] == "WebFetch")
    assert tool["input_schema"]["type"] == "object" and tool["tool_kind"] == "native"


def test_mcp_and_native_results_coexist(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, mcp_provider=_Provider())
    output = toolset.invoke("ToolSearch", {"query": "select:mcp__github__create_issue,fetch_url"}).data["tool_output"]
    assert set(output["matches"]) == {"mcp__github__create_issue", "fetch_url"}


def test_select_returns_public_schemas_for_already_available_and_previously_discovered_tools(
    tmp_path: Path,
    monkeypatch,
) -> None:
    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "c1"),
        code_mode_active=True,
        public_tool_names=True,
        skills=object(),
        run_subagent=lambda **_: {"content": "ok"},
    )
    discovered_calls: list[list[str]] = []
    orig_discover = toolset.discover

    def spy_discover(names):
        batch = list(names)
        discovered_calls.append(batch)
        return orig_discover(batch)

    monkeypatch.setattr(toolset, "discover", spy_discover)

    outcome = toolset.invoke("ToolSearch", {"query": "select:Skill,Glob,Agent,nope"})
    assert outcome.status == "succeeded"
    payload = json.loads(outcome.content)
    output = outcome.data["tool_output"]
    ToolSearchOutput.model_validate(output)

    assert output["matches"] == ["Skill"]
    assert output["already_available"] == ["Glob", "Agent"]
    assert output["not_found"] == ["nope"]
    assert payload["already_available"] == ["Glob", "Agent"]
    assert payload["not_found"] == ["nope"]
    assert [c for c in discovered_calls if c] == [["use_skill"]]

    tools_by_name = {item["name"]: item for item in payload["tools"]}
    assert set(tools_by_name) == {"Skill", "Glob", "Agent"}
    assert tools_by_name["Skill"]["tool_kind"] == "native"
    assert not tools_by_name["Skill"].get("already_available")
    assert "skill" in tools_by_name["Skill"]["input_schema"]["properties"]

    assert tools_by_name["Glob"]["tool_kind"] == "native"
    assert tools_by_name["Glob"]["already_available"] is True
    assert "pattern" in tools_by_name["Glob"]["input_schema"]["properties"]
    assert "max_results" not in tools_by_name["Glob"]["input_schema"]["properties"]

    assert tools_by_name["Agent"]["tool_kind"] == "native"
    assert tools_by_name["Agent"]["already_available"] is True
    assert "prompt" in tools_by_name["Agent"]["input_schema"]["properties"]
    assert "task" not in tools_by_name["Agent"]["input_schema"]["properties"]

    # Selecting Skill a second time after discovery returns its schema with already_available=True
    discovered_calls.clear()
    second = toolset.invoke("ToolSearch", {"query": "select:Skill"})
    second_payload = json.loads(second.content)
    second_output = second.data["tool_output"]
    assert second_output["matches"] == []
    assert second_output["already_available"] == ["Skill"]
    assert second_payload["already_available"] == ["Skill"]
    assert second_payload["not_found"] == []
    assert [c for c in discovered_calls if c] == []
    assert len(second_payload["tools"]) == 1
    assert second_payload["tools"][0]["name"] == "Skill"
    assert second_payload["tools"][0]["tool_kind"] == "native"
    assert second_payload["tools"][0]["already_available"] is True
    assert "skill" in second_payload["tools"][0]["input_schema"]["properties"]


def test_select_already_available_uses_internal_schema_without_public_translator(tmp_path: Path) -> None:
    toolset = AgentToolset(
        ConversationWorkspace(tmp_path, "c1"),
        code_mode_active=False,
        public_tool_names=False,
        mcp_provider=_Provider(),
    )
    outcome = toolset.invoke("search_mcp_tools", {"query": "select:read_file"})
    payload = json.loads(outcome.content)
    assert payload["already_available"] == ["read_file"]
    assert payload["not_found"] == []
    assert payload["tools"][0]["name"] == "read_file"
    assert payload["tools"][0]["tool_kind"] == "native"
    assert payload["tools"][0]["already_available"] is True
    assert "path" in payload["tools"][0]["input_schema"]["properties"]


def test_allow_deferral_false_emits_call_mcp_tool_note_only_when_applicable(tmp_path: Path) -> None:
    from agentos.agentic.tool_policy import AllowList

    # 1) Code Mode, _allow_deferral=False, external MCP activated -> CallMcpTool note present
    ts_code = AgentToolset(
        ConversationWorkspace(tmp_path, "c_code"),
        code_mode_active=True,
        public_tool_names=True,
        mcp_provider=_Provider(),
    )
    ts_code._allow_deferral = False
    res_mcp = ts_code.invoke("ToolSearch", {"query": "select:mcp__github__create_issue"})
    payload_mcp = json.loads(res_mcp.content)
    assert payload_mcp["note"] == (
        "This client cannot reload its tool list during the turn: "
        "call these tools with CallMcpTool(server=..., tool=..., arguments={...})."
    )

    # 1b) Code Mode, _allow_deferral=False, keyword search activating external MCP -> CallMcpTool note present
    res_kw = ts_code.invoke("ToolSearch", {"query": "create_issue"})
    payload_kw = json.loads(res_kw.content)
    assert payload_kw["note"] == (
        "This client cannot reload its tool list during the turn: "
        "call these tools with CallMcpTool(server=..., tool=..., arguments={...})."
    )

    # 2) Code Mode, _allow_deferral=False, only native tools selected -> no note, treats deferred native as already_available
    res_native = ts_code.invoke("ToolSearch", {"query": "select:WebFetch,Glob"})
    payload_native = json.loads(res_native.content)
    assert "note" not in payload_native
    assert payload_native["already_available"] == ["WebFetch", "Glob"]
    assert {t["name"] for t in payload_native["tools"]} == {"WebFetch", "Glob"}

    # 3) Code Mode, _allow_deferral=False, activate=False -> no external MCP activated -> no note
    res_no_activate = ts_code.invoke("ToolSearch", {"query": "select:mcp__github__list_issues", "activate": False})
    assert "note" not in json.loads(res_no_activate.content)

    # 4) _allow_deferral=True -> no note even when external MCP activated
    ts_defer = AgentToolset(
        ConversationWorkspace(tmp_path, "c_defer"),
        code_mode_active=True,
        public_tool_names=True,
        mcp_provider=_Provider(),
    )
    ts_defer._allow_deferral = True
    assert "note" not in json.loads(ts_defer.invoke("ToolSearch", {"query": "select:mcp__github__create_issue"}).content)

    # 5) Chat Mode (public_tool_names=False), _allow_deferral=False -> call_mcp_tool note (internal name)
    ts_chat = AgentToolset(
        ConversationWorkspace(tmp_path, "c_chat"),
        code_mode_active=False,
        public_tool_names=False,
        mcp_provider=_Provider(),
    )
    ts_chat._allow_deferral = False
    res_chat = ts_chat.invoke("search_mcp_tools", {"query": "select:mcp__github__create_issue"})
    assert json.loads(res_chat.content)["note"] == (
        "This client cannot reload its tool list during the turn: "
        "call these tools with call_mcp_tool(server=..., tool=..., arguments={...})."
    )

    # 6) call_mcp_tool denied by policy -> no note even with _allow_deferral=False and external MCP activated
    ts_denied = AgentToolset(
        ConversationWorkspace(tmp_path, "c_denied"),
        code_mode_active=True,
        public_tool_names=True,
        mcp_provider=_Provider(),
        policy=AllowList(denied=("call_mcp_tool",)),
    )
    ts_denied._allow_deferral = False
    res_denied = ts_denied.invoke("ToolSearch", {"query": "select:mcp__github__create_issue"})
    assert "note" not in json.loads(res_denied.content)

    # 7) read_only_mode=True -> call_mcp_tool absent & no activation -> no note
    ts_ro = AgentToolset(
        ConversationWorkspace(tmp_path, "c_ro"),
        code_mode_active=True,
        public_tool_names=True,
        mcp_provider=_Provider(),
        read_only_mode=True,
    )
    ts_ro._allow_deferral = False
    res_ro = ts_ro.invoke("ToolSearch", {"query": "select:mcp__github__create_issue"})
    assert "note" not in json.loads(res_ro.content)

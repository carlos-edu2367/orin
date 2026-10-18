from agentos.agentic.agent_tools import AgentToolset, ToolDefinition, ToolOutcome
from agentos.agentic.tool_policy import AllowList
from agentos.agentic.workspace import ConversationWorkspace


class FakeProvider:
    def __init__(self) -> None:
        self.closed = False

    def definitions(self):
        return (ToolDefinition("mcp__demo__ping", "[Demo] ping", {"type": "object", "properties": {}},
                               lambda **_: ToolOutcome("succeeded", "pong", "pong"), "mcp",
                               policy_tags=("mcp", "mutates", "mcp:demo")),)

    def close(self) -> None:
        self.closed = True


def _workspace(tmp_path) -> ConversationWorkspace:
    return ConversationWorkspace(root=tmp_path, conversation_id="c1")


def test_mcp_definitions_join_the_native_tool_set(tmp_path):
    toolset = AgentToolset(_workspace(tmp_path), mcp_provider=FakeProvider())
    assert "mcp__demo__ping" in {item.name for item in toolset.definitions()}


def test_an_mcp_tool_is_invocable_through_the_toolset(tmp_path):
    toolset = AgentToolset(_workspace(tmp_path), mcp_provider=FakeProvider())
    assert toolset.invoke("mcp__demo__ping", {}).content == "pong"


def test_the_policy_can_deny_the_whole_mcp_family(tmp_path):
    toolset = AgentToolset(_workspace(tmp_path), mcp_provider=FakeProvider(), policy=AllowList(denied=("tag:mcp",)))
    assert "mcp__demo__ping" not in {item.name for item in toolset.definitions()}


def test_closing_the_toolset_closes_the_mcp_sessions(tmp_path):
    provider = FakeProvider()
    AgentToolset(_workspace(tmp_path), mcp_provider=provider).close()
    assert provider.closed is True


def test_mcp_tool_through_agent_toolset_returns_large_output_and_persists_when_exceeding_limit(tmp_path):
    from agentos.mcp.client import McpCallResult
    from agentos.mcp.models import McpServerConfig, McpServerState, McpToolDescriptor, McpTransport
    from agentos.mcp.toolset import MAX_MCP_RESULT_CHARS, McpToolProvider

    class _Client:
        def __init__(self, text: str) -> None:
            self.text = text
        def initialize(self): return None
        def call_tool(self, name, arguments):
            return McpCallResult(content=({"type": "text", "text": self.text},), is_error=False)
        def close(self): return None

    cfg = McpServerConfig(
        server_id="s1", user_id="u1", slug="auryly", display_name="Auryly",
        transport=McpTransport.HTTP, url="https://auryly.com/mcp", state=McpServerState.ACTIVE,
    )
    tool = McpToolDescriptor(name="get_lesson", description="Get lesson", input_schema={"type": "object", "properties": {}})

    # 1) A 45KB payload (previously truncated at 12KB) must arrive whole.
    payload_45k = '{"lesson": "' + ("a" * 45_000) + '"}'
    provider_45k = McpToolProvider([(cfg, (tool,), {})], client_factory=lambda *_, **__: _Client(payload_45k))
    outcome_45k = AgentToolset(_workspace(tmp_path), mcp_provider=provider_45k).invoke("mcp__auryly__get_lesson", {})
    assert outcome_45k.content == payload_45k
    assert not outcome_45k.payload.get("truncated")

    # 2) An oversized payload (> MAX_MCP_RESULT_CHARS) must be persisted to .orin/tool-outputs/ with truncation notice.
    payload_oversized = '{"lesson": "' + ("b" * (MAX_MCP_RESULT_CHARS + 10_000)) + '"}'
    provider_over = McpToolProvider([(cfg, (tool,), {})], client_factory=lambda *_, **__: _Client(payload_oversized))
    ws = _workspace(tmp_path)
    outcome_over = AgentToolset(ws, mcp_provider=provider_over).invoke("mcp__auryly__get_lesson", {})
    assert outcome_over.payload.get("truncated") is True
    saved_rel = outcome_over.payload.get("full_output_path")
    assert saved_rel and ws.resolve(saved_rel).read_text(encoding="utf-8") == payload_oversized
    assert "Full output saved to" in outcome_over.content


def _build_large_auryly_provider():
    from agentos.mcp.client import McpCallResult
    from agentos.mcp.models import McpServerConfig, McpServerState, McpToolDescriptor, McpTransport
    from agentos.mcp.toolset import McpToolProvider

    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def initialize(self):
            return None

        def call_tool(self, name, arguments):
            self.calls.append((name, dict(arguments)))
            return McpCallResult(content=({"type": "text", "text": f"called {name}"},), is_error=False)

        def close(self):
            return None

    client = _Client()
    cfg = McpServerConfig(
        server_id="s1",
        user_id="u1",
        slug="auryly",
        display_name="Auryly",
        transport=McpTransport.HTTP,
        url="https://auryly.com/mcp",
        state=McpServerState.ACTIVE,
    )
    tools = [
        McpToolDescriptor(
            name=f"filler_tool_{idx}",
            description=f"Filler operation {idx}",
            input_schema={"type": "object", "properties": {"id": {"type": "string"}}},
        )
        for idx in range(118)
    ]
    tools.append(
        McpToolDescriptor(
            name="get_funnel_metrics",
            description="Get conversion rates and drop-off analytics for a funnel.",
            input_schema={
                "type": "object",
                "properties": {
                    "funnel_id": {"type": "string", "description": "Funnel UUID"},
                    "date_from": {"type": "string"},
                },
                "required": ["funnel_id"],
            },
        )
    )
    tools.append(
        McpToolDescriptor(
            name="save_funnel_draft",
            description="Save steps and transitions of a funnel draft.",
            input_schema={
                "type": "object",
                "properties": {
                    "funnel_id": {"type": "string"},
                    "step_type": {"type": "string", "enum": ["quiz_single_choice", "checkout_redirect"]},
                    "redirect_url": {"type": "string"},
                },
                "required": ["funnel_id"],
            },
        )
    )
    return McpToolProvider([(cfg, tuple(tools), {})], client_factory=lambda *_, **__: client), client


def test_mcp_remote_tool_schemas_are_lazy_loaded_by_default(tmp_path):
    from agentos.agentic.phases import Phase, kinds_for, tools_for

    provider, _ = _build_large_auryly_provider()
    toolset = AgentToolset(_workspace(tmp_path), mcp_provider=provider)

    # All 120 remote tools remain in definitions() so direct invocation works
    defined = {item.name for item in toolset.definitions()}
    assert "mcp__auryly__get_funnel_metrics" in defined
    assert "mcp__auryly__filler_tool_0" in defined

    # However, schemas() must NOT eagerly publish the 120 remote tools
    initial_schemas = {item["function"]["name"] for item in toolset.schemas()}
    assert not any(name.startswith("mcp__auryly__") for name in initial_schemas)
    assert {"search_mcp_tools", "inspect_mcp_server", "call_mcp_tool"} <= initial_schemas

    orient_schemas = {
        item["function"]["name"]
        for item in toolset.schemas(allowed=tools_for(Phase.ORIENT), kinds=kinds_for(None, phase=Phase.ORIENT))
    }
    assert not any(name.startswith("mcp__auryly__") for name in orient_schemas)
    assert {"search_mcp_tools", "inspect_mcp_server", "call_mcp_tool"} <= orient_schemas


def test_search_mcp_tools_finds_by_schema_and_activates_tools_in_current_turn(tmp_path):
    provider, _ = _build_large_auryly_provider()
    toolset = AgentToolset(_workspace(tmp_path), mcp_provider=provider)

    outcome = toolset.invoke("search_mcp_tools", {"query": "quiz_single_choice redirect_url", "limit": 3})
    assert outcome.status == "succeeded"
    results = outcome.payload["tools"]
    assert results
    assert results[0]["qualified_name"] == "mcp__auryly__save_funnel_draft"
    assert "input_schema" in results[0]

    # The matched tools are now active and published in schemas()
    active_schemas = {item["function"]["name"] for item in toolset.schemas()}
    assert "mcp__auryly__save_funnel_draft" in active_schemas
    assert "mcp__auryly__filler_tool_99" not in active_schemas


def test_inspect_mcp_server_returns_all_server_tools_and_optionally_activates_them(tmp_path):
    provider, _ = _build_large_auryly_provider()
    toolset = AgentToolset(_workspace(tmp_path), mcp_provider=provider)

    outcome = toolset.invoke(
        "inspect_mcp_server",
        {"server": "auryly", "include_schemas": True, "activate_all": False, "limit": 150},
    )
    assert outcome.status == "succeeded"
    assert outcome.payload["tool_count"] == 120
    assert len(outcome.payload["tools"]) == 120
    assert "input_schema" in outcome.payload["tools"][0]
    assert not toolset.active_mcp_tool_names

    outcome_activate = toolset.invoke(
        "inspect_mcp_server",
        {"server": "auryly", "include_schemas": False, "activate_all": True, "limit": 5},
    )
    assert outcome_activate.status == "succeeded"
    assert len(toolset.active_mcp_tool_names) == 5


def test_call_mcp_tool_invokes_remote_tool_directly_and_respects_read_only_mode(tmp_path):
    provider, client = _build_large_auryly_provider()
    toolset = AgentToolset(_workspace(tmp_path), mcp_provider=provider)

    outcome = toolset.invoke(
        "call_mcp_tool",
        {"server": "auryly", "tool": "get_funnel_metrics", "arguments": {"funnel_id": "fn_42"}},
    )
    assert outcome.status == "succeeded"
    assert "called get_funnel_metrics" in outcome.content
    assert client.calls == [("get_funnel_metrics", {"funnel_id": "fn_42"})]

    # Adversarial: read_only_mode (explore / plan subagents) can search/inspect MCPs but CANNOT call_mcp_tool
    ro_toolset = AgentToolset(_workspace(tmp_path), mcp_provider=provider, read_only_mode=True)
    ro_names = {item.name for item in ro_toolset.definitions()}
    assert "search_mcp_tools" in ro_names
    assert "inspect_mcp_server" in ro_names
    assert "call_mcp_tool" not in ro_names
    assert not any(name.startswith("mcp__") for name in ro_names)


def test_system_prompt_includes_compact_connected_mcp_summary():
    from agentos.agentic.session import build_system_prompt

    _, volatile = build_system_prompt(
        tool_names=("search_mcp_tools", "inspect_mcp_server", "call_mcp_tool"),
        memories=[],
        agents=[],
        workspace_hint="Hint",
        subagents_enabled=False,
        mcp_servers=({"slug": "auryly", "display_name": "Auryly", "tool_count": 117},),
    )
    assert "auryly" in volatile
    assert "Auryly" in volatile
    assert "117" in volatile
    assert "search_mcp_tools" in volatile
    assert "inspect_mcp_server" in volatile
    assert "call_mcp_tool" in volatile


def test_adversarial_mcp_meta_tools_and_code_mode_lazy_publication(tmp_path):
    provider, client = _build_large_auryly_provider()
    toolset = AgentToolset(_workspace(tmp_path), mcp_provider=provider, code_mode_active=True)

    # 1) Stringified JSON arguments are accepted gracefully
    outcome_str_json = toolset.invoke(
        "call_mcp_tool",
        {"server": "Auryly", "tool": "get_funnel_metrics", "arguments": '{"funnel_id": "fn_str"}'},
    )
    assert outcome_str_json.status == "succeeded"
    assert client.calls[-1] == ("get_funnel_metrics", {"funnel_id": "fn_str"})

    # 2) Malformed JSON arguments fail cleanly without crashing the turn
    outcome_bad_json = toolset.invoke(
        "call_mcp_tool",
        {"server": "auryly", "tool": "get_funnel_metrics", "arguments": "{not-valid-json"},
    )
    assert outcome_bad_json.status == "failed"
    assert "Invalid JSON" in outcome_bad_json.content

    # 3) Unknown server or unknown tool returns actionable guidance
    outcome_unknown_srv = toolset.invoke("inspect_mcp_server", {"server": "nonexistent"})
    assert outcome_unknown_srv.status == "failed"
    assert "Connected servers: auryly" in outcome_unknown_srv.content

    outcome_unknown_tool = toolset.invoke("call_mcp_tool", {"server": "auryly", "tool": "nonexistent_tool"})
    assert outcome_unknown_tool.status == "failed"
    assert "search_mcp_tools" in outcome_unknown_tool.content




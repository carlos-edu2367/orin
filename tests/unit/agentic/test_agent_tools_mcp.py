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


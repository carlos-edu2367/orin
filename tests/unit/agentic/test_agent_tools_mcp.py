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

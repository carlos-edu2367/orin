from agentos.mcp.client import McpCallResult
from agentos.mcp.models import McpServerConfig, McpServerState, McpToolDescriptor, McpTransport
from agentos.mcp.toolset import McpToolProvider


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.closed = False
        self.result = McpCallResult(content=({"type": "text", "text": "ok"},), is_error=False)

    def initialize(self):
        return None

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return self.result

    def close(self):
        self.closed = True


def _config() -> McpServerConfig:
    return McpServerConfig(server_id="s1", user_id="u1", slug="notion", display_name="Notion",
                           transport=McpTransport.HTTP, url="https://mcp.example.com/v1",
                           state=McpServerState.ACTIVE)


def _provider(client: FakeClient) -> McpToolProvider:
    tools = (McpToolDescriptor(name="search", description="Search pages", input_schema={"type": "object", "properties": {"q": {"type": "string"}}}),)
    return McpToolProvider([(_config(), tools, {})], client_factory=lambda config, secrets: client)


def test_definitions_are_namespaced_and_tagged():
    definition = _provider(FakeClient()).definitions()[0]
    assert definition.name == "mcp__notion__search"
    assert definition.kind == "mcp"
    assert "mcp" in definition.policy_tags
    assert "Notion" in definition.description


def test_no_session_is_opened_until_a_tool_is_called():
    client = FakeClient()
    provider = _provider(client)
    provider.definitions()
    assert provider.open_session_count == 0


def test_invoking_a_definition_calls_the_remote_tool_with_its_bare_name():
    client = FakeClient()
    provider = _provider(client)
    outcome = provider.definitions()[0].handler(q="roadmap")
    assert client.calls == [("search", {"q": "roadmap"})]
    assert outcome.status == "succeeded"
    assert outcome.content == "ok"
    assert outcome.payload["mcp_server"] == "notion"


def test_a_server_side_tool_error_becomes_a_failed_outcome():
    client = FakeClient()
    client.result = McpCallResult(content=({"type": "text", "text": "no access"},), is_error=True)
    outcome = _provider(client).definitions()[0].handler(q="x")
    assert outcome.status == "failed"
    assert outcome.error_code == "MCP_TOOL_ERROR"


def test_an_image_block_becomes_an_image_on_the_outcome():
    client = FakeClient()
    client.result = McpCallResult(content=({"type": "image", "data": "AAAA", "mimeType": "image/png"},), is_error=False)
    outcome = _provider(client).definitions()[0].handler(q="x")
    assert outcome.images == [{"media_type": "image/png", "data": "AAAA"}]


def test_close_closes_every_open_session():
    client = FakeClient()
    provider = _provider(client)
    provider.definitions()[0].handler(q="x")
    provider.close()
    assert client.closed is True
    assert provider.open_session_count == 0

import threading

import pytest

from agentos.mcp.client import McpCallResult, McpNegotiation
from agentos.mcp.models import McpAuthKind, McpServerConfig, McpServerState, McpToolDescriptor, McpTransport
from agentos.mcp.token_source import McpReauthRequired
from agentos.mcp.toolset import McpToolProvider, discover


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


class FakeDiscoveryClient:
    def __init__(self, *, tools=(), fail_list_tools: bool = False) -> None:
        self.closed = False
        self._tools = tools
        self._fail_list_tools = fail_list_tools

    def initialize(self):
        return McpNegotiation(protocol_version="2025-06-18", server_name="demo", capabilities={})

    def list_tools(self):
        if self._fail_list_tools:
            raise RuntimeError("boom")
        return self._tools

    def close(self):
        self.closed = True


def test_discover_returns_the_negotiated_version_and_the_tool_list(monkeypatch):
    tools = (McpToolDescriptor(name="search", description="d", input_schema={"type": "object"}),)
    client = FakeDiscoveryClient(tools=tools)
    monkeypatch.setattr("agentos.mcp.toolset.build_client", lambda config, secrets: client)

    protocol_version, discovered = discover(_config(), {})

    assert protocol_version == "2025-06-18"
    assert discovered == tools


def test_discover_closes_the_client_even_when_listing_tools_fails(monkeypatch):
    client = FakeDiscoveryClient(fail_list_tools=True)
    monkeypatch.setattr("agentos.mcp.toolset.build_client", lambda config, secrets: client)

    with pytest.raises(RuntimeError):
        discover(_config(), {})

    assert client.closed is True


def _oauth_config() -> McpServerConfig:
    return McpServerConfig(server_id="s1", user_id="u1", slug="auryly", display_name="Auryly",
                           transport=McpTransport.HTTP, url="https://mcp.example.com/mcp", auth_kind=McpAuthKind.OAUTH)


class _FakeOAuth:
    def __init__(self) -> None:
        self.requested: list[str] = []

    def token_source(self, config):
        self.requested.append(config.server_id)
        return "token-source"


def test_an_oauth_server_gets_its_token_source():
    built: list[object] = []

    class Client:
        def initialize(self): pass
        def call_tool(self, name, arguments):
            from agentos.mcp.client import McpCallResult
            return McpCallResult(content=({"type": "text", "text": "ok"},), is_error=False)
        def close(self): pass

    def factory(config, secrets, *, token_source=None):
        built.append(token_source)
        return Client()

    tool = McpToolDescriptor(name="list", description="d", input_schema={"type": "object"})
    oauth = _FakeOAuth()
    provider = McpToolProvider([(_oauth_config(), (tool,), {})], client_factory=factory, oauth=oauth)
    outcome = provider.definitions()[0].handler()
    assert outcome.status == "succeeded"
    assert built == ["token-source"] and oauth.requested == ["s1"]


def test_a_lost_sign_in_becomes_a_reauth_outcome():
    class Client:
        def initialize(self): raise McpReauthRequired("O acesso a Auryly expirou. Reconecte em Configurações → MCP.")
        def close(self): pass

    tool = McpToolDescriptor(name="list", description="d", input_schema={"type": "object"})
    provider = McpToolProvider([(_oauth_config(), (tool,), {})],
                               client_factory=lambda config, secrets, token_source=None: Client(), oauth=_FakeOAuth())
    outcome = provider.definitions()[0].handler()
    assert outcome.status == "failed"
    assert outcome.error_code == "MCP_REAUTH_REQUIRED"
    assert "Reconecte" in outcome.content


def test_calls_to_one_server_never_overlap():
    active = {"now": 0, "max": 0}
    guard = threading.Lock()

    class Client:
        def initialize(self): pass
        def call_tool(self, name, arguments):
            from agentos.mcp.client import McpCallResult
            with guard:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            threading.Event().wait(0.02)
            with guard:
                active["now"] -= 1
            return McpCallResult(content=(), is_error=False)
        def close(self): pass

    tool = McpToolDescriptor(name="list", description="d", input_schema={"type": "object"})
    provider = McpToolProvider([(_oauth_config(), (tool,), {})],
                               client_factory=lambda config, secrets, token_source=None: Client(), oauth=_FakeOAuth())
    handler = provider.definitions()[0].handler
    threads = [threading.Thread(target=handler) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert active["max"] == 1

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


def test_long_tool_descriptions_are_preserved_in_definitions():
    long_desc = "Step configuration rules. " * 200  # 5200 chars
    tools = (McpToolDescriptor(name="save_funnel_draft", description=long_desc, input_schema={"type": "object"}),)
    provider = McpToolProvider([(_config(), tools, {})], client_factory=lambda config, secrets: FakeClient())
    definition = provider.definitions()[0]
    assert len(definition.description) > 5000
    assert definition.max_result_size_chars >= 100_000


def test_large_mcp_result_is_not_truncated_at_12k_chars():
    client = FakeClient()
    large_json = '{"steps": "' + ("x" * 45_000) + '"}'
    client.result = McpCallResult(content=({"type": "text", "text": large_json},), is_error=False)
    outcome = _provider(client).definitions()[0].handler(q="funnel")
    assert len(outcome.content) == len(large_json)
    assert outcome.content.endswith('"}')


def test_failed_initialize_closes_the_unregistered_client():
    closed_clients: list[bool] = []

    class FailingInitClient:
        def initialize(self):
            raise RuntimeError("handshake failed")

        def close(self):
            closed_clients.append(True)

    tools = (McpToolDescriptor(name="search", description="d", input_schema={"type": "object"}),)
    provider = McpToolProvider([(_config(), tools, {})], client_factory=lambda config, secrets: FailingInitClient())
    outcome = provider.definitions()[0].handler(q="x")
    assert outcome.status == "failed"
    assert closed_clients == [True]
    assert provider.open_session_count == 0


def test_transport_error_during_call_evicts_session_so_next_call_reconnects():
    created: list[FakeClient] = []

    class FlakyClient(FakeClient):
        def __init__(self, should_fail: bool) -> None:
            super().__init__()
            self.should_fail = should_fail

        def call_tool(self, name, arguments):
            if self.should_fail:
                raise RuntimeError("connection reset by peer")
            return super().call_tool(name, arguments)

    def factory(config, secrets):
        client = FlakyClient(should_fail=(len(created) == 0))
        created.append(client)
        return client

    tools = (McpToolDescriptor(name="search", description="d", input_schema={"type": "object"}),)
    provider = McpToolProvider([(_config(), tools, {})], client_factory=factory)
    handler = provider.definitions()[0].handler

    first = handler(q="1")
    assert first.status == "failed"
    assert created[0].closed is True

    second = handler(q="2")
    assert second.status == "succeeded"
    assert len(created) == 2


def test_provider_exposes_server_summaries_and_inspection():
    tools = (
        McpToolDescriptor(
            name="search",
            description="Search pages",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        ),
        McpToolDescriptor(
            name="create_page",
            description="Create page",
            input_schema={"type": "object", "properties": {"title": {"type": "string"}}},
        ),
    )
    provider = McpToolProvider([(_config(), tools, {})], client_factory=lambda config, secrets: FakeClient())
    summaries = provider.server_summaries()
    assert summaries == ({"slug": "notion", "display_name": "Notion", "tool_count": 2},)

    inspected = provider.inspect_server("notion", include_schemas=False)
    assert inspected is not None
    assert inspected["slug"] == "notion"
    assert inspected["tool_count"] == 2
    assert [t["name"] for t in inspected["tools"]] == ["search", "create_page"]
    assert "input_schema" not in inspected["tools"][0]

    inspected_with_schema = provider.inspect_server("Notion", include_schemas=True)
    assert inspected_with_schema is not None
    assert inspected_with_schema["tools"][0]["input_schema"]["properties"]["query"]["type"] == "string"

    hits = provider.search_tools("title")
    assert hits
    assert hits[0].qualified_name == "mcp__notion__create_page"


def _browser_config(*, args: tuple[str, ...] = ("-y", "@playwright/mcp@latest")) -> McpServerConfig:
    return McpServerConfig(
        server_id="pw-1",
        user_id="u1",
        slug="playwright",
        display_name="Playwright",
        transport=McpTransport.STDIO,
        command="npx",
        args=args,
        state=McpServerState.ACTIVE,
    )


def _stdio_non_browser_config() -> McpServerConfig:
    return McpServerConfig(
        server_id="fs-1",
        user_id="u1",
        slug="filesystem",
        display_name="Filesystem",
        transport=McpTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-filesystem", "."),
        state=McpServerState.ACTIVE,
    )


def test_browser_server_opens_distinct_sessions_for_main_and_subagents_with_isolated_arg():
    from agentos.agentic.agent_context import bind_agent_id

    created_configs: list[tuple[str | None, McpServerConfig, FakeClient]] = []

    def factory(config: McpServerConfig, secrets):
        client = FakeClient()
        from agentos.agentic.agent_context import get_current_agent_id

        created_configs.append((get_current_agent_id(), config, client))
        return client

    tools = (McpToolDescriptor(name="browser_navigate", description="Navigate", input_schema={"type": "object"}),)
    provider = McpToolProvider([(_browser_config(), tools, {})], client_factory=factory)
    handler = provider.definitions()[0].handler

    # Main agent uses original args (without forcing --isolated onto saved config)
    assert handler(url="http://localhost:5173").status == "succeeded"
    # Subagent 1 gets its own session with --isolated appended once
    with bind_agent_id("agent:sub-1"):
        assert handler(url="http://localhost:5173/a").status == "succeeded"
        assert handler(url="http://localhost:5173/a2").status == "succeeded"
    # Subagent 2 gets a third distinct session
    with bind_agent_id("agent:sub-2"):
        assert handler(url="http://localhost:5173/b").status == "succeeded"

    assert provider.open_session_count == 3
    assert len(created_configs) == 3

    main_agent, main_cfg, main_client = created_configs[0]
    sub1_agent, sub1_cfg, sub1_client = created_configs[1]
    sub2_agent, sub2_cfg, sub2_client = created_configs[2]

    assert main_agent is None
    assert main_cfg.args == ("-y", "@playwright/mcp@latest")
    assert sub1_agent == "agent:sub-1"
    assert sub1_cfg.args == ("-y", "@playwright/mcp@latest", "--isolated")
    assert sub2_agent == "agent:sub-2"
    assert sub2_cfg.args == ("-y", "@playwright/mcp@latest", "--isolated")
    assert len({id(main_client), id(sub1_client), id(sub2_client)}) == 3
    assert len(sub1_client.calls) == 2


def test_subagent_browser_session_does_not_duplicate_isolated_flag():
    from agentos.agentic.agent_context import bind_agent_id

    seen_args: list[tuple[str, ...]] = []

    def factory(config: McpServerConfig, secrets):
        seen_args.append(config.args)
        return FakeClient()

    tools = (McpToolDescriptor(name="browser_snapshot", description="Snapshot", input_schema={"type": "object"}),)
    provider = McpToolProvider(
        [(_browser_config(args=("-y", "@playwright/mcp@latest", "--isolated")), tools, {})],
        client_factory=factory,
    )
    handler = provider.definitions()[0].handler

    with bind_agent_id("agent:sub-1"):
        assert handler().status == "succeeded"

    assert seen_args == [("-y", "@playwright/mcp@latest", "--isolated")]


def test_parallel_subagent_browser_calls_do_not_block_each_other():
    from agentos.agentic.agent_context import bind_agent_id

    barrier = threading.Barrier(2, timeout=2.0)
    overlapped = {"ok": False}

    class ConcurrentClient(FakeClient):
        def call_tool(self, name, arguments):
            try:
                barrier.wait()
                overlapped["ok"] = True
            except threading.BrokenBarrierError:
                pass
            return super().call_tool(name, arguments)

    tools = (McpToolDescriptor(name="browser_click", description="Click", input_schema={"type": "object"}),)
    provider = McpToolProvider([(_browser_config(), tools, {})], client_factory=lambda cfg, sec: ConcurrentClient())
    handler = provider.definitions()[0].handler

    def run_for(agent_id: str) -> None:
        with bind_agent_id(agent_id):
            outcome = handler(ref="e1")
            assert outcome.status == "succeeded"

    t1 = threading.Thread(target=run_for, args=("agent:sub-1",))
    t2 = threading.Thread(target=run_for, args=("agent:sub-2",))
    t1.start()
    t2.start()
    t1.join(timeout=3.0)
    t2.join(timeout=3.0)

    assert overlapped["ok"] is True


def test_browser_session_limit_and_release_agent_frees_slot():
    from agentos.agentic.agent_context import bind_agent_id

    clients_by_agent: dict[str | None, FakeClient] = {}

    def factory(config: McpServerConfig, secrets):
        from agentos.agentic.agent_context import get_current_agent_id

        client = FakeClient()
        clients_by_agent[get_current_agent_id()] = client
        return client

    tools = (McpToolDescriptor(name="browser_navigate", description="Nav", input_schema={"type": "object"}),)
    provider = McpToolProvider(
        [(_browser_config(), tools, {})],
        client_factory=factory,
        max_browser_sessions=2,
        browser_slot_timeout=0.05,
    )
    handler = provider.definitions()[0].handler

    with bind_agent_id("agent:sub-1"):
        assert handler(url="http://localhost:1").status == "succeeded"
    with bind_agent_id("agent:sub-2"):
        assert handler(url="http://localhost:2").status == "succeeded"

    # A 3rd concurrent browser session exceeds max_browser_sessions=2 and fails clearly
    with bind_agent_id("agent:sub-3"):
        blocked = handler(url="http://localhost:3")
    assert blocked.status == "failed"
    assert "npx playwright test" in blocked.content
    assert provider.open_session_count == 2

    # Releasing sub-1 closes only sub-1's session and frees the slot for sub-3
    provider.release_agent("agent:sub-1")
    assert clients_by_agent["agent:sub-1"].closed is True
    assert clients_by_agent["agent:sub-2"].closed is False
    assert provider.open_session_count == 1

    with bind_agent_id("agent:sub-3"):
        allowed = handler(url="http://localhost:3")
    assert allowed.status == "succeeded"
    assert provider.open_session_count == 2


def test_subagent_browser_error_evicts_only_that_subagent_session():
    from agentos.agentic.agent_context import bind_agent_id

    clients_by_agent: dict[str | None, FakeClient] = {}

    class MaybeFailingClient(FakeClient):
        def __init__(self, fail: bool) -> None:
            super().__init__()
            self.fail = fail

        def call_tool(self, name, arguments):
            if self.fail:
                raise RuntimeError("timed out after 120s waiting for Playwright")
            return super().call_tool(name, arguments)

    def factory(config: McpServerConfig, secrets):
        from agentos.agentic.agent_context import get_current_agent_id

        agent_id = get_current_agent_id()
        client = MaybeFailingClient(fail=(agent_id == "agent:sub-bad"))
        clients_by_agent[agent_id] = client
        return client

    tools = (McpToolDescriptor(name="browser_click", description="Click", input_schema={"type": "object"}),)
    provider = McpToolProvider([(_browser_config(), tools, {})], client_factory=factory)
    handler = provider.definitions()[0].handler

    assert handler(ref="main").status == "succeeded"
    with bind_agent_id("agent:sub-good"):
        assert handler(ref="good").status == "succeeded"
    with bind_agent_id("agent:sub-bad"):
        failed = handler(ref="bad")
    assert failed.status == "failed"

    assert clients_by_agent["agent:sub-bad"].closed is True
    assert clients_by_agent[None].closed is False
    assert clients_by_agent["agent:sub-good"].closed is False
    assert provider.open_session_count == 2


def test_non_browser_server_continues_sharing_single_session_across_subagents():
    from agentos.agentic.agent_context import bind_agent_id

    created: list[FakeClient] = []

    def factory(config: McpServerConfig, secrets):
        client = FakeClient()
        created.append(client)
        return client

    tools = (McpToolDescriptor(name="read_file", description="Read", input_schema={"type": "object"}),)
    provider = McpToolProvider([(_stdio_non_browser_config(), tools, {})], client_factory=factory)
    handler = provider.definitions()[0].handler

    assert handler(path="a.txt").status == "succeeded"
    with bind_agent_id("agent:sub-1"):
        assert handler(path="b.txt").status == "succeeded"
    with bind_agent_id("agent:sub-2"):
        assert handler(path="c.txt").status == "succeeded"

    assert len(created) == 1
    assert provider.open_session_count == 1
    assert len(created[0].calls) == 3
    provider.release_agent("agent:sub-1")
    assert created[0].closed is False


def test_build_client_uses_120s_timeout_only_for_browser_server():
    from agentos.mcp.toolset import BROWSER_TIMEOUT_SECONDS, build_client
    from agentos.mcp.transport_stdio import DEFAULT_TIMEOUT_SECONDS

    browser_client = build_client(_browser_config(), {})
    other_client = build_client(_stdio_non_browser_config(), {})

    assert browser_client._transport._timeout == BROWSER_TIMEOUT_SECONDS == 120.0
    assert other_client._transport._timeout == DEFAULT_TIMEOUT_SECONDS == 45.0


def test_contextvar_propagates_to_runtime_worker_threads_and_waiting_model_is_tagged(tmp_path):
    from datetime import timedelta
    from agentos.agentic.agent_context import bind_agent_id, get_current_agent_id
    from agentos.agentic.agent_tools import ToolDefinition, ToolOutcome
    from agentos.agentic.events import AgentActivityEventType
    from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
    from agentos.agentic.session import TurnSession
    from agentos.agentic.workspace import ConversationWorkspace

    seen_agents: list[str | None] = []
    seen_threads: set[int] = set()
    guard = threading.Lock()

    def make_recording_tool(name: str) -> ToolDefinition:
        def _handler(**kwargs):
            with guard:
                seen_agents.append(get_current_agent_id())
                seen_threads.add(threading.get_ident())
            return ToolOutcome("succeeded", f"{name} ok", "ok", {"tool_kind": "mcp"})

        return ToolDefinition(
            name=name,
            description="test tool",
            parameters={"type": "object", "properties": {}},
            handler=_handler,
            kind="mcp",
            read_only=True,
        )

    class _Toolset:
        def definitions(self):
            return (make_recording_tool("mcp__playwright__a"), make_recording_tool("mcp__playwright__b"))

        def is_read_only(self, name):
            return True

        def is_concurrency_safe(self, name, arguments):
            return True

        def invoke(self, name, arguments, *, invocation_id=None):
            for d in self.definitions():
                if d.name == name:
                    return d.handler(**arguments)
            raise KeyError(name)

    class _Store:
        def __init__(self) -> None:
            self.turn = {"turn_id": "t1", "conversation_id": "c1", "model_id": "m1", "provider": "test"}

    store = _Store()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=object(),
        toolset=_Toolset(),
        system_prompt="test",
        limits=AgenticLimits(max_iterations=4, max_actions=6, deadline=timedelta(minutes=1)),
        tool_kinds={"mcp__playwright__a": "mcp", "mcp__playwright__b": "mcp"},
    )
    with bind_agent_id("agent:sub-parallel"):
        results = runtime._run_toolset(
            store.turn,
            [
                {"id": "c1", "name": "mcp__playwright__a", "arguments": "{}"},
                {"id": "c2", "name": "mcp__playwright__b", "arguments": "{}"},
            ],
        )
    assert [r["status"] for r in results] == ["succeeded", "succeeded"]
    assert seen_agents == ["agent:sub-parallel", "agent:sub-parallel"]
    assert threading.get_ident() not in seen_threads

    # Also verify TurnSession tags waiting_model events with phase="waiting_model" and waiting_model=True
    recorded: list[tuple] = []

    class _ActivityStore:
        def record(self, turn, event_type, summary, payload, **kw):
            recorded.append((event_type, summary, payload, kw))

    session = TurnSession(
        turn={"turn_id": "t1", "conversation_id": "conv-wait", "user_id": "u1", "model_id": "m1", "provider": "test"},
        store=_ActivityStore(),
        workspace_root=tmp_path,
    )
    session.emit_lifecycle("waiting_model", seconds=20, iteration=1, agent_id="agent:sub-1", agent_name="general-1")
    assert len(recorded) == 1
    ev_type, summary, payload, kw = recorded[0]
    assert ev_type == AgentActivityEventType.TOOL_PROGRESSED
    assert summary == "pensando há 20 s"
    assert payload.get("phase") == "waiting_model"
    assert payload.get("waiting_model") is True
    assert kw.get("agent_id") == "agent:sub-1"





import asyncio
import time

from httpx import ASGITransport, AsyncClient

from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app

SLOW_SECONDS = 1.0


class SlowMcp:
    """A fake MCP service whose approve()/test() block synchronously, the way a
    real npx/uvx subprocess or HTTP handshake does."""

    def list(self, user_id):
        return []

    def get(self, user_id, server_id):
        return {"server_id": server_id, "slug": "github", "display_name": "GitHub", "transport": "stdio",
                "command": "npx", "args": [], "url": None, "secret_names": [], "catalog_id": "github",
                "state": "pending_approval", "state_reason": "", "protocol_version": "", "tool_count": 0}

    def approve(self, *, user_id, server_id, secrets, connect):
        time.sleep(SLOW_SECONDS)
        return {**self.get(user_id, server_id), "state": "active"}

    def test(self, user_id, slug, connect):
        time.sleep(SLOW_SECONDS)
        return {"connected": True, "protocol_version": "", "tools": [], "error": None}


class SlowPlugins:
    """A fake plugin service whose inspect() blocks synchronously, the way a
    real `git clone` does."""

    def inspect(self, *, user_id, reference):
        time.sleep(SLOW_SECONDS)
        return {"plugin_id": "demo", "state": "pending_approval"}

    def list(self, user_id):
        return []

    def approve(self, *, user_id, plugin_id):
        time.sleep(SLOW_SECONDS)
        return {"plugin_id": plugin_id, "state": "active"}

    def discover_library(self, *, refresh=False, query=None):
        time.sleep(SLOW_SECONDS)
        return {"entries": [], "web_search_available": False}


def _headers(key: str = "k-1") -> dict[str, str]:
    return {"Authorization": "Bearer pat", "Idempotency-Key": key}


def _app(**services):
    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    return create_app(ApiServices(security=security, **services))


async def _fast_request_completes_while_slow_one_is_in_flight(app, slow_call, fast_call):
    # A real blocking call (time.sleep on the event-loop thread) freezes the whole
    # process, so a delay placed *before* issuing the fast request would itself be
    # delayed by the same amount — it can't be used to "wait for the slow request to
    # start". The only reliable signal is: how long after scenario start does the
    # fast request actually complete? If the loop is blocked, that time is pinned to
    # (at least) SLOW_SECONDS, no matter when the fast call was fired.
    transport = ASGITransport(app=app)
    scenario_started = time.monotonic()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        slow = asyncio.ensure_future(slow_call(client))
        await asyncio.sleep(SLOW_SECONDS / 4)  # give the slow request a head start
        await fast_call(client)
        fast_completed_at = time.monotonic() - scenario_started
        await slow
        return fast_completed_at


def test_mcp_approve_does_not_block_a_concurrent_simple_request():
    app = _app(mcp=SlowMcp())

    async def slow_call(client):
        return await client.post("/v1/mcp/servers/s1/approve", headers=_headers(), json={"secrets": {}})

    async def fast_call(client):
        return await client.get("/v1/mcp/servers", headers=_headers())

    fast_completed_at = asyncio.run(_fast_request_completes_while_slow_one_is_in_flight(app, slow_call, fast_call))
    assert fast_completed_at < SLOW_SECONDS / 2, (
        "a concurrent GET should not wait for a slow /approve to finish — the event loop is blocked"
    )


def test_mcp_test_route_does_not_block_a_concurrent_simple_request():
    app = _app(mcp=SlowMcp())

    async def slow_call(client):
        return await client.post("/v1/mcp/servers/s1/test", headers=_headers())

    async def fast_call(client):
        return await client.get("/v1/mcp/servers", headers=_headers())

    fast_completed_at = asyncio.run(_fast_request_completes_while_slow_one_is_in_flight(app, slow_call, fast_call))
    assert fast_completed_at < SLOW_SECONDS / 2, (
        "a concurrent GET should not wait for a slow /test to finish — the event loop is blocked"
    )


def test_plugin_inspect_does_not_block_a_concurrent_simple_request():
    app = _app(plugins=SlowPlugins())

    async def slow_call(client):
        return await client.post("/v1/plugins/inspect", headers=_headers(), json={"reference": "obra/superpowers"})

    async def fast_call(client):
        return await client.get("/v1/plugins", headers=_headers())

    fast_completed_at = asyncio.run(_fast_request_completes_while_slow_one_is_in_flight(app, slow_call, fast_call))
    assert fast_completed_at < SLOW_SECONDS / 2, (
        "a concurrent GET should not wait for a slow /inspect to finish — the event loop is blocked"
    )


def test_plugin_approve_does_not_block_a_concurrent_simple_request():
    app = _app(plugins=SlowPlugins())

    async def slow_call(client):
        return await client.post("/v1/plugins/demo/approve", headers=_headers())

    async def fast_call(client):
        return await client.get("/v1/plugins", headers=_headers())

    fast_completed_at = asyncio.run(_fast_request_completes_while_slow_one_is_in_flight(app, slow_call, fast_call))
    assert fast_completed_at < SLOW_SECONDS / 2, (
        "a concurrent GET should not wait for a slow plugin /approve to finish — the event loop is blocked"
    )


def test_plugin_library_does_not_block_a_concurrent_simple_request():
    app = _app(plugins=SlowPlugins())

    async def slow_call(client):
        return await client.get("/v1/plugins/library", headers=_headers())

    async def fast_call(client):
        return await client.get("/v1/plugins", headers=_headers())

    fast_completed_at = asyncio.run(_fast_request_completes_while_slow_one_is_in_flight(app, slow_call, fast_call))
    assert fast_completed_at < SLOW_SECONDS / 2, (
        "a concurrent GET should not wait for a slow /library to finish — the event loop is blocked"
    )

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import create_engine

from agentos.mcp.oauth_records import McpOAuthClient, McpOAuthRecords
from agentos.mcp.service import McpServerService
from agentos.mcp.token_source import LEASE_TTL, LeasedTokenSource, McpAuthUnavailable, McpReauthRequired
from agentos.oauth.flow import OAuthTokens
from agentos.oauth.token_store import OAuthTokenStore
from agentos.persistence.postgres.schema import metadata


@pytest.fixture(autouse=True)
def _skip_dns(monkeypatch):
    monkeypatch.setattr("agentos.oauth.netpolicy._public_url", lambda url, resolve_dns=False: url)


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA=")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    server_id = McpServerService(engine).propose({
        "user_id": "u1", "display_name": "Auryly", "transport": "http", "url": "https://mcp.example.com/mcp",
    })["server_id"]
    records = McpOAuthRecords(engine)
    records.save_client(McpOAuthClient(
        server_id=server_id, issuer="https://auth.example.com/", authorization_endpoint="https://auth.example.com/authorize",
        token_endpoint="https://auth.example.com/token", revocation_endpoint=None, resource="https://mcp.example.com/mcp",
        scope=None, client_id="c-1", client_secret=None, token_endpoint_auth_method="none",
        redirect_uri="http://127.0.0.1:49200/v1/mcp/oauth/callback"))
    return engine, OAuthTokenStore(engine), records, server_id


def _source(env, handler, *, sleep=lambda _s: None) -> LeasedTokenSource:
    _engine, store, records, server_id = env
    return LeasedTokenSource(store=store, records=records, user_id="u1", server_id=server_id, display_name="Auryly",
                             http_client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleep)


def _refused(_request: httpx.Request) -> httpx.Response:
    raise AssertionError("no refresh expected")


def test_a_fresh_token_is_used_without_refreshing(env):
    _engine, store, _records, server_id = env
    store.save(user_id="u1", provider_id=server_id, tokens=OAuthTokens("at", "rt", 3600, None))
    assert _source(env, _refused).current() == "at"


def test_a_token_close_to_expiry_is_refreshed_with_the_resource(env):
    _engine, store, _records, server_id = env
    store.save(user_id="u1", provider_id=server_id, tokens=OAuthTokens("at", "rt", 30, None))
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({key: values[0] for key, values in parse_qs(request.content.decode()).items()})
        return httpx.Response(200, json={"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600})

    assert _source(env, handler).current() == "at-2"
    assert seen["refresh_token"] == "rt" and seen["resource"] == "https://mcp.example.com/mcp"
    assert store.get(user_id="u1", provider_id=server_id).refresh_token == "rt-2"


def test_force_refresh_skips_when_another_caller_already_replaced_the_token(env):
    _engine, store, _records, server_id = env
    store.save(user_id="u1", provider_id=server_id, tokens=OAuthTokens("at-new", "rt", 3600, None))
    assert _source(env, _refused).force_refresh("at-old") == "at-new"


def test_a_rejected_refresh_forgets_the_tokens_and_marks_the_server(env):
    _engine, store, records, server_id = env
    store.save(user_id="u1", provider_id=server_id, tokens=OAuthTokens("at", "rt", 0, None))

    with pytest.raises(McpReauthRequired, match="Reconecte em Configurações → MCP"):
        _source(env, lambda r: httpx.Response(400, json={"error": "invalid_grant"})).current()

    assert store.get(user_id="u1", provider_id=server_id) is None
    assert records.server("u1", server_id)["state"] == "error"


def test_a_network_failure_keeps_the_tokens_and_frees_the_lease(env):
    _engine, store, _records, server_id = env
    store.save(user_id="u1", provider_id=server_id, tokens=OAuthTokens("at", "rt", 0, None))

    with pytest.raises(McpAuthUnavailable):
        _source(env, lambda r: httpx.Response(503)).current()

    assert store.get(user_id="u1", provider_id=server_id).refresh_token == "rt"
    assert store.try_acquire_refresh_lease(user_id="u1", provider_id=server_id, version=0, ttl=LEASE_TTL)


def test_a_caller_without_the_lease_waits_for_the_new_version(env):
    _engine, store, _records, server_id = env
    store.save(user_id="u1", provider_id=server_id, tokens=OAuthTokens("at", "rt", 0, None))
    assert store.try_acquire_refresh_lease(user_id="u1", provider_id=server_id, version=0, ttl=LEASE_TTL)
    naps: list[float] = []

    def other_process_finishes(seconds: float) -> None:
        naps.append(seconds)
        if len(naps) == 2:
            store.save(user_id="u1", provider_id=server_id, tokens=OAuthTokens("at-2", "rt-2", 3600, None))

    assert _source(env, _refused, sleep=other_process_finishes).current() == "at-2"
    assert naps == [0.25, 0.25]


def test_waiting_gives_up_after_the_limit(env):
    _engine, store, _records, server_id = env
    store.save(user_id="u1", provider_id=server_id, tokens=OAuthTokens("at", "rt", 0, None))
    store.try_acquire_refresh_lease(user_id="u1", provider_id=server_id, version=0, ttl=LEASE_TTL)
    with pytest.raises(McpAuthUnavailable):
        _source(env, _refused).current()


def test_missing_tokens_require_a_new_sign_in(env):
    with pytest.raises(McpReauthRequired):
        _source(env, _refused).current()


def test_a_grant_without_refresh_token_requires_a_new_sign_in_when_it_expires(env):
    _engine, store, _records, server_id = env
    store.save(user_id="u1", provider_id=server_id, tokens=OAuthTokens("at", None, 0, None))
    with pytest.raises(McpReauthRequired):
        _source(env, _refused).current()

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from agentos.oauth.flow import (
    OAuthFlowError,
    OAuthProviderConfig,
    begin_authorization,
    exchange_code_for_tokens,
    refresh_tokens,
)


def _config(**overrides) -> OAuthProviderConfig:
    defaults = dict(
        provider_id="acme",
        authorize_url="https://acme.example.com/oauth/authorize",
        token_url="https://acme.example.com/oauth/token",
        scopes=("read", "write"),
        client_id="client-123",
    )
    return OAuthProviderConfig(**{**defaults, **overrides})


def test_authorize_and_token_urls_must_be_https():
    with pytest.raises(ValueError):
        OAuthProviderConfig(provider_id="acme", authorize_url="http://acme.example.com/authorize",
                             token_url="https://acme.example.com/token", scopes=(), client_id="c")
    with pytest.raises(ValueError):
        OAuthProviderConfig(provider_id="acme", authorize_url="https://acme.example.com/authorize",
                             token_url="http://acme.example.com/token", scopes=(), client_id="c")


def test_a_provider_with_no_client_id_is_reported_as_unconfigured():
    config = _config(client_id=None)
    assert config.is_configured is False


def test_begin_authorization_refuses_a_provider_without_a_client_id():
    with pytest.raises(OAuthFlowError):
        begin_authorization(_config(client_id=None), redirect_uri="http://127.0.0.1:5555/callback")


def test_begin_authorization_refuses_a_non_loopback_redirect_uri():
    with pytest.raises(OAuthFlowError):
        begin_authorization(_config(), redirect_uri="https://attacker.example.com/callback")


def test_begin_authorization_builds_a_pkce_authorization_url():
    pending = begin_authorization(_config(), redirect_uri="http://127.0.0.1:5555/callback")
    parsed = urlparse(pending.authorization_url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https" and parsed.netloc == "acme.example.com"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["client-123"]
    assert query["redirect_uri"] == ["http://127.0.0.1:5555/callback"]
    assert query["scope"] == ["read write"]
    assert query["state"] == [pending.state]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) > 0
    assert query["code_challenge"][0] != pending.code_verifier  # the challenge is derived, not the raw verifier


def test_exchange_code_for_tokens_posts_the_verifier_and_parses_the_response():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["form"] = dict(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, "scope": "read"})

    pending = begin_authorization(_config(), redirect_uri="http://127.0.0.1:5555/callback")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    tokens = exchange_code_for_tokens(_config(), pending, code="auth-code-1", client=client)

    assert tokens.access_token == "at-1"
    assert tokens.refresh_token == "rt-1"
    assert tokens.expires_in == 3600
    assert captured["form"]["grant_type"] == ["authorization_code"]
    assert captured["form"]["code"] == ["auth-code-1"]
    assert captured["form"]["code_verifier"] == [pending.code_verifier]
    assert captured["form"]["redirect_uri"] == ["http://127.0.0.1:5555/callback"]


def test_exchange_code_for_tokens_raises_on_an_error_response():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    pending = begin_authorization(_config(), redirect_uri="http://127.0.0.1:5555/callback")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(OAuthFlowError):
        exchange_code_for_tokens(_config(), pending, code="bad-code", client=client)


def test_refresh_tokens_posts_the_refresh_token_and_parses_the_response():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["form"] = dict(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "at-2", "expires_in": 1800})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tokens = refresh_tokens(_config(), "rt-1", client=client)

    assert tokens.access_token == "at-2"
    assert tokens.refresh_token == "rt-1"  # provider omitted it; the old one still applies
    assert captured["form"]["grant_type"] == ["refresh_token"]
    assert captured["form"]["refresh_token"] == ["rt-1"]


def test_refresh_tokens_raises_on_an_error_response():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_grant"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(OAuthFlowError):
        refresh_tokens(_config(), "rt-expired", client=client)

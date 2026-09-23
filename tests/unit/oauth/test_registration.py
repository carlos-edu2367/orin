import json
from dataclasses import replace

import httpx
import pytest

from agentos.oauth.discovery import AuthorizationServerMetadata
from agentos.oauth.registration import (
    ClientRegistration,
    OAuthRegistrationError,
    OAuthRegistrationUnreachable,
    register_client,
)

METADATA = AuthorizationServerMetadata(
    issuer="https://auth.example.com/", authorization_endpoint="https://auth.example.com/authorize",
    token_endpoint="https://auth.example.com/token", registration_endpoint="https://auth.example.com/register",
    revocation_endpoint=None, scopes_supported=(),
)
REDIRECT = "http://127.0.0.1:49200/v1/mcp/oauth/callback"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_registers_as_a_public_client_with_the_loopback_redirect():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(201, json={"client_id": "c-1", "token_endpoint_auth_method": "none"})

    registration = register_client(METADATA, redirect_uri=REDIRECT, scope="content:admin", client=_client(handler))

    assert registration == ClientRegistration("c-1", None, "none")
    assert seen["redirect_uris"] == [REDIRECT]
    assert seen["token_endpoint_auth_method"] == "none"
    assert seen["grant_types"] == ["authorization_code", "refresh_token"]
    assert seen["scope"] == "content:admin"
    assert seen["client_name"] == "Orin"


def test_a_secret_issued_anyway_is_kept_and_used_by_post():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"client_id": "c-2", "client_secret": "s-2"})

    registration = register_client(METADATA, redirect_uri=REDIRECT, scope=None, client=_client(handler))
    assert registration == ClientRegistration("c-2", "s-2", "client_secret_post")


def test_a_server_without_a_registration_endpoint_is_refused():
    with pytest.raises(OAuthRegistrationError, match="dynamic client registration"):
        register_client(replace(METADATA, registration_endpoint=None), redirect_uri=REDIRECT, scope=None,
                        client=_client(lambda r: httpx.Response(500)))


def test_a_rejected_registration_is_an_error():
    with pytest.raises(OAuthRegistrationError):
        register_client(METADATA, redirect_uri=REDIRECT, scope=None,
                        client=_client(lambda r: httpx.Response(400, json={"error": "invalid_redirect_uri"})))


def test_a_registration_without_client_id_is_an_error():
    with pytest.raises(OAuthRegistrationError, match="client_id"):
        register_client(METADATA, redirect_uri=REDIRECT, scope=None, client=_client(lambda r: httpx.Response(201, json={})))


def test_a_server_error_is_unreachable():
    with pytest.raises(OAuthRegistrationUnreachable):
        register_client(METADATA, redirect_uri=REDIRECT, scope=None, client=_client(lambda r: httpx.Response(503)))

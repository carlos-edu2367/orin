"""OAuth 2.0 Dynamic Client Registration (RFC 7591) for MCP servers.

MCP servers such as the Auryly content server hand out client ids only through
their registration endpoint. Orin registers as a public client
(``token_endpoint_auth_method: none``, PKCE protects the code) and keeps a
secret only if the server insists on issuing one.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from .discovery import AuthorizationServerMetadata
from .netpolicy import OAuthUrlRefused, public_https

DEFAULT_TIMEOUT_SECONDS = 20.0
_AUTH_METHODS = frozenset({"none", "client_secret_post", "client_secret_basic"})


class OAuthRegistrationError(RuntimeError):
    """The server refused to register Orin, or answered with something unusable."""


class OAuthRegistrationUnreachable(OAuthRegistrationError):
    """The registration endpoint failed at the network level or with a server error."""


@dataclass(frozen=True, slots=True)
class ClientRegistration:
    client_id: str
    client_secret: str | None
    token_endpoint_auth_method: str


def register_client(
    metadata: AuthorizationServerMetadata, *, redirect_uri: str, scope: str | None, client: httpx.Client,
    client_name: str = "Orin",
) -> ClientRegistration:
    if not metadata.registration_endpoint:
        raise OAuthRegistrationError(f"'{metadata.issuer}' does not accept dynamic client registration")
    try:
        url = public_https(metadata.registration_endpoint)
    except OAuthUrlRefused as error:
        raise OAuthRegistrationError(str(error)) from error
    body: dict[str, object] = {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    if scope:
        body["scope"] = scope
    try:
        response = client.post(url, json=body, headers={"accept": "application/json"}, timeout=DEFAULT_TIMEOUT_SECONDS)
    except httpx.HTTPError as error:
        raise OAuthRegistrationUnreachable(f"the registration endpoint did not answer: {error}") from error
    if response.status_code >= 500:
        raise OAuthRegistrationUnreachable(f"the registration endpoint answered {response.status_code}")
    if response.status_code >= 400:
        raise OAuthRegistrationError(f"the registration endpoint refused Orin ({response.status_code})")
    try:
        payload = response.json()
    except ValueError as error:
        raise OAuthRegistrationError("the registration endpoint did not answer with JSON") from error
    if not isinstance(payload, dict):
        raise OAuthRegistrationError("the registration endpoint did not answer with a JSON object")
    client_id = payload.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise OAuthRegistrationError("the registration response carried no client_id")
    secret = payload.get("client_secret") if isinstance(payload.get("client_secret"), str) and payload.get("client_secret") else None
    method = payload.get("token_endpoint_auth_method")
    if not isinstance(method, str) or not method:
        method = "client_secret_post" if secret else "none"
    if method not in _AUTH_METHODS:
        raise OAuthRegistrationError(f"unsupported token_endpoint_auth_method '{method}'")
    return ClientRegistration(client_id=client_id, client_secret=secret, token_endpoint_auth_method=method)


__all__ = ["ClientRegistration", "OAuthRegistrationError", "OAuthRegistrationUnreachable", "register_client"]

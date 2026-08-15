"""MCP Authorization spec metadata discovery (RFC 9728 + RFC 8414).

A remote MCP server does not always publish a fixed, citable authorize/token
URL pair — the spec instead has it point at its authorization server via a
`/.well-known/oauth-protected-resource` document, and that authorization
server in turn publishes its own `/.well-known/oauth-authorization-server`
metadata. This walks that two-hop chain so no endpoint URL has to be
hardcoded (or guessed) per provider.
"""
from __future__ import annotations

import httpx

DEFAULT_TIMEOUT_SECONDS = 15.0


class OAuthDiscoveryError(RuntimeError):
    """The server's OAuth metadata could not be found or was incomplete."""


def discover_authorization_endpoints(resource_url: str, *, client: httpx.Client) -> tuple[str, str]:
    if not resource_url.lower().startswith("https://"):
        raise OAuthDiscoveryError("OAuth discovery requires an https resource URL")
    resource_metadata = _fetch_json(client, resource_url.rstrip("/") + "/.well-known/oauth-protected-resource")
    servers = resource_metadata.get("authorization_servers")
    if not isinstance(servers, list) or not servers or not isinstance(servers[0], str):
        raise OAuthDiscoveryError(f"'{resource_url}' names no authorization server")
    authorization_server = str(servers[0]).rstrip("/")
    if not authorization_server.lower().startswith("https://"):
        raise OAuthDiscoveryError(f"'{resource_url}' names a non-https authorization server")
    server_metadata = _fetch_json(client, authorization_server + "/.well-known/oauth-authorization-server")
    authorize_url = server_metadata.get("authorization_endpoint")
    token_url = server_metadata.get("token_endpoint")
    if not isinstance(authorize_url, str) or not authorize_url:
        raise OAuthDiscoveryError(f"'{authorization_server}' metadata is missing authorization_endpoint")
    if not isinstance(token_url, str) or not token_url:
        raise OAuthDiscoveryError(f"'{authorization_server}' metadata is missing token_endpoint")
    return authorize_url, token_url


def _fetch_json(client: httpx.Client, url: str) -> dict:
    try:
        response = client.get(url, timeout=DEFAULT_TIMEOUT_SECONDS)
    except httpx.HTTPError as error:
        raise OAuthDiscoveryError(f"could not reach '{url}': {error}") from error
    if response.status_code >= 400:
        raise OAuthDiscoveryError(f"'{url}' answered {response.status_code}")
    try:
        payload = response.json()
    except ValueError as error:
        raise OAuthDiscoveryError(f"'{url}' did not answer with JSON") from error
    if not isinstance(payload, dict):
        raise OAuthDiscoveryError(f"'{url}' did not answer with a JSON object")
    return payload


__all__ = ["OAuthDiscoveryError", "discover_authorization_endpoints"]

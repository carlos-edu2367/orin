"""MCP Authorization discovery: RFC 9728 (protected resource) and RFC 8414 (authorization server).

A 401 from an MCP server may name its metadata document in
``WWW-Authenticate: Bearer resource_metadata="..."``. Without it, the
well-known suffix goes between host and path (RFC 9728 §3.1, RFC 8414 §3.1):
``https://host/mcp`` is described at
``https://host/.well-known/oauth-protected-resource/mcp``, with the root
document as the fallback. OpenID Connect discovery is the last fallback for the
authorization server. Every fetched URL passes the network policy first.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from .netpolicy import OAuthUrlRefused, public_https

DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_METADATA_BYTES = 256_000


class OAuthDiscoveryError(RuntimeError):
    """The server's OAuth metadata could not be found, or it failed a check the spec requires."""


class OAuthDiscoveryUnreachable(OAuthDiscoveryError):
    """Every metadata location failed at the network level or with a server error."""


@dataclass(frozen=True, slots=True)
class BearerChallenge:
    resource_metadata: str | None
    scope: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class ProtectedResourceMetadata:
    resource: str
    authorization_servers: tuple[str, ...]
    scopes_supported: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AuthorizationServerMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    revocation_endpoint: str | None
    scopes_supported: tuple[str, ...]


_CHALLENGE_PARAM = re.compile(r'([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^\s,]+))')


def parse_www_authenticate(header: str | None) -> BearerChallenge:
    if not header:
        return BearerChallenge(None, None, None)
    bearer = re.search(r"(?i)\bbearer\b(.*)", header)
    if bearer is None:
        return BearerChallenge(None, None, None)
    params: dict[str, str] = {}
    for match in _CHALLENGE_PARAM.finditer(bearer.group(1)):
        quoted, bare = match.group(2), match.group(3)
        value = re.sub(r"\\(.)", r"\1", quoted) if quoted is not None else bare
        params.setdefault(match.group(1).lower(), value)
    return BearerChallenge(params.get("resource_metadata"), params.get("scope"), params.get("error"))


def discover_protected_resource(
    resource_url: str, *, challenge: BearerChallenge | None = None, client: httpx.Client,
) -> ProtectedResourceMetadata:
    candidates = _well_known(resource_url, "oauth-protected-resource")
    if challenge is not None and challenge.resource_metadata:
        candidates = (challenge.resource_metadata, *candidates)
    payload = _first_document(client, candidates)
    resource = payload.get("resource")
    if not isinstance(resource, str) or not _same_url(resource, resource_url):
        raise OAuthDiscoveryError(f"the metadata describes '{resource}', not '{resource_url}'")
    servers = tuple(item for item in payload.get("authorization_servers") or () if isinstance(item, str) and item)
    if not servers:
        raise OAuthDiscoveryError(f"'{resource_url}' names no authorization server")
    for server in servers:
        _require_https(server, "authorization server")
    return ProtectedResourceMetadata(resource=resource, authorization_servers=servers,
                                     scopes_supported=_strings(payload.get("scopes_supported")))


def discover_authorization_server(issuer: str, *, client: httpx.Client) -> AuthorizationServerMetadata:
    _require_https(issuer, "issuer")
    parts = urlsplit(issuer)
    base = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    path = parts.path.rstrip("/")
    candidates = [f"{base}/.well-known/oauth-authorization-server{path}", f"{base}/.well-known/openid-configuration{path}"]
    if path:
        candidates.append(f"{base}{path}/.well-known/openid-configuration")
    payload = _first_document(client, tuple(candidates))
    declared = payload.get("issuer")
    if not isinstance(declared, str) or not _same_url(declared, issuer):
        raise OAuthDiscoveryError(f"the metadata issuer '{declared}' does not match '{issuer}'")
    authorize = _require_https(payload.get("authorization_endpoint"), "authorization_endpoint")
    token = _require_https(payload.get("token_endpoint"), "token_endpoint")
    if "S256" not in _strings(payload.get("code_challenge_methods_supported")):
        raise OAuthDiscoveryError(f"'{issuer}' does not advertise PKCE S256")
    registration = payload.get("registration_endpoint")
    revocation = payload.get("revocation_endpoint")
    return AuthorizationServerMetadata(
        issuer=declared,
        authorization_endpoint=authorize,
        token_endpoint=token,
        registration_endpoint=_require_https(registration, "registration_endpoint") if registration else None,
        revocation_endpoint=_require_https(revocation, "revocation_endpoint") if revocation else None,
        scopes_supported=_strings(payload.get("scopes_supported")),
    )


def _well_known(url: str, suffix: str) -> tuple[str, ...]:
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    inserted = urlunsplit((parts.scheme, parts.netloc, f"/.well-known/{suffix}{path}", "", ""))
    root = urlunsplit((parts.scheme, parts.netloc, f"/.well-known/{suffix}", "", ""))
    return (inserted,) if inserted == root else (inserted, root)


def _same_url(left: str, right: str) -> bool:
    return left.rstrip("/") == right.rstrip("/")


def _require_https(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.lower().startswith("https://"):
        raise OAuthDiscoveryError(f"the {label} must be an https URL")
    return value


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _first_document(client: httpx.Client, candidates: tuple[str, ...]) -> dict[str, Any]:
    failures: list[OAuthDiscoveryError] = []
    for url in candidates:
        try:
            return _fetch_json(client, url)
        except OAuthDiscoveryError as error:
            failures.append(error)
    message = "; ".join(str(item) for item in failures) or "no metadata location to try"
    if failures and all(isinstance(item, OAuthDiscoveryUnreachable) for item in failures):
        raise OAuthDiscoveryUnreachable(message)
    raise OAuthDiscoveryError(message)


def _fetch_json(client: httpx.Client, url: str) -> dict[str, Any]:
    try:
        checked = public_https(url)
    except OAuthUrlRefused as error:
        raise OAuthDiscoveryError(str(error)) from error
    try:
        response = client.get(checked, headers={"accept": "application/json"}, timeout=DEFAULT_TIMEOUT_SECONDS)
    except httpx.HTTPError as error:
        raise OAuthDiscoveryUnreachable(f"could not reach '{url}': {error}") from error
    if response.status_code >= 500:
        raise OAuthDiscoveryUnreachable(f"'{url}' answered {response.status_code}")
    if response.status_code >= 400:
        raise OAuthDiscoveryError(f"'{url}' answered {response.status_code}")
    if len(response.content) > MAX_METADATA_BYTES:
        raise OAuthDiscoveryError(f"'{url}' answered with an oversized document")
    try:
        payload = response.json()
    except ValueError as error:
        raise OAuthDiscoveryError(f"'{url}' did not answer with JSON") from error
    if not isinstance(payload, dict):
        raise OAuthDiscoveryError(f"'{url}' did not answer with a JSON object")
    return payload


__all__ = [
    "AuthorizationServerMetadata", "BearerChallenge", "OAuthDiscoveryError", "OAuthDiscoveryUnreachable",
    "ProtectedResourceMetadata", "discover_authorization_server", "discover_protected_resource",
    "parse_www_authenticate",
]

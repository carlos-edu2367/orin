"""OAuth sign-in for remote MCP servers (MCP Authorization spec).

``start`` probes the server for its 401 challenge, discovers the protected
resource and its authorization server, registers Orin when it has no
registration for this redirect URI yet, stores a single-use pending sign-in and
returns the URL to open in the browser. ``complete`` runs when the browser
comes back to the API's callback route. Activation (tool discovery with the new
token) stays with ``McpServerService``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

import httpx
from sqlalchemy.engine import Engine

from agentos.oauth.discovery import (
    BearerChallenge,
    OAuthDiscoveryError,
    OAuthDiscoveryUnreachable,
    discover_authorization_server,
    discover_protected_resource,
    parse_www_authenticate,
)
from agentos.oauth.flow import OAuthFlowError, PendingAuthorization, begin_authorization, exchange_code_for_tokens, revoke_token
from agentos.oauth.netpolicy import OAuthUrlRefused, public_https
from agentos.oauth.registration import OAuthRegistrationError, OAuthRegistrationUnreachable, register_client
from agentos.oauth.token_store import OAuthTokenStore

from .models import McpAuthKind, McpServerConfig, McpServerState, McpTransport
from .oauth_records import McpOAuthClient, McpOAuthRecords
from .protocol import CLIENT_INFO, PROTOCOL_VERSION
from .service import McpServerNotFound, McpServiceError
from .token_source import LeasedTokenSource

_LOGGER = logging.getLogger(__name__)
PENDING_TTL = timedelta(minutes=10)
HTTP_TIMEOUT_SECONDS = 20.0
_SIGN_IN_STATES = frozenset({McpServerState.PENDING_APPROVAL.value, McpServerState.ERROR.value})


class McpOAuthError(RuntimeError):
    """Base for OAuth sign-in failures of an MCP server."""


class McpOAuthUnsupported(McpOAuthError):
    """The server does not offer what the MCP Authorization spec requires (metadata, registration, S256)."""


class McpOAuthUnreachable(McpOAuthError):
    """The MCP server or its authorization server did not answer."""


class McpOAuthCallbackError(McpOAuthError):
    """The browser came back with something Orin cannot turn into a sign-in; the message is user-facing."""


@dataclass(frozen=True, slots=True)
class SignInStart:
    authorization_url: str
    expires_at: datetime


def _http_client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False)


class McpOAuth:
    def __init__(self, engine: Engine, *, http_client_factory: Callable[[], httpx.Client] = _http_client) -> None:
        self._records = McpOAuthRecords(engine)
        self._tokens = OAuthTokenStore(engine)
        self._http_client_factory = http_client_factory

    def start(self, *, user_id: str, server_id: str, redirect_uri: str) -> SignInStart:
        row = self._records.server(user_id, server_id)
        if row is None:
            raise McpServerNotFound(f"no MCP server '{server_id}' for this user")
        if row["transport"] != McpTransport.HTTP.value or row["auth_kind"] != McpAuthKind.OAUTH.value:
            raise McpServiceError("only a remote server that asked for sign-in can start an OAuth sign-in")
        if row["state"] not in _SIGN_IN_STATES:
            raise McpServiceError("this server is not waiting for a sign-in")
        try:
            client = self._client_for(str(row["url"]), server_id=server_id, redirect_uri=redirect_uri)
        except McpOAuthError as error:
            self._records.set_server_reason(server_id, f"Não foi possível iniciar o login: {error}")
            raise
        pending = begin_authorization(client.provider_config(), redirect_uri=redirect_uri)
        expires_at = self._records.add_pending(
            state=pending.state, user_id=user_id, server_id=server_id, code_verifier=pending.code_verifier,
            redirect_uri=redirect_uri, ttl=PENDING_TTL,
        )
        # The screen polls the server and treats any reason as a failure, so a
        # reason left over from an earlier attempt must not survive a new one.
        self._records.set_server_reason(server_id, "")
        return SignInStart(authorization_url=pending.authorization_url, expires_at=expires_at)

    def complete(self, *, user_id: str, state: str, code: str, error: str) -> str:
        pending = self._records.take_pending(state, user_id=user_id) if state else None
        if pending is None:
            raise McpOAuthCallbackError("Este link de autorização não é válido ou já expirou. Tente conectar de novo pelo Orin.")
        if error or not code:
            reason = "Autorização recusada" if error == "access_denied" else f"O servidor de autorização devolveu um erro ({error or 'sem código'})"
            self._records.set_server_reason(pending.server_id, reason)
            raise McpOAuthCallbackError(reason)
        client = self._records.client(pending.server_id)
        if client is None:
            reason = "O registro do Orin neste servidor sumiu. Tente conectar de novo."
            self._records.set_server_reason(pending.server_id, reason)
            raise McpOAuthCallbackError(reason)
        authorization = PendingAuthorization(provider_id=pending.server_id, state=pending.state,
                                             code_verifier=pending.code_verifier, redirect_uri=pending.redirect_uri,
                                             authorization_url="")
        http = self._http_client_factory()
        try:
            tokens = exchange_code_for_tokens(client.provider_config(), authorization, code=code, client=http)
        except OAuthFlowError as failure:
            reason = f"Não foi possível concluir o login: {failure}"
            self._records.set_server_reason(pending.server_id, reason)
            raise McpOAuthCallbackError(reason) from failure
        finally:
            http.close()
        try:
            self._tokens.save(user_id=user_id, provider_id=pending.server_id, tokens=tokens)
        except ValueError as failure:  # the cipher refuses values over 4096 characters
            reason = "O servidor emitiu um token grande demais para ser guardado com segurança"
            self._records.set_server_reason(pending.server_id, reason)
            raise McpOAuthCallbackError(reason) from failure
        return pending.server_id

    def cancel(self, *, user_id: str, server_id: str) -> None:
        self._records.cancel_pending(user_id=user_id, server_id=server_id)

    def token_source(self, config: McpServerConfig) -> LeasedTokenSource:
        return LeasedTokenSource(store=self._tokens, records=self._records, user_id=config.user_id,
                                 server_id=config.server_id, display_name=config.display_name,
                                 http_client_factory=self._http_client_factory)

    def revoke(self, *, user_id: str, server_id: str) -> None:
        client = self._records.client(server_id)
        tokens = self._tokens.get(user_id=user_id, provider_id=server_id)
        if client is None or tokens is None or not client.revocation_endpoint:
            return
        token, hint = (tokens.refresh_token, "refresh_token") if tokens.refresh_token else (tokens.access_token, "access_token")
        http = self._http_client_factory()
        try:
            revoke_token(client.provider_config(), revocation_url=client.revocation_endpoint, token=token,
                         token_type_hint=hint, client=http)
        except Exception:  # revocation is courtesy; removal never waits on it
            _LOGGER.info("could not revoke the OAuth grant of MCP server %s", server_id)
        finally:
            http.close()

    def _client_for(self, url: str, *, server_id: str, redirect_uri: str) -> McpOAuthClient:
        http = self._http_client_factory()
        try:
            challenge = self._probe(http, url)
            try:
                resource = discover_protected_resource(url, challenge=challenge, client=http)
                server = discover_authorization_server(resource.authorization_servers[0], client=http)
            except OAuthDiscoveryUnreachable as error:
                raise McpOAuthUnreachable(str(error)) from error
            except OAuthDiscoveryError as error:
                raise McpOAuthUnsupported(str(error)) from error
            scope = challenge.scope or (" ".join(resource.scopes_supported) or None)
            existing = self._records.client(server_id)
            if existing is not None and existing.redirect_uri == redirect_uri and existing.issuer == server.issuer:
                return existing
            try:
                registration = register_client(server, redirect_uri=redirect_uri, scope=scope, client=http)
            except OAuthRegistrationUnreachable as error:
                raise McpOAuthUnreachable(str(error)) from error
            except OAuthRegistrationError as error:
                raise McpOAuthUnsupported(str(error)) from error
        finally:
            http.close()
        client = McpOAuthClient(
            server_id=server_id, issuer=server.issuer, authorization_endpoint=server.authorization_endpoint,
            token_endpoint=server.token_endpoint, revocation_endpoint=server.revocation_endpoint,
            resource=resource.resource, scope=scope, client_id=registration.client_id,
            client_secret=registration.client_secret, token_endpoint_auth_method=registration.token_endpoint_auth_method,
            redirect_uri=redirect_uri,
        )
        self._records.save_client(client)
        return client

    @staticmethod
    def _probe(http: httpx.Client, url: str) -> BearerChallenge:
        try:
            checked = public_https(url)
        except OAuthUrlRefused as error:
            raise McpOAuthUnsupported(str(error)) from error
        frame = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                 "params": {"protocolVersion": PROTOCOL_VERSION, "clientInfo": CLIENT_INFO, "capabilities": {}}}
        try:
            response = http.post(checked, json=frame, headers={"accept": "application/json, text/event-stream"})
        except httpx.HTTPError as error:
            raise McpOAuthUnreachable(f"o servidor não respondeu: {error}") from error
        if response.status_code != 401:
            return BearerChallenge(None, None, None)
        return parse_www_authenticate(response.headers.get("www-authenticate"))


__all__ = [
    "McpOAuth", "McpOAuthCallbackError", "McpOAuthError", "McpOAuthUnreachable", "McpOAuthUnsupported",
    "PENDING_TTL", "SignInStart",
]

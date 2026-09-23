"""Bearer tokens for an OAuth-signed MCP server, renewed without spending a refresh token twice.

The API (approve, test) and the worker (agent turns) are separate processes
over one SQLite file, and servers with refresh-token rotation revoke the whole
grant when a refresh token is replayed. A lease on the ``oauth_tokens`` row
lets exactly one process refresh; the others wait for the version to move and
read the new token. Tokens are read from the database on every call, so a
process always sees what another one wrote.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Callable, NoReturn, Protocol

import httpx

from agentos.oauth.flow import OAuthFlowError, OAuthGrantRejected, refresh_tokens
from agentos.oauth.token_store import OAuthTokenStore, StoredOAuthTokens

from .oauth_records import McpOAuthRecords

REFRESH_MARGIN = timedelta(seconds=60)
LEASE_TTL = timedelta(seconds=30)
WAIT_STEP_SECONDS = 0.25
WAIT_LIMIT_SECONDS = 10.0
HTTP_TIMEOUT_SECONDS = 20.0


class McpReauthRequired(RuntimeError):
    """The server no longer accepts this sign-in; the user has to sign in again."""


class McpAuthUnavailable(RuntimeError):
    """A refresh could not complete right now (network, server error, or another process still refreshing)."""


class TokenSource(Protocol):
    def current(self) -> str: ...
    def force_refresh(self, rejected: str) -> str: ...
    def invalidate(self, reason: str) -> NoReturn: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _http_client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False)


class LeasedTokenSource:
    def __init__(self, *, store: OAuthTokenStore, records: McpOAuthRecords, user_id: str, server_id: str,
                 display_name: str, http_client_factory: Callable[[], httpx.Client] = _http_client,
                 clock: Callable[[], datetime] = _now, sleep: Callable[[float], None] = time.sleep) -> None:
        self._store = store
        self._records = records
        self._user_id = user_id
        self._server_id = server_id
        self._display_name = display_name
        self._http_client_factory = http_client_factory
        self._clock = clock
        self._sleep = sleep

    def current(self) -> str:
        tokens = self._load()
        if tokens.expires_at is None or tokens.expires_at - self._clock() > REFRESH_MARGIN:
            return tokens.access_token
        return self._refresh(tokens)

    def force_refresh(self, rejected: str) -> str:
        tokens = self._load()
        if tokens.access_token != rejected:
            return tokens.access_token
        return self._refresh(tokens)

    def invalidate(self, reason: str) -> NoReturn:
        self._store.delete(user_id=self._user_id, provider_id=self._server_id)
        self._records.mark_reauth_required(self._server_id, reason)
        raise McpReauthRequired(f"O acesso a {self._display_name} expirou. Reconecte em Configurações → MCP.")

    def _load(self) -> StoredOAuthTokens:
        tokens = self._store.get(user_id=self._user_id, provider_id=self._server_id)
        if tokens is None:
            self.invalidate("Reconexão necessária: não há login salvo")
        return tokens

    def _refresh(self, tokens: StoredOAuthTokens) -> str:
        if not tokens.refresh_token:
            self.invalidate("Reconexão necessária: o acesso venceu e o servidor não permite renovar")
        client = self._records.client(self._server_id)
        if client is None:
            self.invalidate("Reconexão necessária: o registro do Orin no servidor sumiu")
        if not self._store.try_acquire_refresh_lease(
            user_id=self._user_id, provider_id=self._server_id, version=tokens.version, ttl=LEASE_TTL,
        ):
            return self._wait_for_other_refresh(tokens.version)
        http = self._http_client_factory()
        try:
            fresh = refresh_tokens(client.provider_config(), tokens.refresh_token, client=http)
        except OAuthGrantRejected:
            self.invalidate("Reconexão necessária: o servidor recusou a renovação do acesso")
        except OAuthFlowError as error:
            self._store.release_refresh_lease(user_id=self._user_id, provider_id=self._server_id)
            raise McpAuthUnavailable(f"não foi possível renovar o acesso: {error}") from error
        finally:
            http.close()
        self._store.save(user_id=self._user_id, provider_id=self._server_id, tokens=fresh)
        return fresh.access_token

    def _wait_for_other_refresh(self, version: int) -> str:
        waited = 0.0
        while waited < WAIT_LIMIT_SECONDS:
            self._sleep(WAIT_STEP_SECONDS)
            waited += WAIT_STEP_SECONDS
            latest = self._store.get(user_id=self._user_id, provider_id=self._server_id)
            if latest is None:
                self.invalidate("Reconexão necessária: não há login salvo")
            if latest.version != version:
                return latest.access_token
        raise McpAuthUnavailable("outra renovação do acesso não terminou a tempo")


__all__ = [
    "LEASE_TTL", "LeasedTokenSource", "McpAuthUnavailable", "McpReauthRequired", "REFRESH_MARGIN", "TokenSource",
    "WAIT_LIMIT_SECONDS", "WAIT_STEP_SECONDS",
]

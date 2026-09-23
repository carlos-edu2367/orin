"""SQL for the OAuth side of an MCP server: its client registration and pending sign-ins.

Kept apart from ``auth`` so the flow rules read without SQL, and apart from
``service`` so the server lifecycle does not grow a second responsibility.
Secrets (client secret, PKCE verifier) are stored through the same
``ProviderSecretCipher`` that protects every other credential.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from agentos.oauth.flow import OAuthProviderConfig
from agentos.persistence.postgres.schema import mcp_oauth_clients, mcp_servers, oauth_pending_authorizations
from agentos.persistence.provider_secrets import ProviderSecretCipher

from .models import McpServerState

MAX_STATE_REASON_CHARS = 512


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _cipher() -> ProviderSecretCipher:
    return ProviderSecretCipher.from_environment(required=True)


@dataclass(frozen=True, slots=True)
class McpOAuthClient:
    server_id: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None
    resource: str
    scope: str | None
    client_id: str
    client_secret: str | None
    token_endpoint_auth_method: str
    redirect_uri: str

    def provider_config(self) -> OAuthProviderConfig:
        return OAuthProviderConfig(
            provider_id=self.server_id, authorize_url=self.authorization_endpoint, token_url=self.token_endpoint,
            scopes=tuple(self.scope.split()) if self.scope else (), client_id=self.client_id, resource=self.resource,
            client_secret=self.client_secret, token_endpoint_auth_method=self.token_endpoint_auth_method,
        )


@dataclass(frozen=True, slots=True)
class PendingSignIn:
    state: str
    user_id: str
    server_id: str
    code_verifier: str
    redirect_uri: str


class McpOAuthRecords:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def server(self, user_id: str, server_id: str) -> Mapping[str, Any] | None:
        with self._engine.connect() as connection:
            return connection.execute(
                select(mcp_servers).where(mcp_servers.c.server_id == server_id, mcp_servers.c.user_id == user_id)
            ).mappings().first()

    def client(self, server_id: str) -> McpOAuthClient | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(mcp_oauth_clients).where(mcp_oauth_clients.c.server_id == server_id)
            ).mappings().first()
        if row is None:
            return None
        secret = row["client_secret_ciphertext"]
        return McpOAuthClient(
            server_id=str(row["server_id"]), issuer=str(row["issuer"]),
            authorization_endpoint=str(row["authorization_endpoint"]), token_endpoint=str(row["token_endpoint"]),
            revocation_endpoint=row["revocation_endpoint"], resource=str(row["resource"]), scope=row["scope"],
            client_id=str(row["client_id"]), client_secret=_cipher().decrypt(secret) if secret else None,
            token_endpoint_auth_method=str(row["token_endpoint_auth_method"]), redirect_uri=str(row["redirect_uri"]),
        )

    def save_client(self, client: McpOAuthClient) -> None:
        now = _now()
        with self._engine.begin() as connection:
            connection.execute(delete(mcp_oauth_clients).where(mcp_oauth_clients.c.server_id == client.server_id))
            connection.execute(insert(mcp_oauth_clients).values(
                server_id=client.server_id, issuer=client.issuer, authorization_endpoint=client.authorization_endpoint,
                token_endpoint=client.token_endpoint, revocation_endpoint=client.revocation_endpoint,
                resource=client.resource, scope=client.scope, client_id=client.client_id,
                client_secret_ciphertext=_cipher().encrypt(client.client_secret) if client.client_secret else None,
                token_endpoint_auth_method=client.token_endpoint_auth_method, redirect_uri=client.redirect_uri,
                created_at=now, updated_at=now,
            ))

    def add_pending(self, *, state: str, user_id: str, server_id: str, code_verifier: str, redirect_uri: str,
                    ttl: timedelta) -> datetime:
        now = _now()
        expires_at = now + ttl
        with self._engine.begin() as connection:
            connection.execute(delete(oauth_pending_authorizations).where(oauth_pending_authorizations.c.expires_at < now))
            connection.execute(insert(oauth_pending_authorizations).values(
                state=state, user_id=user_id, server_id=server_id,
                code_verifier_ciphertext=_cipher().encrypt(code_verifier), redirect_uri=redirect_uri,
                expires_at=expires_at, created_at=now,
            ))
        return expires_at

    def take_pending(self, state: str, *, user_id: str) -> PendingSignIn | None:
        """Consume a pending sign-in: it is deleted whether or not it is still valid."""
        with self._engine.begin() as connection:
            row = connection.execute(
                select(oauth_pending_authorizations).where(
                    oauth_pending_authorizations.c.state == state, oauth_pending_authorizations.c.user_id == user_id,
                )
            ).mappings().first()
            if row is None:
                return None
            connection.execute(delete(oauth_pending_authorizations).where(oauth_pending_authorizations.c.state == state))
        if _aware(row["expires_at"]) <= _now():
            return None
        return PendingSignIn(
            state=str(row["state"]), user_id=str(row["user_id"]), server_id=str(row["server_id"]),
            code_verifier=_cipher().decrypt(str(row["code_verifier_ciphertext"])), redirect_uri=str(row["redirect_uri"]),
        )

    def cancel_pending(self, *, user_id: str, server_id: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(delete(oauth_pending_authorizations).where(
                oauth_pending_authorizations.c.user_id == user_id, oauth_pending_authorizations.c.server_id == server_id,
            ))

    def set_server_reason(self, server_id: str, reason: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(update(mcp_servers).where(mcp_servers.c.server_id == server_id)
                               .values(state_reason=reason[:MAX_STATE_REASON_CHARS], updated_at=_now()))

    def mark_reauth_required(self, server_id: str, reason: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(update(mcp_servers).where(mcp_servers.c.server_id == server_id).values(
                state=McpServerState.ERROR.value, state_reason=reason[:MAX_STATE_REASON_CHARS], updated_at=_now(),
            ))


__all__ = ["McpOAuthClient", "McpOAuthRecords", "PendingSignIn"]

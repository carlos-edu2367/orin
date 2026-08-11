"""Durable production SecurityService backed by PostgreSQL.

Mirrors ``agentos.api.security.InMemorySecurityService`` semantics exactly
(PATs stored as digests, server-side sessions, monotonic revocation epochs,
sliding-window rate limiting) but persists them so a restart or a second
process observes the same state. There is no HTTP login/PAT-issuance route
in ``agentos.api.gateway`` today (confirmed against ``docs/frontend/
BACKEND_DISCOVERY.md``), so provisioning stays a Python-level operational
API — the same shape ``InMemorySecurityService`` already exposes — rather
than an invented public endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from time import monotonic

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from agentos.api.security import (
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthorizationError,
    RateLimitError,
)

from .schema import security_pats, security_rate_limit_hits, security_revocations, security_sessions


class PostgresSecurityService:
    """Production adapter for the ``SecurityService`` port (RFC frontend Fase 0)."""

    def __init__(self, engine: Engine, *, maximum_requests: int = 120, window_seconds: int = 60) -> None:
        self._engine = engine
        self._maximum_requests = maximum_requests
        self._window_seconds = window_seconds

    def add_pat(self, token: str, principal: AuthenticatedPrincipal) -> None:
        with self._engine.begin() as connection:
            connection.execute(delete(security_pats).where(security_pats.c.credential_ref == principal.credential_ref))
            connection.execute(insert(security_pats).values(
                credential_ref=principal.credential_ref,
                user_id=principal.user_id,
                token_digest=self._digest(token),
                scopes=sorted(principal.scopes),
                revoked=principal.revoked,
                created_at=datetime.now(UTC),
            ))

    def add_session(self, session_id: str, principal: AuthenticatedPrincipal, *, csrf_token: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(delete(security_sessions).where(security_sessions.c.session_id == session_id))
            connection.execute(insert(security_sessions).values(
                session_id=session_id,
                user_id=principal.user_id,
                credential_ref=principal.credential_ref,
                csrf_digest=self._digest(csrf_token),
                scopes=sorted(principal.scopes),
                revoked=False,
                created_at=datetime.now(UTC),
            ))

    def revoke(self, credential_ref: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(update(security_pats).where(security_pats.c.credential_ref == credential_ref).values(revoked=True))
            connection.execute(update(security_sessions).where(security_sessions.c.credential_ref == credential_ref).values(revoked=True))
            existing = connection.execute(select(security_revocations.c.epoch).where(security_revocations.c.credential_ref == credential_ref)).scalar_one_or_none()
            if existing is None:
                connection.execute(insert(security_revocations).values(credential_ref=credential_ref, epoch=1))
            else:
                connection.execute(update(security_revocations).where(security_revocations.c.credential_ref == credential_ref).values(epoch=existing + 1))

    def authenticate(self, *, bearer_token: str | None, session_id: str | None) -> AuthenticatedPrincipal:
        with self._engine.connect() as connection:
            if bearer_token:
                row = connection.execute(select(security_pats).where(security_pats.c.token_digest == self._digest(bearer_token))).mappings().first()
                if row is None or row["revoked"]:
                    raise AuthenticationError("credential is invalid")
                return AuthenticatedPrincipal(row["user_id"], row["credential_ref"], frozenset(row["scopes"]), "pat", False)
            if session_id:
                row = connection.execute(select(security_sessions).where(security_sessions.c.session_id == session_id)).mappings().first()
                if row is None or row["revoked"]:
                    raise AuthenticationError("session is invalid")
                pat_revoked = connection.execute(select(security_pats.c.revoked).where(security_pats.c.credential_ref == row["credential_ref"])).scalar_one_or_none()
                if pat_revoked:
                    raise AuthenticationError("session is invalid")
                return AuthenticatedPrincipal(
                    row["user_id"], row["credential_ref"], frozenset(row["scopes"]),
                    "session", False, session_id=session_id,
                )
        raise AuthenticationError("credential is required")

    def validate_csrf(self, principal: AuthenticatedPrincipal, token: str | None, origin: str | None) -> None:
        if principal.credential_kind != "session":
            return
        if not token or not origin:
            raise AuthorizationError("csrf validation failed")
        with self._engine.connect() as connection:
            stored = connection.execute(select(security_sessions.c.csrf_digest).where(
                security_sessions.c.session_id == principal.session_id,
                security_sessions.c.user_id == principal.user_id,
                security_sessions.c.credential_ref == principal.credential_ref,
                security_sessions.c.revoked.is_(False),
            )).scalar_one_or_none()
        if stored is not None and compare_digest(stored, self._digest(token)):
            return
        raise AuthorizationError("csrf validation failed")

    def authorize(self, principal: AuthenticatedPrincipal, *, action: str, resource_id: str | None, purpose: str) -> None:
        if principal.revoked or "api" not in principal.scopes or not purpose.strip():
            raise AuthorizationError("action is not authorized")

    def check_rate_limit(self, principal: AuthenticatedPrincipal, *, action: str, origin: str | None) -> None:
        now = datetime.now(UTC)
        window_start = now - timedelta(seconds=self._window_seconds)
        with self._engine.begin() as connection:
            connection.execute(delete(security_rate_limit_hits).where(
                security_rate_limit_hits.c.credential_ref == principal.credential_ref,
                security_rate_limit_hits.c.action == action,
                security_rate_limit_hits.c.occurred_at < window_start,
            ))
            count = len(connection.execute(select(security_rate_limit_hits.c.id).where(
                security_rate_limit_hits.c.credential_ref == principal.credential_ref,
                security_rate_limit_hits.c.action == action,
            )).all())
            if count >= self._maximum_requests:
                raise RateLimitError("rate limit exceeded")
            connection.execute(insert(security_rate_limit_hits).values(credential_ref=principal.credential_ref, action=action, occurred_at=now))

    def revocation_epoch(self, principal: AuthenticatedPrincipal) -> int:
        with self._engine.connect() as connection:
            epoch = connection.execute(select(security_revocations.c.epoch).where(security_revocations.c.credential_ref == principal.credential_ref)).scalar_one_or_none()
        return epoch or 0

    @staticmethod
    def _digest(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()


__all__ = ["PostgresSecurityService"]

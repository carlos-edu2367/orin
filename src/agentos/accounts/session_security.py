"""Browser sessions for server mode on top of the durable security adapter.

Differences from ``PostgresSecurityService``: sessions expire (30 days,
sliding), the CSRF token is derived from the session id so every tab gets the
same one from ``/v1/auth/me``, the request Origin must be the public URL, and
scopes come from the live account (role, active, temporary password) on every
request rather than from whatever was stored when the session opened.
"""
from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from typing import Callable

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from agentos.api.security import (
    AdminRequiredError, AuthenticatedPrincipal, AuthenticationError, AuthorizationError, PasswordChangeRequiredError,
)
from agentos.persistence.postgres.schema import security_sessions
from agentos.persistence.postgres.security import PostgresSecurityService

from .clock import aware, utcnow
from .store import UserRecord, UserStore

SESSION_COOKIE = "agentos_session"
SESSION_TTL = timedelta(days=30)
TOUCH_INTERVAL = timedelta(minutes=5)


def derive_csrf_secret(encryption_key: str) -> bytes:
    return sha256(b"orin-session-csrf:" + encryption_key.encode("utf-8")).digest()


def revoke_user_sessions(engine: Engine, user_id: str, *, keep_session_id: str | None = None) -> None:
    condition = security_sessions.c.user_id == user_id
    if keep_session_id is not None:
        condition = condition & (security_sessions.c.session_id != keep_session_id)
    with engine.begin() as connection:
        connection.execute(update(security_sessions).where(condition).values(revoked=True))


class SessionSecurityService(PostgresSecurityService):
    requires_loopback_client = False

    def __init__(self, engine: Engine, *, users: UserStore, public_origin: str, csrf_secret: bytes, clock: Callable[[], datetime] = utcnow, maximum_requests: int = 120) -> None:
        super().__init__(engine, maximum_requests=maximum_requests)
        self._users = users
        self.public_origin = public_origin
        self._csrf_secret = csrf_secret
        self._clock = clock

    def csrf_for(self, session_id: str) -> str:
        return hmac.new(self._csrf_secret, session_id.encode("utf-8"), sha256).hexdigest()

    def open_session(self, user: UserRecord) -> tuple[str, str]:
        session_id = secrets.token_urlsafe(32)
        csrf = self.csrf_for(session_id)
        now = self._clock()
        with self._engine.begin() as connection:
            connection.execute(insert(security_sessions).values(
                session_id=session_id, user_id=user.user_id, credential_ref=f"session:{session_id}",
                csrf_digest=self._digest(csrf), scopes=["api"], revoked=False,
                created_at=now, expires_at=now + SESSION_TTL, last_seen_at=now,
            ))
        return session_id, csrf

    def close_session(self, session_id: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(update(security_sessions).where(security_sessions.c.session_id == session_id).values(revoked=True))

    def revoke_user(self, user_id: str, *, keep_session_id: str | None = None) -> None:
        revoke_user_sessions(self._engine, user_id, keep_session_id=keep_session_id)

    def authenticate(self, *, bearer_token: str | None, session_id: str | None) -> AuthenticatedPrincipal:
        if bearer_token:
            return super().authenticate(bearer_token=bearer_token, session_id=None)
        if not session_id:
            raise AuthenticationError("credential is required")
        now = self._clock()
        with self._engine.connect() as connection:
            row = connection.execute(select(security_sessions).where(security_sessions.c.session_id == session_id)).mappings().first()
        if row is None or row["revoked"] or row["expires_at"] is None or aware(row["expires_at"]) <= now:
            raise AuthenticationError("session is invalid")
        user = self._users.get(str(row["user_id"]))
        if user is None or not user.active:
            raise AuthenticationError("session is invalid")
        last_seen = aware(row["last_seen_at"]) if row["last_seen_at"] is not None else None
        if last_seen is None or now - last_seen >= TOUCH_INTERVAL:
            with self._engine.begin() as connection:
                connection.execute(update(security_sessions).where(security_sessions.c.session_id == session_id).values(
                    last_seen_at=now, expires_at=now + SESSION_TTL,
                ))
        if user.must_change_password:
            scopes = frozenset({"password_change"})
        else:
            scopes = frozenset({"api", "admin"} if user.is_admin else {"api"})
        return AuthenticatedPrincipal(user.user_id, str(row["credential_ref"]), scopes, "session", False, session_id=session_id)

    def validate_csrf(self, principal: AuthenticatedPrincipal, token: str | None, origin: str | None) -> None:
        if principal.credential_kind != "session":
            return
        if origin != self.public_origin or not token or not compare_digest(token, self.csrf_for(principal.session_id or "")):
            raise AuthorizationError("csrf validation failed")

    def authorize(self, principal: AuthenticatedPrincipal, *, action: str, resource_id: str | None, purpose: str) -> None:
        if "password_change" in principal.scopes and "api" not in principal.scopes:
            raise PasswordChangeRequiredError("password change required")
        if action.startswith("admin.") and "admin" not in principal.scopes:
            raise AdminRequiredError("admin required")
        super().authorize(principal, action=action, resource_id=resource_id, purpose=purpose)


__all__ = ["SESSION_COOKIE", "SESSION_TTL", "SessionSecurityService", "TOUCH_INTERVAL", "derive_csrf_secret", "revoke_user_sessions"]

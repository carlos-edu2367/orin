from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from agentos.api.security import (
    AuthenticatedPrincipal,
    AuthorizationError,
    InMemorySecurityService,
)
from agentos.persistence.postgres.schema import security_pats, security_sessions
from agentos.persistence.postgres.security import PostgresSecurityService


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal("user-1", "credential-shared", frozenset({"api"}), "session")


def test_in_memory_csrf_token_is_bound_to_the_authenticated_session() -> None:
    service = InMemorySecurityService()
    service.add_session("session-1", _principal(), csrf_token="csrf-1")
    service.add_session("session-2", _principal(), csrf_token="csrf-2")

    authenticated = service.authenticate(bearer_token=None, session_id="session-1")

    assert "session-1" not in repr(authenticated)
    service.validate_csrf(authenticated, "csrf-1", "https://app.example")
    with pytest.raises(AuthorizationError):
        service.validate_csrf(authenticated, "csrf-2", "https://app.example")


def test_postgres_csrf_token_is_bound_to_the_authenticated_session() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    security_pats.create(engine)
    security_sessions.create(engine)
    service = PostgresSecurityService(engine)
    service.add_session("session-1", _principal(), csrf_token="csrf-1")
    service.add_session("session-2", _principal(), csrf_token="csrf-2")

    authenticated = service.authenticate(bearer_token=None, session_id="session-1")

    service.validate_csrf(authenticated, "csrf-1", "https://app.example")
    with pytest.raises(AuthorizationError):
        service.validate_csrf(authenticated, "csrf-2", "https://app.example")

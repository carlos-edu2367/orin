from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from agentos.api.security import AuthenticatedPrincipal, AuthenticationError, AuthorizationError, RateLimitError
from agentos.persistence.postgres import upgrade
from agentos.persistence.postgres.security import PostgresSecurityService


pytestmark = pytest.mark.skipif(not os.getenv("AGENTOS_TEST_POSTGRES_DSN"), reason="AGENTOS_TEST_POSTGRES_DSN is not configured")


def _engine():
    engine = create_engine(os.environ["AGENTOS_TEST_POSTGRES_DSN"], future=True)
    upgrade(engine)
    return engine


def test_pat_round_trips_and_survives_a_second_service_instance() -> None:
    engine = _engine()
    suffix = uuid4().hex
    principal = AuthenticatedPrincipal(f"user:{suffix}", f"cred:{suffix}", frozenset({"api"}), "pat")
    PostgresSecurityService(engine).add_pat(f"token:{suffix}", principal)

    authenticated = PostgresSecurityService(engine).authenticate(bearer_token=f"token:{suffix}", session_id=None)

    assert authenticated.user_id == principal.user_id
    assert authenticated.credential_ref == principal.credential_ref
    assert authenticated.scopes == frozenset({"api"})


def test_unknown_bearer_token_is_rejected() -> None:
    engine = _engine()
    with pytest.raises(AuthenticationError):
        PostgresSecurityService(engine).authenticate(bearer_token=f"nonexistent:{uuid4().hex}", session_id=None)


def test_session_requires_matching_csrf_digest_and_origin() -> None:
    engine = _engine()
    suffix = uuid4().hex
    principal = AuthenticatedPrincipal(f"user:{suffix}", f"cred:{suffix}", frozenset({"api"}), "session")
    service = PostgresSecurityService(engine)
    service.add_session(f"sess:{suffix}", principal, csrf_token="csrf-secret")
    authenticated = service.authenticate(bearer_token=None, session_id=f"sess:{suffix}")

    service.validate_csrf(authenticated, "csrf-secret", "https://app.example")

    with pytest.raises(AuthorizationError):
        service.validate_csrf(authenticated, "wrong-token", "https://app.example")


def test_revoke_invalidates_credential_and_bumps_epoch_durably() -> None:
    engine = _engine()
    suffix = uuid4().hex
    principal = AuthenticatedPrincipal(f"user:{suffix}", f"cred:{suffix}", frozenset({"api"}), "pat")
    service = PostgresSecurityService(engine)
    service.add_pat(f"token:{suffix}", principal)
    baseline_epoch = service.revocation_epoch(principal)

    service.revoke(principal.credential_ref)

    with pytest.raises(AuthenticationError):
        service.authenticate(bearer_token=f"token:{suffix}", session_id=None)
    assert PostgresSecurityService(engine).revocation_epoch(principal) == baseline_epoch + 1


def test_rate_limit_is_enforced_across_service_instances() -> None:
    engine = _engine()
    suffix = uuid4().hex
    principal = AuthenticatedPrincipal(f"user:{suffix}", f"cred:{suffix}", frozenset({"api"}), "pat")
    service = PostgresSecurityService(engine, maximum_requests=2)
    service.check_rate_limit(principal, action="execution.create", origin=None)
    PostgresSecurityService(engine, maximum_requests=2).check_rate_limit(principal, action="execution.create", origin=None)

    with pytest.raises(RateLimitError):
        PostgresSecurityService(engine, maximum_requests=2).check_rate_limit(principal, action="execution.create", origin=None)


def test_authorize_rejects_credential_without_api_scope() -> None:
    engine = _engine()
    principal = AuthenticatedPrincipal(f"user:{uuid4().hex}", f"cred:{uuid4().hex}", frozenset(), "pat")
    with pytest.raises(AuthorizationError):
        PostgresSecurityService(engine).authorize(principal, action="execution.create", resource_id=None, purpose="execution.create")

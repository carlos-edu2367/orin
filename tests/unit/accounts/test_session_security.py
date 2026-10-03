from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select

from agentos.accounts.session_security import SessionSecurityService, derive_csrf_secret
from agentos.accounts.store import UserStore
from agentos.api.security import (
    AdminRequiredError, AuthenticatedPrincipal, AuthenticationError, AuthorizationError, PasswordChangeRequiredError,
)
from agentos.persistence.postgres.schema import metadata, security_sessions

ORIGIN = "https://orin.example.com"


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def world(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    users = UserStore(engine)
    clock = Clock()
    sessions = SessionSecurityService(engine, users=users, public_origin=ORIGIN, csrf_secret=derive_csrf_secret("k" * 44), clock=clock)
    admin = users.create(username="carla", password="a long password", role="admin")
    member = users.create(username="bruno", password="a long password")
    return engine, users, sessions, clock, admin, member


def test_a_session_authenticates_with_role_scopes(world):
    _, _, sessions, _, admin, member = world
    admin_sid, _ = sessions.open_session(admin)
    member_sid, _ = sessions.open_session(member)
    admin_principal = sessions.authenticate(bearer_token=None, session_id=admin_sid)
    member_principal = sessions.authenticate(bearer_token=None, session_id=member_sid)
    assert admin_principal.user_id == "local-user"
    assert admin_principal.credential_kind == "session"
    assert admin_principal.scopes == frozenset({"api", "admin"})
    assert member_principal.scopes == frozenset({"api"})
    assert admin_principal.credential_ref == f"session:{admin_sid}"


def test_the_csrf_token_is_stable_and_bound_to_the_session(world):
    _, _, sessions, _, admin, _ = world
    sid, csrf = sessions.open_session(admin)
    other_sid, other_csrf = sessions.open_session(admin)
    assert sessions.csrf_for(sid) == csrf == sessions.csrf_for(sid)
    assert csrf != other_csrf
    principal = sessions.authenticate(bearer_token=None, session_id=sid)
    sessions.validate_csrf(principal, csrf, ORIGIN)
    for token, origin in ((other_csrf, ORIGIN), (csrf, "https://evil.example"), (csrf, None), (None, ORIGIN)):
        with pytest.raises(AuthorizationError):
            sessions.validate_csrf(principal, token, origin)


def test_unknown_or_closed_sessions_are_rejected(world):
    _, _, sessions, _, admin, _ = world
    with pytest.raises(AuthenticationError):
        sessions.authenticate(bearer_token=None, session_id="nope")
    with pytest.raises(AuthenticationError):
        sessions.authenticate(bearer_token=None, session_id=None)
    sid, _ = sessions.open_session(admin)
    sessions.close_session(sid)
    with pytest.raises(AuthenticationError):
        sessions.authenticate(bearer_token=None, session_id=sid)


def test_sessions_slide_and_expire_after_thirty_idle_days(world):
    _, _, sessions, clock, admin, _ = world
    sid, _ = sessions.open_session(admin)
    clock.now += timedelta(days=29)
    sessions.authenticate(bearer_token=None, session_id=sid)
    clock.now += timedelta(days=29)
    sessions.authenticate(bearer_token=None, session_id=sid)
    clock.now += timedelta(days=30, seconds=1)
    with pytest.raises(AuthenticationError):
        sessions.authenticate(bearer_token=None, session_id=sid)


def test_last_seen_is_written_at_most_every_five_minutes(world):
    engine, _, sessions, clock, admin, _ = world
    sid, _ = sessions.open_session(admin)

    def last_seen():
        with engine.connect() as connection:
            return connection.execute(select(security_sessions.c.last_seen_at).where(security_sessions.c.session_id == sid)).scalar_one()

    first = last_seen()
    clock.now += timedelta(minutes=4)
    sessions.authenticate(bearer_token=None, session_id=sid)
    assert last_seen() == first
    clock.now += timedelta(minutes=2)
    sessions.authenticate(bearer_token=None, session_id=sid)
    assert last_seen() != first


def test_a_deactivated_user_loses_every_session(world):
    _, users, sessions, _, _, member = world
    sid, _ = sessions.open_session(member)
    users.update(member.user_id, active=False)
    with pytest.raises(AuthenticationError):
        sessions.authenticate(bearer_token=None, session_id=sid)


def test_a_temporary_password_only_grants_the_password_change_scope(world):
    _, users, sessions, _, _, member = world
    users.set_password(member.user_id, "temporary-123", temporary=True)
    sid, _ = sessions.open_session(users.get(member.user_id))
    principal = sessions.authenticate(bearer_token=None, session_id=sid)
    assert principal.scopes == frozenset({"password_change"})
    with pytest.raises(PasswordChangeRequiredError):
        sessions.authorize(principal, action="conversation.read", resource_id=None, purpose="conversation.read")


def test_admin_actions_require_the_admin_scope(world):
    _, _, sessions, _, admin, member = world
    member_principal = sessions.authenticate(bearer_token=None, session_id=sessions.open_session(member)[0])
    admin_principal = sessions.authenticate(bearer_token=None, session_id=sessions.open_session(admin)[0])
    with pytest.raises(AdminRequiredError):
        sessions.authorize(member_principal, action="admin.users", resource_id=None, purpose="admin.users.list")
    sessions.authorize(admin_principal, action="admin.users", resource_id=None, purpose="admin.users.list")
    sessions.authorize(member_principal, action="conversation.read", resource_id=None, purpose="conversation.read")


def test_revoke_user_can_keep_the_current_session(world):
    _, _, sessions, _, admin, _ = world
    keep, _ = sessions.open_session(admin)
    drop, _ = sessions.open_session(admin)
    sessions.revoke_user(admin.user_id, keep_session_id=keep)
    sessions.authenticate(bearer_token=None, session_id=keep)
    with pytest.raises(AuthenticationError):
        sessions.authenticate(bearer_token=None, session_id=drop)


def test_personal_access_tokens_still_work(world):
    _, _, sessions, _, _, _ = world
    sessions.add_pat("pat-token", AuthenticatedPrincipal("local-user", "pat-1", frozenset({"api"})))
    principal = sessions.authenticate(bearer_token="pat-token", session_id=None)
    assert principal.credential_kind == "pat"
    sessions.validate_csrf(principal, None, None)

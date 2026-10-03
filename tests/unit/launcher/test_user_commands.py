import pytest
from sqlalchemy import create_engine

from agentos.accounts.errors import UserNotFound
from agentos.accounts.passwords import verify_password
from agentos.accounts.session_security import SessionSecurityService, derive_csrf_secret
from agentos.accounts.store import UserStore
from agentos.api.security import AuthenticationError
from agentos.launcher.users import create_user, reset_password
from agentos.persistence.postgres.migrate import upgrade


@pytest.fixture()
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'orin.db'}")
    upgrade(engine)
    return engine


def test_create_user_from_the_terminal(engine):
    admin = create_user(engine, username="carla", password="a long password", admin=True)
    assert (admin.user_id, admin.role) == ("local-user", "admin")


def test_reset_password_returns_a_temporary_one_and_ends_sessions(engine):
    users = UserStore(engine)
    admin = create_user(engine, username="carla", password="a long password", admin=True)
    sessions = SessionSecurityService(engine, users=users, public_origin="https://o.test", csrf_secret=derive_csrf_secret("k"))
    sid, _ = sessions.open_session(admin)
    temporary = reset_password(engine, username="carla")
    _, encoded = users.find_for_login("carla")
    assert verify_password(temporary, encoded)
    assert users.get(admin.user_id).must_change_password is True
    with pytest.raises(AuthenticationError):
        sessions.authenticate(bearer_token=None, session_id=sid)


def test_reset_password_for_an_unknown_user(engine):
    with pytest.raises(UserNotFound):
        reset_password(engine, username="ghost")

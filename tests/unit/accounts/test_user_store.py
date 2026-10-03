import pytest
from sqlalchemy import create_engine

from agentos.accounts.errors import InvalidUsername, LastAdmin, UserNotFound, UsernameTaken, WeakPassword
from agentos.accounts.passwords import verify_password
from agentos.accounts.store import FIRST_USER_ID, UserStore, normalize_username
from agentos.persistence.postgres.schema import metadata


@pytest.fixture()
def store(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orin.db'}")
    metadata.create_all(engine)
    return UserStore(engine)


def test_first_user_inherits_the_local_profile_id(store):
    admin = store.create(username="Carla", password="a long password", role="admin")
    member = store.create(username="bruno", password="another password")
    assert admin.user_id == FIRST_USER_ID
    assert admin.username == "carla"
    assert member.user_id.startswith("usr_") and len(member.user_id) == 36
    assert store.count() == 2


@pytest.mark.parametrize("raw", ["ab", "-carla", "carla smith", "c" * 65, "çarla", ""])
def test_invalid_usernames_are_rejected(raw):
    with pytest.raises(InvalidUsername):
        normalize_username(raw)


def test_usernames_are_unique_case_insensitively(store):
    store.create(username="carla", password="a long password", role="admin")
    with pytest.raises(UsernameTaken):
        store.create(username="CARLA", password="a long password")


def test_weak_password_is_refused_on_create(store):
    with pytest.raises(WeakPassword):
        store.create(username="carla", password="short")


def test_find_for_login_returns_the_hash(store):
    store.create(username="carla", password="a long password", role="admin")
    record, encoded = store.find_for_login(" Carla ")
    assert record.username == "carla"
    assert verify_password("a long password", encoded)
    assert store.find_for_login("nobody") is None
    assert store.find_for_login("x y") is None


def test_public_view_never_contains_the_hash(store):
    record = store.create(username="carla", password="a long password", role="admin")
    public = record.public()
    assert set(public) == {"user_id", "username", "display_name", "role", "active", "must_change_password", "created_at", "updated_at", "password_changed_at"}
    assert public["display_name"] == "carla"


def test_temporary_password_sets_and_clears_the_flag(store):
    admin = store.create(username="carla", password="a long password", role="admin")
    reset = store.set_password(admin.user_id, "temporary-123", temporary=True)
    assert reset.must_change_password is True
    changed = store.set_password(admin.user_id, "my new password", temporary=False)
    assert changed.must_change_password is False
    _, encoded = store.find_for_login("carla")
    assert verify_password("my new password", encoded)


def test_cannot_remove_the_last_active_admin(store):
    admin = store.create(username="carla", password="a long password", role="admin")
    with pytest.raises(LastAdmin):
        store.update(admin.user_id, role="member")
    with pytest.raises(LastAdmin):
        store.update(admin.user_id, active=False)
    second = store.create(username="bruno", password="a long password", role="admin")
    store.update(admin.user_id, active=False)
    assert store.is_active(admin.user_id) is False
    with pytest.raises(LastAdmin):
        store.update(second.user_id, role="member")


def test_update_unknown_user(store):
    with pytest.raises(UserNotFound):
        store.update("usr_missing", display_name="x")
    assert store.is_active("usr_missing") is False
    assert store.get("usr_missing") is None


def test_list_keeps_creation_order(store):
    store.create(username="carla", password="a long password", role="admin")
    store.create(username="bruno", password="a long password")
    assert [item.username for item in store.list()] == ["carla", "bruno"]

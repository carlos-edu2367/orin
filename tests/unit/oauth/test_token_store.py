from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine

from agentos.oauth.flow import OAuthTokens
from agentos.oauth.token_store import OAuthTokenStore
from agentos.persistence.postgres.schema import metadata


@pytest.fixture()
def store(monkeypatch):
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA=")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return OAuthTokenStore(engine)


def test_save_then_get_round_trips_the_tokens(store):
    tokens = OAuthTokens(access_token="at-1", refresh_token="rt-1", expires_in=3600, scope="read write")
    store.save(user_id="u1", provider_id="google-drive", tokens=tokens)

    stored = store.get(user_id="u1", provider_id="google-drive")

    assert stored is not None
    assert stored.access_token == "at-1"
    assert stored.refresh_token == "rt-1"
    assert stored.scope == "read write"
    # SQLite (unit-test-only backend) does not round-trip tzinfo the way Postgres
    # does; the value itself is still stored/interpreted as UTC by this store.
    assert stored.expires_at is not None
    assert stored.expires_at.replace(tzinfo=UTC) > datetime.now(UTC)


def test_get_returns_none_for_an_unknown_provider(store):
    assert store.get(user_id="u1", provider_id="nope") is None


def test_the_database_never_holds_a_plaintext_token(store):
    tokens = OAuthTokens(access_token="super-secret-access", refresh_token="super-secret-refresh", expires_in=3600, scope=None)
    store.save(user_id="u1", provider_id="google-drive", tokens=tokens)

    with store._engine.connect() as connection:
        from sqlalchemy import select

        from agentos.persistence.postgres.schema import oauth_tokens

        row = connection.execute(select(oauth_tokens)).mappings().one()

    assert "super-secret-access" not in row["access_token_ciphertext"]
    assert "super-secret-refresh" not in row["refresh_token_ciphertext"]


def test_save_upserts_on_a_repeat_authorization(store):
    store.save(user_id="u1", provider_id="google-drive", tokens=OAuthTokens("at-1", "rt-1", 3600, None))
    store.save(user_id="u1", provider_id="google-drive", tokens=OAuthTokens("at-2", "rt-2", 3600, None))

    stored = store.get(user_id="u1", provider_id="google-drive")
    assert stored.access_token == "at-2"
    assert stored.refresh_token == "rt-2"


def test_a_missing_refresh_token_is_stored_as_none(store):
    store.save(user_id="u1", provider_id="google-drive", tokens=OAuthTokens("at-1", None, 3600, None))
    stored = store.get(user_id="u1", provider_id="google-drive")
    assert stored.refresh_token is None


def test_delete_removes_the_stored_tokens(store):
    store.save(user_id="u1", provider_id="google-drive", tokens=OAuthTokens("at-1", "rt-1", 3600, None))
    store.delete(user_id="u1", provider_id="google-drive")
    assert store.get(user_id="u1", provider_id="google-drive") is None


def test_expires_at_comes_back_timezone_aware(store):
    store.save(user_id="u1", provider_id="p", tokens=OAuthTokens("at", "rt", 60, None))
    assert store.get(user_id="u1", provider_id="p").expires_at.tzinfo is not None


def test_each_save_moves_the_version(store):
    store.save(user_id="u1", provider_id="p", tokens=OAuthTokens("at-1", "rt-1", 60, None))
    first = store.get(user_id="u1", provider_id="p").version
    store.save(user_id="u1", provider_id="p", tokens=OAuthTokens("at-2", "rt-2", 60, None))
    assert store.get(user_id="u1", provider_id="p").version == first + 1


def test_only_one_caller_gets_the_lease(store):
    store.save(user_id="u1", provider_id="p", tokens=OAuthTokens("at", "rt", 60, None))
    version = store.get(user_id="u1", provider_id="p").version
    assert store.try_acquire_refresh_lease(user_id="u1", provider_id="p", version=version, ttl=timedelta(seconds=30))
    assert not store.try_acquire_refresh_lease(user_id="u1", provider_id="p", version=version, ttl=timedelta(seconds=30))


def test_an_expired_lease_can_be_taken_again(store):
    store.save(user_id="u1", provider_id="p", tokens=OAuthTokens("at", "rt", 60, None))
    assert store.try_acquire_refresh_lease(user_id="u1", provider_id="p", version=0, ttl=timedelta(seconds=-1))
    assert store.try_acquire_refresh_lease(user_id="u1", provider_id="p", version=0, ttl=timedelta(seconds=30))


def test_a_stale_version_never_gets_the_lease(store):
    store.save(user_id="u1", provider_id="p", tokens=OAuthTokens("at", "rt", 60, None))
    store.save(user_id="u1", provider_id="p", tokens=OAuthTokens("at-2", "rt-2", 60, None))
    assert not store.try_acquire_refresh_lease(user_id="u1", provider_id="p", version=0, ttl=timedelta(seconds=30))


def test_saving_clears_the_lease_and_release_frees_it(store):
    store.save(user_id="u1", provider_id="p", tokens=OAuthTokens("at", "rt", 60, None))
    assert store.try_acquire_refresh_lease(user_id="u1", provider_id="p", version=0, ttl=timedelta(seconds=30))
    store.release_refresh_lease(user_id="u1", provider_id="p")
    assert store.try_acquire_refresh_lease(user_id="u1", provider_id="p", version=0, ttl=timedelta(seconds=30))
    store.save(user_id="u1", provider_id="p", tokens=OAuthTokens("at-2", "rt-2", 60, None))
    assert store.try_acquire_refresh_lease(user_id="u1", provider_id="p", version=1, ttl=timedelta(seconds=30))


def test_two_processes_on_one_file_share_the_lease(monkeypatch, tmp_path):
    # The API and the worker are separate processes over the same SQLite file.
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA=")
    url = f"sqlite+pysqlite:///{tmp_path / 'orin.db'}"
    api_engine, worker_engine = create_engine(url), create_engine(url)
    metadata.create_all(api_engine)
    api, worker = OAuthTokenStore(api_engine), OAuthTokenStore(worker_engine)
    api.save(user_id="u1", provider_id="p", tokens=OAuthTokens("at", "rt", 60, None))

    assert worker.try_acquire_refresh_lease(user_id="u1", provider_id="p", version=0, ttl=timedelta(seconds=30))
    assert not api.try_acquire_refresh_lease(user_id="u1", provider_id="p", version=0, ttl=timedelta(seconds=30))
    worker.save(user_id="u1", provider_id="p", tokens=OAuthTokens("at-2", "rt-2", 60, None))
    assert api.get(user_id="u1", provider_id="p").access_token == "at-2"

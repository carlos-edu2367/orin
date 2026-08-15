from datetime import UTC, datetime

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

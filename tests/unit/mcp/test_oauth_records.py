from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select

from agentos.mcp.oauth_records import McpOAuthClient, McpOAuthRecords
from agentos.mcp.service import McpServerService
from agentos.persistence.postgres.schema import metadata, mcp_oauth_clients, oauth_pending_authorizations


@pytest.fixture()
def engine(monkeypatch):
    monkeypatch.setenv("AGENTOS_PROVIDER_ENCRYPTION_KEY", "wYIYy1yzr2r_LRw2P0FE8zpO6zRQmYtP6cn0FdOtBOA=")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return engine


@pytest.fixture()
def server_id(engine):
    return McpServerService(engine).propose({
        "user_id": "u1", "display_name": "Auryly", "transport": "http", "url": "https://mcp.example.com/mcp",
    })["server_id"]


def _client(server_id: str, **overrides) -> McpOAuthClient:
    values = dict(server_id=server_id, issuer="https://auth.example.com/", authorization_endpoint="https://auth.example.com/authorize",
                  token_endpoint="https://auth.example.com/token", revocation_endpoint=None, resource="https://mcp.example.com/mcp",
                  scope="content:admin", client_id="c-1", client_secret="s-1", token_endpoint_auth_method="client_secret_post",
                  redirect_uri="http://127.0.0.1:49200/v1/mcp/oauth/callback")
    return McpOAuthClient(**{**values, **overrides})


def test_a_client_round_trips_with_its_secret_encrypted(engine, server_id):
    records = McpOAuthRecords(engine)
    records.save_client(_client(server_id))
    assert records.client(server_id) == _client(server_id)
    with engine.connect() as connection:
        stored = connection.execute(select(mcp_oauth_clients.c.client_secret_ciphertext)).scalar_one()
    assert stored.startswith("enc:v1:") and "s-1" not in stored


def test_saving_again_replaces_the_registration(engine, server_id):
    records = McpOAuthRecords(engine)
    records.save_client(_client(server_id))
    records.save_client(_client(server_id, client_id="c-2", redirect_uri="http://127.0.0.1:49201/v1/mcp/oauth/callback"))
    assert records.client(server_id).client_id == "c-2"


def test_the_provider_config_carries_resource_and_client_auth(engine, server_id):
    config = _client(server_id).provider_config()
    assert config.resource == "https://mcp.example.com/mcp"
    assert config.scopes == ("content:admin",)
    assert config.token_endpoint_auth_method == "client_secret_post"


def test_a_pending_sign_in_is_single_use(engine, server_id):
    records = McpOAuthRecords(engine)
    records.add_pending(state="st", user_id="u1", server_id=server_id, code_verifier="v", redirect_uri="http://127.0.0.1:1/cb",
                        ttl=timedelta(minutes=10))
    first = records.take_pending("st", user_id="u1")
    assert first is not None and first.code_verifier == "v"
    assert records.take_pending("st", user_id="u1") is None


def test_an_expired_or_foreign_pending_sign_in_is_refused(engine, server_id):
    records = McpOAuthRecords(engine)
    records.add_pending(state="old", user_id="u1", server_id=server_id, code_verifier="v", redirect_uri="http://127.0.0.1:1/cb",
                        ttl=timedelta(seconds=-1))
    records.add_pending(state="mine", user_id="u1", server_id=server_id, code_verifier="v", redirect_uri="http://127.0.0.1:1/cb",
                        ttl=timedelta(minutes=10))
    assert records.take_pending("old", user_id="u1") is None
    assert records.take_pending("mine", user_id="someone-else") is None


def test_the_verifier_is_stored_encrypted_and_expired_rows_are_purged(engine, server_id):
    records = McpOAuthRecords(engine)
    records.add_pending(state="old", user_id="u1", server_id=server_id, code_verifier="secret-verifier",
                        redirect_uri="http://127.0.0.1:1/cb", ttl=timedelta(seconds=-1))
    records.add_pending(state="new", user_id="u1", server_id=server_id, code_verifier="secret-verifier",
                        redirect_uri="http://127.0.0.1:1/cb", ttl=timedelta(minutes=10))
    with engine.connect() as connection:
        rows = connection.execute(select(oauth_pending_authorizations)).mappings().all()
    assert [row["state"] for row in rows] == ["new"]
    assert "secret-verifier" not in rows[0]["code_verifier_ciphertext"]


def test_reauth_marks_the_server_as_error(engine, server_id):
    records = McpOAuthRecords(engine)
    records.mark_reauth_required(server_id, "Reconexão necessária")
    row = records.server("u1", server_id)
    assert (row["state"], row["state_reason"]) == ("error", "Reconexão necessária")

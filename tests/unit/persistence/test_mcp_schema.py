from agentos.persistence.postgres.schema import (
    mcp_oauth_clients, mcp_server_tools, mcp_servers, oauth_pending_authorizations, oauth_tokens,
)


def test_mcp_servers_declares_the_columns_the_service_needs():
    columns = set(mcp_servers.c.keys())
    assert {"server_id", "user_id", "slug", "display_name", "transport", "command", "args",
            "url", "secret_names", "secrets_ciphertext", "catalog_id", "tool_allowlist",
            "state", "state_reason", "protocol_version", "tools_digest",
            "created_at", "updated_at"} <= columns


def test_a_slug_is_unique_per_user():
    names = {constraint.name for constraint in mcp_servers.constraints if constraint.name}
    assert "uq_mcp_servers_slug" in names


def test_mcp_server_tools_keeps_the_discovered_schema():
    columns = set(mcp_server_tools.c.keys())
    assert {"id", "server_id", "name", "description", "input_schema", "enabled", "discovered_at"} <= columns


def test_mcp_servers_records_how_the_server_authenticates():
    assert "auth_kind" in mcp_servers.c


def test_mcp_oauth_clients_keeps_the_registration_per_server():
    columns = set(mcp_oauth_clients.c.keys())
    assert {"server_id", "issuer", "authorization_endpoint", "token_endpoint", "revocation_endpoint", "resource",
            "scope", "client_id", "client_secret_ciphertext", "token_endpoint_auth_method", "redirect_uri",
            "created_at", "updated_at"} <= columns
    assert [key.column.table.name for key in mcp_oauth_clients.foreign_keys] == ["mcp_servers"]


def test_pending_authorizations_store_only_the_encrypted_verifier():
    columns = set(oauth_pending_authorizations.c.keys())
    assert {"state", "user_id", "server_id", "code_verifier_ciphertext", "redirect_uri", "expires_at", "created_at"} <= columns
    assert "code_verifier" not in columns


def test_oauth_tokens_carry_a_version_and_a_refresh_lease():
    assert {"version", "refresh_lease_until"} <= set(oauth_tokens.c.keys())

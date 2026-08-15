from agentos.persistence.postgres.schema import mcp_server_tools, mcp_servers


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

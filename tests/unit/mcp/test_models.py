import pytest

from agentos.mcp.models import (
    McpServerConfig, McpServerState, McpToolDescriptor, McpTransport,
    qualified_tool_name, slugify, tools_digest,
)


def test_slugify_produces_a_bounded_lowercase_identifier():
    assert slugify("Notion Workspace") == "notion-workspace"
    assert slugify("  GitHub  ") == "github"
    assert len(slugify("x" * 200)) <= 32


def test_slugify_rejects_a_name_without_usable_characters():
    with pytest.raises(ValueError):
        slugify("***")


def test_qualified_tool_name_namespaces_the_remote_tool():
    assert qualified_tool_name("notion", "search") == "mcp__notion__search"


def test_tools_digest_is_stable_and_order_independent():
    first = McpToolDescriptor(name="a", description="d", input_schema={"type": "object"})
    second = McpToolDescriptor(name="b", description="e", input_schema={"type": "object"})
    assert tools_digest((first, second)) == tools_digest((second, first))
    changed = McpToolDescriptor(name="a", description="d2", input_schema={"type": "object"})
    assert tools_digest((first, second)) != tools_digest((changed, second))


def test_stdio_config_requires_a_command_and_http_requires_a_url():
    with pytest.raises(ValueError):
        McpServerConfig(server_id="s1", user_id="u1", slug="x", display_name="X",
                        transport=McpTransport.STDIO, command=None)
    with pytest.raises(ValueError):
        McpServerConfig(server_id="s1", user_id="u1", slug="x", display_name="X",
                        transport=McpTransport.HTTP, url=None)


def test_a_new_config_starts_in_pending_approval():
    config = McpServerConfig(server_id="s1", user_id="u1", slug="x", display_name="X",
                             transport=McpTransport.HTTP, url="https://mcp.example.com/v1")
    assert config.state is McpServerState.PENDING_APPROVAL
    assert config.is_usable is False

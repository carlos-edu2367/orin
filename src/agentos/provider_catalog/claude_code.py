"""Compatibility facade re-exporting from `agentos.providers.claude_cli`."""
from __future__ import annotations

from agentos.providers.claude_cli.catalog import (
    PUBLISHED_CLAUDE_MODEL_CATALOG_URL,
    ClaudeCliModelCatalogClient,
    ClaudeCodeModelCatalogClient,
    _discover_cached_claude_cli_models,
    _fetch_published_claude_cli_models,
    _format_catalog_model_entry,
)
from agentos.providers.claude_cli.command import (
    build_claude_command as _build_claude_cli_command,
    build_isolated_claude_env,
    check_claude_cli_status,
    resolve_claude_binary as _resolve_claude_binary,
)
from agentos.providers.claude_cli.errors import ProviderTransportError
from agentos.providers.claude_cli.provider import (
    ANTHROPIC_CLOUD_BASE_URL,
    CLAUDE_CODE_LOCAL_BASE_URL,
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_CLAUDE_CLI_BRIDGE_URL,
    ClaudeCliProvider,
    is_anthropic_cloud,
    is_claude_cli_bridge_url,
    kill_process_tree as _kill_process_tree,
    normalize_anthropic_base_url,
    stream_claude_code_bridge,
)
from agentos.providers.claude_cli.stream import StreamUsage
from agentos.providers.claude_cli.text_protocol import (
    resolve_native_tool_call as _resolve_native_tool_call,
)

__all__ = [
    "ANTHROPIC_CLOUD_BASE_URL",
    "CLAUDE_CODE_LOCAL_BASE_URL",
    "ClaudeCliModelCatalogClient",
    "ClaudeCliProvider",
    "ClaudeCodeModelCatalogClient",
    "DEFAULT_ANTHROPIC_BASE_URL",
    "DEFAULT_CLAUDE_CLI_BRIDGE_URL",
    "PUBLISHED_CLAUDE_MODEL_CATALOG_URL",
    "ProviderTransportError",
    "StreamUsage",
    "_build_claude_cli_command",
    "_discover_cached_claude_cli_models",
    "_fetch_published_claude_cli_models",
    "_format_catalog_model_entry",
    "_kill_process_tree",
    "_resolve_claude_binary",
    "_resolve_native_tool_call",
    "build_isolated_claude_env",
    "check_claude_cli_status",
    "is_anthropic_cloud",
    "is_claude_cli_bridge_url",
    "normalize_anthropic_base_url",
    "stream_claude_code_bridge",
]

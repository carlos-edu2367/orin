"""Native Google Antigravity CLI (`agy`) adapter package."""
from __future__ import annotations

from agentos.providers.antigravity_cli.adapter import AntigravityCliAdapter
from agentos.providers.antigravity_cli.catalog import (
    DEFAULT_ANTIGRAVITY_CONTEXT_WINDOW,
    FALLBACK_ANTIGRAVITY_MODELS,
    context_window_for,
    discover_antigravity_models,
)
from agentos.providers.antigravity_cli.command import (
    DEFAULT_ANTIGRAVITY_MODEL,
    MAX_ANTIGRAVITY_MAJOR,
    MIN_ANTIGRAVITY_VERSION,
    VALID_EFFORT_LEVELS,
    antigravity_version,
    build_antigravity_command,
    build_isolated_antigravity_env,
    clean_antigravity_model_id,
    encode_antigravity_user_turn,
    is_antigravity_version_supported,
    redact_antigravity_argv,
    resolve_antigravity_binary,
    wire_antigravity_tool_server,
)
from agentos.providers.antigravity_cli.errors import classify_antigravity_error
from agentos.providers.antigravity_cli.stream import (
    FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
    AntigravityStreamTranslator,
)

__all__ = [
    "AntigravityCliAdapter",
    "AntigravityStreamTranslator",
    "DEFAULT_ANTIGRAVITY_CONTEXT_WINDOW",
    "DEFAULT_ANTIGRAVITY_MODEL",
    "FALLBACK_ANTIGRAVITY_MODELS",
    "FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS",
    "MAX_ANTIGRAVITY_MAJOR",
    "MIN_ANTIGRAVITY_VERSION",
    "VALID_EFFORT_LEVELS",
    "antigravity_version",
    "build_antigravity_command",
    "build_isolated_antigravity_env",
    "classify_antigravity_error",
    "clean_antigravity_model_id",
    "context_window_for",
    "discover_antigravity_models",
    "encode_antigravity_user_turn",
    "is_antigravity_version_supported",
    "redact_antigravity_argv",
    "resolve_antigravity_binary",
    "wire_antigravity_tool_server",
]

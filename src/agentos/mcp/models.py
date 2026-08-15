"""Value types for MCP server configuration and remote tool descriptors.

A descriptor arriving from a remote server is untrusted data (RFC 903): these
types only carry it, never grant anything because of it.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

MAX_SLUG_LENGTH = 32
TOOL_NAME_PREFIX = "mcp"


class McpTransport(StrEnum):
    STDIO = "stdio"
    HTTP = "http"


class McpServerState(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    DISABLED = "disabled"
    ERROR = "error"


def slugify(value: str) -> str:
    slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")
    if not slug:
        raise ValueError("mcp server name does not produce an identifier")
    return slug[:MAX_SLUG_LENGTH].rstrip("-")


def qualified_tool_name(slug: str, tool_name: str) -> str:
    return f"{TOOL_NAME_PREFIX}__{slug}__{tool_name}"


@dataclass(frozen=True, slots=True)
class McpToolDescriptor:
    name: str
    description: str
    input_schema: Mapping[str, Any]


def tools_digest(tools: tuple[McpToolDescriptor, ...]) -> str:
    payload = sorted(
        (item.name, item.description, json.dumps(item.input_schema, sort_keys=True))
        for item in tools
    )
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    server_id: str
    user_id: str
    slug: str
    display_name: str
    transport: McpTransport
    command: str | None = None
    args: tuple[str, ...] = ()
    url: str | None = None
    # Names only. Values live encrypted and never enter this type.
    secret_names: tuple[str, ...] = ()
    catalog_id: str | None = None
    tool_allowlist: tuple[str, ...] | None = None
    state: McpServerState = McpServerState.PENDING_APPROVAL
    state_reason: str = ""
    protocol_version: str = ""
    tools_digest: str = ""

    def __post_init__(self) -> None:
        if self.transport is McpTransport.STDIO and not self.command:
            raise ValueError("a stdio mcp server requires a command")
        if self.transport is McpTransport.HTTP and not self.url:
            raise ValueError("an http mcp server requires a url")
        if self.slug != slugify(self.slug):
            raise ValueError("mcp server slug is not normalized")

    @property
    def is_usable(self) -> bool:
        return self.state is McpServerState.ACTIVE


__all__ = [
    "MAX_SLUG_LENGTH", "McpServerConfig", "McpServerState", "McpToolDescriptor",
    "McpTransport", "qualified_tool_name", "slugify", "tools_digest",
]

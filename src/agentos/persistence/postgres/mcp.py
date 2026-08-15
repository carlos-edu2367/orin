"""Row <-> dataclass helpers for MCP server persistence.

Kept separate from the service so the SQL shape of a row never leaks into the
approval/activation rules that live in agentos.mcp.service.
"""
from __future__ import annotations

from typing import Any, Mapping

from agentos.mcp.models import McpServerConfig, McpServerState, McpToolDescriptor, McpTransport


def row_to_config(row: Mapping[str, Any]) -> McpServerConfig:
    return McpServerConfig(
        server_id=str(row["server_id"]),
        user_id=str(row["user_id"]),
        slug=str(row["slug"]),
        display_name=str(row["display_name"]),
        transport=McpTransport(str(row["transport"])),
        command=row["command"],
        args=tuple(row["args"] or ()),
        url=row["url"],
        secret_names=tuple(row["secret_names"] or ()),
        catalog_id=row["catalog_id"],
        tool_allowlist=tuple(row["tool_allowlist"]) if row["tool_allowlist"] is not None else None,
        state=McpServerState(str(row["state"])),
        state_reason=str(row["state_reason"] or ""),
        protocol_version=str(row["protocol_version"] or ""),
        tools_digest=str(row["tools_digest"] or ""),
    )


def row_to_tool(row: Mapping[str, Any]) -> McpToolDescriptor:
    return McpToolDescriptor(name=str(row["name"]), description=str(row["description"]),
                             input_schema=dict(row["input_schema"] or {}))


def public_summary(row: Mapping[str, Any], *, tool_count: int) -> dict[str, Any]:
    """The shape returned to callers: never the ciphertext, only secret names."""
    return {
        "server_id": str(row["server_id"]), "slug": str(row["slug"]), "display_name": str(row["display_name"]),
        "transport": str(row["transport"]), "command": row["command"], "args": list(row["args"] or ()),
        "url": row["url"], "secret_names": list(row["secret_names"] or ()), "catalog_id": row["catalog_id"],
        "state": str(row["state"]), "state_reason": str(row["state_reason"] or ""),
        "protocol_version": str(row["protocol_version"] or ""), "tool_count": tool_count,
    }


__all__ = ["public_summary", "row_to_config", "row_to_tool"]

"""MCP resources and tool refresh (W5): ListMcpResources, ReadMcpResource, ReadMcpResourceDir, RefreshMcpTools.

Mixed into ``AgentToolset`` through ``McpTools``; ``self`` is the toolset. A resource's text reaches the model as
data; an image arrives as an image; other binary content is described, not shown.
"""
from __future__ import annotations

import base64
import json
from typing import Any

from pydantic import Field

from agentos.agentic.tools._shared import AgentToolError, ToolOutcome, _TEXT, _schema
from agentos.agentic.tools.spec import ToolInput, ToolSpec

MAX_LISTED_RESOURCES = 500
MAX_RESOURCE_TEXT_CHARS = 100_000
MAX_RESOURCE_IMAGES = 4


class McpResourceTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def _resource_provider(self) -> Any:
        provider = getattr(self, "_mcp_provider", None)
        if provider is None:
            raise AgentToolError("No MCP server is connected in this turn.")
        return provider

    def _known_server(self, provider: Any, server: str | None) -> str | None:
        clean = str(server or "").strip()
        if clean and provider.find_server(clean) is None:
            names = ", ".join(sorted(provider.server_slugs())) or "none"
            raise AgentToolError(f"No MCP server named '{clean[:80]}'. Connected servers: {names}.")
        return clean or None

    def list_mcp_resources(self, server: str | None = None) -> ToolOutcome:
        provider = self._resource_provider()
        items = provider.list_resources(self._known_server(provider, server))
        return _listing(items, "recursos MCP")

    def read_mcp_resource_dir(self, server: str, uri: str) -> ToolOutcome:
        provider = self._resource_provider()
        clean_server = self._known_server(provider, server)
        prefix = str(uri or "").strip()
        if not clean_server or not prefix:
            raise AgentToolError("Give the server and the address (uri) of the folder of resources.")
        items = [item for item in provider.list_resources(clean_server) if "error" in item or str(item.get("uri", "")).startswith(prefix)]
        return _listing(items, f"recursos em {prefix[:80]}")

    def read_mcp_resource(self, server: str, uri: str) -> ToolOutcome:
        provider = self._resource_provider()
        clean_server = self._known_server(provider, server)
        clean_uri = str(uri or "").strip()
        if not clean_server or not clean_uri:
            raise AgentToolError("Give the server and the resource's address (uri).")
        try:
            contents = provider.read_resource(clean_server, clean_uri)
        except Exception as error:  # noqa: BLE001 - the server's failure is the tool's answer
            message = f"{clean_server} could not read {clean_uri[:200]}: {str(error)[:500]}"
            return ToolOutcome("failed", message[:240], message, {"tool_kind": "mcp", "mcp_server": clean_server}, "MCP_UNAVAILABLE")
        parts: list[str] = []
        images: list[dict[str, str]] = []
        for item in contents:
            mime = str(item.get("mimeType") or "")
            if isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item.get("blob"), str):
                if mime.startswith("image/") and len(images) < MAX_RESOURCE_IMAGES:
                    images.append({"media_type": mime, "data": item["blob"]})
                else:
                    size = len(base64.b64decode(item["blob"], validate=False)) if item["blob"] else 0
                    parts.append(f"[binary content of {size} bytes, {mime or 'unknown type'}, not shown]")
        text = "\n\n".join(parts)
        if len(text) > MAX_RESOURCE_TEXT_CHARS:
            text = text[:MAX_RESOURCE_TEXT_CHARS] + "\n[truncated]"
        payload = {"tool_kind": "mcp", "mcp_server": clean_server, "uri": clean_uri[:500]}
        return ToolOutcome("succeeded", f"Leu {clean_uri[:120]}", text or "(the resource is empty)", payload, None, images)

    def refresh_mcp_tools(self, server: str | None = None) -> ToolOutcome:
        provider = self._resource_provider()
        report = provider.refresh_tools(self._known_server(provider, server))
        changed = sum(len(item.get("added", [])) + len(item.get("removed", [])) for item in report)
        return ToolOutcome(
            "succeeded", f"Ferramentas MCP atualizadas ({changed} mudança(s))", json.dumps(report, ensure_ascii=False, indent=1),
            {"tool_kind": "mcp", "servers": [item.get("server") for item in report]},
        )


def _listing(items: list[dict[str, Any]], what: str) -> ToolOutcome:
    shown = items[:MAX_LISTED_RESOURCES]
    content = json.dumps(shown, ensure_ascii=False, indent=1)
    if len(items) > len(shown):
        content += f"\n[{len(items) - len(shown)} more not shown]"
    return ToolOutcome("succeeded", f"{len([item for item in items if 'uri' in item])} {what}", content, {"tool_kind": "mcp"})


class ListMcpResourcesInput(ToolInput):
    server: str | None = Field(None, description="Only this server's resources.")


class ReadMcpResourceInput(ToolInput):
    server: str = Field(description="The MCP server.")
    uri: str = Field(description="The resource's address.")


class ReadMcpResourceDirInput(ToolInput):
    server: str = Field(description="The MCP server.")
    uri: str = Field(description="The address the listed resources start with.")


class RefreshMcpToolsInput(ToolInput):
    server: str | None = Field(None, description="Only this server.")


_ON = {"read_only": True, "kind": "mcp", "policy_tags": ("mcp",), "max_result_chars": 100_000}


def _available(toolset: Any) -> bool:
    return getattr(toolset, "_mcp_provider", None) is not None


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="list_mcp_resources",
        description="List the resources (files, records, documents) the connected MCP servers offer, optionally for one server.",
        legacy_schema=_schema({"server": _TEXT}, ()),
        handler="list_mcp_resources", input_model=ListMcpResourcesInput,
        public_description="List the resources the connected MCP servers offer.", available=_available, order=640, **_ON,
    ),
    ToolSpec(
        key="read_mcp_resource",
        description="Read one resource from an MCP server by its address (uri).",
        legacy_schema=_schema({"server": _TEXT, "uri": _TEXT}, ("server", "uri")),
        handler="read_mcp_resource", input_model=ReadMcpResourceInput,
        public_description="Read a resource from an MCP server by its address.", available=_available, order=641, **_ON,
    ),
    ToolSpec(
        key="read_mcp_resource_dir",
        description="List the resources of an MCP server whose address starts with the given uri (a folder of resources).",
        legacy_schema=_schema({"server": _TEXT, "uri": _TEXT}, ("server", "uri")),
        handler="read_mcp_resource_dir", input_model=ReadMcpResourceDirInput,
        public_description="List the resources of an MCP server under an address.", available=_available, order=642, **_ON,
    ),
    ToolSpec(
        key="refresh_mcp_tools",
        description="Ask the connected MCP servers (or one) for their tools again. Servers from configuration files get the new list; Orin's own servers keep the tools the person approved and report the difference.",
        legacy_schema=_schema({"server": _TEXT}, ()),
        handler="refresh_mcp_tools", input_model=RefreshMcpToolsInput,
        public_description="List the MCP servers' tools again.", available=_available, order=643, **_ON,
    ),
)

__all__ = [
    "ListMcpResourcesInput", "McpResourceTools", "ReadMcpResourceDirInput", "ReadMcpResourceInput", "RefreshMcpToolsInput", "SPECS",
]

"""Turn cached MCP descriptors into native ToolDefinitions.

Building the tool set must not touch the network: the definitions come from the
discovery cache. A session opens on the first call to that server and closes
with the turn, so a configured-but-unused server costs nothing.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Sequence

from agentos.agentic.agent_tools import ToolOutcome, _bounded
from .models import McpServerConfig, McpToolDescriptor, McpTransport, qualified_tool_name

MAX_MCP_RESULT_CHARS = 12_000
MAX_IMAGES_PER_CALL = 4

ServerBundle = tuple[McpServerConfig, tuple[McpToolDescriptor, ...], Mapping[str, str]]


def build_client(config: McpServerConfig, secrets: Mapping[str, str]) -> "McpClient":
    # Imported here so building a tool set never imports a transport it will not
    # use, and so the client module stays free of a cycle back into this one.
    from .client import McpClient
    from .transport_http import HttpTransport
    from .transport_stdio import StdioTransport

    if config.transport is McpTransport.STDIO:
        transport = StdioTransport(command=str(config.command), args=config.args, env=dict(secrets))
    else:
        headers = {"authorization": f"Bearer {secrets['token']}"} if "token" in secrets else {}
        transport = HttpTransport(url=str(config.url), headers=headers)
    return McpClient(transport)


class McpToolProvider:
    def __init__(self, bundles: Iterable[ServerBundle],
                 client_factory: Callable[[McpServerConfig, Mapping[str, str]], Any] = build_client) -> None:
        self._bundles = list(bundles)
        self._client_factory = client_factory
        self._sessions: dict[str, Any] = {}

    @property
    def open_session_count(self) -> int:
        return len(self._sessions)

    def _session(self, config: McpServerConfig, secrets: Mapping[str, str]) -> Any:
        client = self._sessions.get(config.server_id)
        if client is None:
            client = self._client_factory(config, secrets)
            client.initialize()
            self._sessions[config.server_id] = client
        return client

    def _handler(self, config: McpServerConfig, secrets: Mapping[str, str], tool: McpToolDescriptor):
        def call(**arguments: Any) -> ToolOutcome:
            from .protocol import McpProtocolError

            try:
                result = self._session(config, secrets).call_tool(tool.name, arguments)
            except (McpProtocolError, RuntimeError) as error:
                message = f"{config.display_name}: {error}"
                return ToolOutcome("failed", message[:240], message[:MAX_MCP_RESULT_CHARS],
                                   {"tool_kind": "mcp", "mcp_server": config.slug, "mcp_tool": tool.name},
                                   "MCP_UNAVAILABLE")
            text, images = _render(result.content)
            payload = {"tool_kind": "mcp", "mcp_server": config.slug, "mcp_tool": tool.name}
            if result.is_error:
                return ToolOutcome("failed", f"{config.display_name} recusou {tool.name}"[:240],
                                   text or "the server reported a tool error", payload, "MCP_TOOL_ERROR")
            return ToolOutcome("succeeded", f"{config.display_name} · {tool.name}"[:240], text, payload, None, images)

        return call

    def definitions(self) -> tuple[Any, ...]:
        from agentos.agentic.agent_tools import ToolDefinition

        built: list[ToolDefinition] = []
        for config, tools, secrets in self._bundles:
            for tool in tools:
                description = f"[{config.display_name}] {tool.description}".strip()[:1200]
                built.append(ToolDefinition(
                    qualified_tool_name(config.slug, tool.name),
                    description,
                    dict(tool.input_schema),
                    self._handler(config, secrets, tool),
                    "mcp",
                    read_only=False,
                    policy_tags=("mcp", "mutates", f"mcp:{config.slug}"),
                ))
        return tuple(built)

    def close(self) -> None:
        for client in list(self._sessions.values()):
            try:
                client.close()
            except Exception:  # closing must never fail a finished turn
                pass
        self._sessions.clear()


def _render(content: Sequence[Mapping[str, Any]]) -> tuple[str, list[dict[str, str]]]:
    parts: list[str] = []
    images: list[dict[str, str]] = []
    for block in content:
        kind = str(block.get("type") or "")
        if kind == "text":
            parts.append(str(block.get("text") or ""))
        elif kind == "image" and len(images) < MAX_IMAGES_PER_CALL:
            images.append({"media_type": str(block.get("mimeType") or "image/png"), "data": str(block.get("data") or "")})
        elif kind == "resource":
            resource = block.get("resource") if isinstance(block.get("resource"), Mapping) else {}
            parts.append(str(resource.get("text") or resource.get("uri") or ""))
    text, _ = _bounded("\n".join(part for part in parts if part), MAX_MCP_RESULT_CHARS)
    return text, images


__all__ = ["McpToolProvider", "ServerBundle", "build_client"]

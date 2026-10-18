"""The MCP client: negotiate once, discover tools, call one tool at a time."""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol


from .models import McpToolDescriptor
from .protocol import CLIENT_INFO, PROTOCOL_VERSION, McpProtocolError, notification, parse_response, request
from .sanitize import sanitize_tool_descriptors

MAX_TOOL_PAGES = 1000


class McpTransportPort(Protocol):
    kind: str

    def open(self) -> None: ...
    def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class McpNegotiation:
    protocol_version: str
    server_name: str
    capabilities: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class McpCallResult:
    content: tuple[Mapping[str, Any], ...]
    is_error: bool


class McpClient:
    def __init__(self, transport: McpTransportPort) -> None:
        self._transport = transport
        self._ids = itertools.count(1)
        self._negotiation: McpNegotiation | None = None

    @property
    def negotiation(self) -> McpNegotiation | None:
        return self._negotiation

    def _call(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        request_id = next(self._ids)
        frame = self._transport.send(request(request_id, method, params))
        if frame is None:
            raise McpProtocolError(f"the transport returned no response for {method}")
        return parse_response(frame, expected_id=request_id)

    def initialize(self) -> McpNegotiation:
        self._transport.open()
        result = self._call("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientInfo": CLIENT_INFO,
            "capabilities": {},
        })
        server_info = result.get("serverInfo") if isinstance(result.get("serverInfo"), Mapping) else {}
        self._negotiation = McpNegotiation(
            protocol_version=str(result.get("protocolVersion") or PROTOCOL_VERSION),
            server_name=str(server_info.get("name") or "")[:120],
            capabilities=dict(result.get("capabilities") or {}),
        )
        self._transport.send(notification("notifications/initialized"))
        return self._negotiation

    def _require_session(self) -> None:
        if self._negotiation is None:
            raise McpProtocolError("the MCP session was not initialized")

    def list_tools(self) -> tuple[McpToolDescriptor, ...]:
        self._require_session()
        collected: list[Mapping[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(MAX_TOOL_PAGES):
            params = {"cursor": cursor} if cursor else {}
            result = self._call("tools/list", params)
            raw = result.get("tools")
            collected.extend(item for item in (raw or []) if isinstance(item, Mapping))
            next_cursor = result.get("nextCursor")
            cursor = next_cursor.strip() if isinstance(next_cursor, str) else None
            if not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
        return sanitize_tool_descriptors(collected)

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> McpCallResult:
        self._require_session()
        result = self._call("tools/call", {"name": name, "arguments": dict(arguments)})
        content = tuple(item for item in (result.get("content") or []) if isinstance(item, Mapping))
        if not content and result.get("structuredContent") is not None:
            try:
                text = json.dumps(result["structuredContent"], ensure_ascii=False)
            except TypeError:
                text = str(result["structuredContent"])
            content = ({"type": "text", "text": text},)
        return McpCallResult(content=content, is_error=bool(result.get("isError")))


    def close(self) -> None:
        self._negotiation = None
        self._transport.close()


__all__ = ["McpCallResult", "McpClient", "McpNegotiation", "McpTransportPort"]

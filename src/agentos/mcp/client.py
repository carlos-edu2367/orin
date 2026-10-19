"""The MCP client: negotiate once, discover tools, call one tool at a time.

While a call is in flight the server may send its own requests and notifications (W5). The transport hands
them to ``on_message`` (set on the transport by the client): ``ping`` and ``roots/list`` are answered,
``elicitation/create`` goes to the ``elicit`` callback (declined without one), anything else is refused, and a
``list_changed`` notification is noted in ``changed`` so the caller can list again.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol


from .models import McpToolDescriptor
from .protocol import CLIENT_INFO, PROTOCOL_VERSION, McpProtocolError, notification, parse_response, request
from .sanitize import sanitize_tool_descriptors

MAX_TOOL_PAGES = 1000
MAX_RESOURCE_PAGES = 100


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


_CHANGED = {
    "notifications/tools/list_changed": "tools",
    "notifications/resources/list_changed": "resources",
    "notifications/prompts/list_changed": "prompts",
}


class McpClient:
    def __init__(
        self,
        transport: McpTransportPort,
        *,
        roots: tuple[tuple[str, str], ...] = (),
        elicit: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        on_notification: Callable[[str, Mapping[str, Any]], None] | None = None,
    ) -> None:
        self._transport = transport
        self._ids = itertools.count(1)
        self._negotiation: McpNegotiation | None = None
        self._roots = tuple(roots)
        self._elicit = elicit
        self._on_notification = on_notification
        self.changed: set[str] = set()  # "tools", "resources", "prompts" the server said have changed
        try:
            transport.on_message = self._handle_server_message  # type: ignore[attr-defined]
        except AttributeError:  # a transport that cannot take it simply never delivers server messages
            pass

    def _handle_server_message(self, message: Mapping[str, Any]) -> Mapping[str, Any] | None:
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), Mapping) else {}
        if message.get("id") is None:
            kind = _CHANGED.get(method)
            if kind is not None:
                self.changed.add(kind)
            if self._on_notification is not None:
                self._on_notification(method, params)
            return None
        if method == "ping":
            return {}
        if method == "roots/list":
            return {"roots": [{"uri": uri, "name": name} for uri, name in self._roots]}
        if method == "elicitation/create":
            if self._elicit is None:
                return {"action": "decline"}
            return dict(self._elicit(params))
        return None  # sampling and anything else: not supported

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
            "capabilities": {"roots": {"listChanged": False}, "elicitation": {}},
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


    def list_resources(self) -> tuple[dict[str, str], ...]:
        """The server's resources (W5): address, name, description and type, bounded."""
        self._require_session()
        items: list[dict[str, str]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(MAX_RESOURCE_PAGES):
            result = self._call("resources/list", {"cursor": cursor} if cursor else {})
            for raw in result.get("resources") or []:
                if isinstance(raw, Mapping) and isinstance(raw.get("uri"), str):
                    items.append({
                        "uri": raw["uri"][:2000], "name": str(raw.get("name") or "")[:200],
                        "description": str(raw.get("description") or "")[:1000], "mimeType": str(raw.get("mimeType") or "")[:100],
                    })
            next_cursor = result.get("nextCursor")
            cursor = next_cursor.strip() if isinstance(next_cursor, str) else None
            if not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
        return tuple(items)

    def read_resource(self, uri: str) -> tuple[Mapping[str, Any], ...]:
        self._require_session()
        result = self._call("resources/read", {"uri": str(uri)})
        return tuple(item for item in (result.get("contents") or []) if isinstance(item, Mapping))

    def list_prompts(self) -> tuple[dict[str, Any], ...]:
        """The server's prompts (W5): name, description and arguments (name, description, required)."""
        self._require_session()
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(MAX_RESOURCE_PAGES):
            result = self._call("prompts/list", {"cursor": cursor} if cursor else {})
            for raw in result.get("prompts") or []:
                if not isinstance(raw, Mapping) or not isinstance(raw.get("name"), str):
                    continue
                arguments = [
                    {"name": str(item["name"])[:100], "description": str(item.get("description") or "")[:500], "required": item.get("required") is True}
                    for item in (raw.get("arguments") or []) if isinstance(item, Mapping) and isinstance(item.get("name"), str)
                ][:20]
                items.append({"name": raw["name"][:200], "description": str(raw.get("description") or "")[:1000], "arguments": arguments})
            next_cursor = result.get("nextCursor")
            cursor = next_cursor.strip() if isinstance(next_cursor, str) else None
            if not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
        return tuple(items)

    def get_prompt(self, name: str, arguments: Mapping[str, str]) -> tuple[Mapping[str, Any], ...]:
        self._require_session()
        result = self._call("prompts/get", {"name": str(name), "arguments": {str(k): str(v) for k, v in arguments.items()}})
        return tuple(item for item in (result.get("messages") or []) if isinstance(item, Mapping))

    def close(self) -> None:
        self._negotiation = None
        self._transport.close()


__all__ = ["McpCallResult", "McpClient", "McpNegotiation", "McpTransportPort"]

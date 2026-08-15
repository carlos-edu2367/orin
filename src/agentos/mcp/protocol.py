"""Minimal JSON-RPC 2.0 framing for MCP. No transport knowledge here."""
from __future__ import annotations

from typing import Any, Mapping

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "orin", "version": "1"}


class McpProtocolError(RuntimeError):
    """The peer answered with an error frame or an unusable envelope."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def request(request_id: int, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        frame["params"] = dict(params)
    return frame


def notification(method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        frame["params"] = dict(params)
    return frame


def parse_response(frame: Mapping[str, Any], *, expected_id: int) -> dict[str, Any]:
    if not isinstance(frame, Mapping) or frame.get("jsonrpc") != "2.0":
        raise McpProtocolError("the peer did not answer with a JSON-RPC 2.0 envelope")
    if frame.get("id") != expected_id:
        raise McpProtocolError(f"expected a response for request {expected_id}, got {frame.get('id')!r}")
    if "error" in frame:
        error = frame["error"] if isinstance(frame["error"], Mapping) else {}
        raise McpProtocolError(str(error.get("message") or "the server refused the request")[:512],
                               code=error.get("code") if isinstance(error.get("code"), int) else None)
    result = frame.get("result")
    if not isinstance(result, Mapping):
        raise McpProtocolError("the response carried no result object")
    return dict(result)


__all__ = ["CLIENT_INFO", "PROTOCOL_VERSION", "McpProtocolError", "notification", "parse_response", "request"]

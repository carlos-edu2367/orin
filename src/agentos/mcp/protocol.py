"""Minimal JSON-RPC 2.0 framing for MCP. No transport knowledge here."""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping


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
        message = str(error.get("message") or "the server refused the request")
        data = error.get("data")
        if data is not None:
            try:
                serialized = json.dumps(data, ensure_ascii=False)
            except TypeError:
                serialized = str(data)
            message = f"{message} ({serialized})"
        raise McpProtocolError(message[:2048],
                               code=error.get("code") if isinstance(error.get("code"), int) else None)
    result = frame.get("result")

    if not isinstance(result, Mapping):
        raise McpProtocolError("the response carried no result object")
    return dict(result)


METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


def response(request_id: Any, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": dict(result)}


def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": int(code), "message": str(message)[:500]}}


def dispatch_server_message(message: Mapping[str, Any], handler: Callable[[Mapping[str, Any]], Any] | None) -> dict[str, Any] | None:
    """A request or notification the server sent us: the frame to send back (requests only), or None.

    ``handler`` returns the result object for a request it supports and None otherwise (answered "method not
    found"); for a notification its return value is ignored. A handler that raises answers with an internal error.
    """
    method = message.get("method")
    if not isinstance(method, str):
        return None  # a stray response: nothing to answer
    is_request = message.get("id") is not None
    try:
        result = handler(message) if handler is not None else None
    except Exception:  # noqa: BLE001 - the server gets an error answer, the call goes on
        return error_response(message["id"], INTERNAL_ERROR, f"the client could not handle {method[:80]}") if is_request else None
    if not is_request:
        return None
    if not isinstance(result, Mapping):
        return error_response(message["id"], METHOD_NOT_FOUND, f"method not supported: {method[:80]}")
    return response(message["id"], result)


__all__ = [
    "CLIENT_INFO", "INTERNAL_ERROR", "METHOD_NOT_FOUND", "PROTOCOL_VERSION", "McpProtocolError", "dispatch_server_message",
    "error_response", "notification", "parse_response", "request", "response",
]

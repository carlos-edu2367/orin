"""The WebSocket transport for an MCP server in a configuration file (``"type": "ws"``).

One connection (subprotocol ``mcp``); each JSON-RPC message is one text frame. The server's own requests that
arrive while a call waits are answered on the same connection. Without ``allow_local`` the address must be public
``wss://``.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Mapping

from .protocol import MAX_SERVER_REQUESTS_PER_CALL, call_ceiling_seconds, dispatch_server_message
from .transport_sse import checked_url

DEFAULT_TIMEOUT_SECONDS = 45.0
MAX_FRAME_BYTES = 4_000_000


class WebSocketTransportError(RuntimeError):
    """The connection failed or closed."""


def _default_connect(url: str, **options: Any) -> Any:
    from websockets.sync.client import connect

    return connect(url, **options)


class WebSocketTransport:
    kind = "ws"

    def __init__(self, *, url: str, headers: Mapping[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 allow_local: bool = False, connect: Callable[..., Any] | None = None) -> None:
        try:
            self._url = checked_url(url, allow_local=allow_local, secure="wss://", plain="ws://")
        except RuntimeError as error:
            raise WebSocketTransportError(str(error)) from error
        self._headers = dict(headers or {})
        self._allow_local = allow_local
        self._timeout = timeout
        self._connect = connect or _default_connect
        self._socket: Any = None
        self._lock = threading.Lock()
        self.on_message: Any = None  # set by the client: the server's own requests and notifications

    def open(self) -> None:
        if self._socket is not None:
            return
        try:
            checked_url(self._url, allow_local=self._allow_local, secure="wss://", plain="ws://")  # checked again on opening
        except RuntimeError as error:
            raise WebSocketTransportError(str(error)) from error
        try:
            self._socket = self._connect(
                self._url, subprotocols=["mcp"], additional_headers=self._headers or None,
                open_timeout=self._timeout, max_size=MAX_FRAME_BYTES,
            )
        except Exception as error:  # noqa: BLE001 - reported for this server only
            raise WebSocketTransportError(f"could not connect: {str(error)[:200]}") from error

    def _write(self, frame: Mapping[str, Any]) -> None:
        try:
            self._socket.send(json.dumps(frame))
        except Exception as error:  # noqa: BLE001
            raise WebSocketTransportError(f"the connection closed: {str(error)[:200]}") from error

    def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None:
        if self._socket is None:
            raise WebSocketTransportError("the connection is not open")
        with self._lock:
            self._write(frame)
            if "id" not in frame:
                return None
            ceiling_seconds = call_ceiling_seconds(self._timeout)
            ceiling = time.monotonic() + ceiling_seconds
            deadline = time.monotonic() + self._timeout
            answered = 0
            while True:
                if time.monotonic() >= ceiling:
                    raise WebSocketTransportError(f"the MCP server kept one call open for longer than {ceiling_seconds:g}s")
                try:
                    raw = self._socket.recv(timeout=max(0.0, min(deadline, ceiling) - time.monotonic()))
                except TimeoutError:
                    if time.monotonic() < deadline:
                        continue  # woke at the call ceiling, which the check above reports
                    raise WebSocketTransportError(f"the MCP server did not answer within {self._timeout:g}s") from None
                except Exception as error:  # noqa: BLE001 - a closed connection
                    raise WebSocketTransportError(f"the connection closed: {str(error)[:200]}") from error
                try:
                    message = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("id") == frame["id"] and ("result" in message or "error" in message):
                    return message
                if "method" in message:
                    reply = dispatch_server_message(message, self.on_message)
                    if reply is not None:
                        answered += 1
                        if answered > MAX_SERVER_REQUESTS_PER_CALL:
                            raise WebSocketTransportError(
                                f"the MCP server sent more than {MAX_SERVER_REQUESTS_PER_CALL} requests while answering one call"
                            )
                        self._write(reply)
                        deadline = time.monotonic() + self._timeout

    def close(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            try:
                socket.close()
            except Exception:  # noqa: BLE001 - closing never fails a turn
                pass


__all__ = ["WebSocketTransport", "WebSocketTransportError"]

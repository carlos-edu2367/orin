"""An SDK host's in-process MCP server, reached through ``orin code`` (W6, ``mcp_message``).

The server lives inside the host program behind the CLI, so every JSON-RPC frame travels as a question for the host
(``agentic.host_requests``): the turn registers its channel under a relay key, the transport asks it, the CLI hands
the frame to the host as an ``mcp_message`` control request and relays the host's ``mcp_response`` back. A frame
with no ``id`` (a notification) is delivered and its answer ignored.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any

Ask = Callable[[str, dict[str, Any], float], "dict[str, Any] | None"]

_RELAYS: dict[str, Ask] = {}
_LOCK = threading.Lock()
DEFAULT_TIMEOUT_SECONDS = 60.0


class SdkRelayError(RuntimeError):
    """The SDK host's server could not be reached or did not answer."""


def register_relay(key: str, ask: Ask) -> None:
    with _LOCK:
        _RELAYS[key] = ask


def unregister_relay(key: str, ask: Ask | None = None) -> None:
    with _LOCK:
        if ask is None or _RELAYS.get(key) is ask:
            _RELAYS.pop(key, None)


def relay_for(key: str) -> Ask | None:
    with _LOCK:
        return _RELAYS.get(key)


class SdkRelayTransport:
    kind = "sdk"

    def __init__(self, *, server_name: str, relay_key: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._server_name = server_name
        self._relay_key = relay_key
        self._timeout = timeout
        self.on_message: Any = None  # the client's hook for a server's own requests (an SDK server sends none)

    def open(self) -> None:
        if relay_for(self._relay_key) is None:
            raise SdkRelayError("the SDK host that serves this MCP server is not connected to this turn")

    def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None:
        ask = relay_for(self._relay_key)
        if ask is None:
            raise SdkRelayError("the SDK host that serves this MCP server is not connected to this turn")
        answer = ask("mcp_message", {"server_name": self._server_name, "message": dict(frame)}, self._timeout)
        if "id" not in frame:
            return None
        if answer is None:
            raise SdkRelayError(f"the SDK host did not answer within {self._timeout:g}s")
        if "_error" in answer:
            raise SdkRelayError(str(answer["_error"])[:300])
        response = answer.get("mcp_response")
        if not isinstance(response, dict):
            raise SdkRelayError("the SDK host's answer is not a JSON-RPC message")
        return response

    def close(self) -> None:
        return None


__all__ = ["SdkRelayError", "SdkRelayTransport", "register_relay", "relay_for", "unregister_relay"]

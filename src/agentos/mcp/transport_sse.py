"""The legacy HTTP+SSE transport for a remote MCP server (protocol 2024-11-05), for servers in configuration files.

The client opens one event stream (GET); the server's first ``endpoint`` event names where to POST messages, and
every answer arrives on the stream. The endpoint must be on the stream's own origin. Only a server the person wrote
in a configuration file uses this transport (``allow_local`` lets it be local or plain http); without
``allow_local`` the address must be public HTTPS, checked again before every message.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

import httpx

from agentos.agentic.agent_tools import _public_url

from .protocol import dispatch_server_message

DEFAULT_TIMEOUT_SECONDS = 45.0
MAX_EVENT_BYTES = 4_000_000


class SseTransportError(RuntimeError):
    """The event stream or a message post failed."""


def checked_url(url: str, *, allow_local: bool, secure: str = "https://", plain: str = "http://") -> str:
    """The address, if the endpoint policy allows it: public and ``secure`` unless ``allow_local``."""
    text = str(url or "").strip()
    if allow_local:
        if not text.lower().startswith((secure, plain)):
            raise SseTransportError(f"an MCP endpoint must start with {secure} or {plain}")
        return text
    if not text.lower().startswith(secure):
        raise SseTransportError(f"an MCP endpoint must use {secure.rstrip(':/')}")
    try:
        _public_url("https://" + text[len(secure):], resolve_dns=True)
    except Exception as error:  # the policy raises its own refusal type
        raise SseTransportError(str(error)) from error
    return text


class SseTransport:
    kind = "sse"

    def __init__(self, *, url: str, headers: Mapping[str, str] | None = None, client: httpx.Client | None = None,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS, allow_local: bool = False) -> None:
        self._allow_local = allow_local
        self._url = checked_url(url, allow_local=allow_local)
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
        self._endpoint: str | None = None
        self._failure = ""
        self._messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._ready = threading.Event()
        self._ended = threading.Event()
        self._reader: threading.Thread | None = None
        self._response: httpx.Response | None = None
        self._lock = threading.Lock()
        self.on_message: Any = None  # set by the client: the server's own requests and notifications

    def open(self) -> None:
        if self._reader is not None:
            return
        if not self._allow_local:
            checked_url(self._url, allow_local=False)  # the address is checked again when the stream opens
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(self._timeout, read=None), follow_redirects=False)
        self._reader = threading.Thread(target=self._read_stream, name="orin-mcp-sse", daemon=True)
        self._reader.start()
        if not self._ready.wait(self._timeout) or self._endpoint is None:
            self.close()
            raise SseTransportError(self._failure or f"the server named no endpoint within {self._timeout:g}s")

    def _read_stream(self) -> None:
        assert self._client is not None
        size = 0
        try:
            with self._client.stream("GET", self._url, headers={"accept": "text/event-stream", **self._headers}) as response:
                self._response = response
                if response.status_code >= 400:
                    self._failure = f"the MCP endpoint answered {response.status_code}"
                    return
                name, data = "", []
                for line in response.iter_lines():
                    size += len(line)
                    if size > MAX_EVENT_BYTES * 16:
                        self._failure = "the event stream sent too much"
                        return
                    if not line.strip():
                        if data:
                            self._event(name or "message", "\n".join(data))
                        name, data = "", []
                    elif line.startswith("event:"):
                        name = line[6:].strip()
                    elif line.startswith("data:"):
                        chunk = line[5:]
                        data.append(chunk[1:] if chunk.startswith(" ") else chunk)
        except Exception as error:  # noqa: BLE001 - the next send reports it
            if not self._failure:
                self._failure = f"the event stream broke: {str(error)[:200]}"
        finally:
            self._ended.set()
            self._ready.set()
            self._messages.put(None)

    def _event(self, name: str, data: str) -> None:
        if name == "endpoint":
            endpoint = urljoin(self._url, data.strip())
            if urlsplit(endpoint)[:2] != urlsplit(self._url)[:2]:
                self._failure = "the server named an endpoint on another origin"
            else:
                self._endpoint = endpoint
            self._ready.set()
            return
        if name != "message" or len(data) > MAX_EVENT_BYTES:
            return
        try:
            message = json.loads(data)
        except json.JSONDecodeError:
            return
        if isinstance(message, dict):
            self._messages.put(message)

    def _post(self, frame: Mapping[str, Any]) -> None:
        if self._endpoint is None or self._client is None:
            raise SseTransportError("the event stream is not open")
        if not self._allow_local:
            checked_url(self._endpoint, allow_local=False)
        try:
            response = self._client.post(self._endpoint, json=dict(frame), headers={"content-type": "application/json", **self._headers},
                                         timeout=self._timeout)
        except httpx.HTTPError as error:
            raise SseTransportError(f"the MCP endpoint did not answer: {error}") from error
        if response.status_code >= 400:
            raise SseTransportError(f"the MCP endpoint answered {response.status_code}")

    def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            self._post(frame)
            if "id" not in frame:
                return None
            deadline = time.monotonic() + self._timeout
            while True:
                try:
                    message = self._messages.get(timeout=max(0.0, deadline - time.monotonic()))
                except queue.Empty:
                    raise SseTransportError(f"the MCP server did not answer within {self._timeout:g}s") from None
                if message is None:
                    self._messages.put(None)  # every later send sees the end too
                    raise SseTransportError(self._failure or "the event stream closed")
                if message.get("id") == frame["id"] and ("result" in message or "error" in message):
                    return message
                if "method" in message:
                    reply = dispatch_server_message(message, self.on_message)
                    if reply is not None:
                        self._post(reply)
                        deadline = time.monotonic() + self._timeout

    def close(self) -> None:
        response, self._response = self._response, None
        if response is not None:
            try:
                response.close()
            except Exception:  # noqa: BLE001 - closing never fails a turn
                pass
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None


__all__ = ["SseTransport", "SseTransportError", "checked_url"]

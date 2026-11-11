"""Streamable-HTTP transport for a remote MCP server.

Endpoint policy is the one the agent's own fetch_url already enforces: public
HTTPS only. A private, loopback or link-local endpoint is refused before the
first byte leaves the machine.

The policy check re-resolves DNS on every call, not only once at construction:
a server the caller does not control the DNS record for could otherwise pass
validation once and then repoint its domain at a private or loopback address
for the connection httpx actually opens (a DNS-rebinding race). Re-checking
immediately before each request closes the "validate once, reuse forever" gap;
it does not eliminate the much narrower race against httpx's own resolution a
few milliseconds later, which needs a pinned-IP connection to close fully.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Mapping
from urllib.parse import urlsplit

import httpx

from agentos.agentic.agent_tools import _public_url

from .file_servers import redact_secret_values
from .protocol import dispatch_server_message

if TYPE_CHECKING:
    from .token_source import TokenSource

DEFAULT_TIMEOUT_SECONDS = 45.0
MAX_RESPONSE_BYTES = 4_000_000
MAX_REDIRECTS = 3
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


class HttpTransportError(RuntimeError):
    """The server was reachable but the exchange failed."""


class HttpTransportRefused(HttpTransportError):
    """The endpoint is not allowed by the network policy."""


class HttpUnauthorized(HttpTransportError):
    """The endpoint answered 401 and there is no sign-in to present."""

    def __init__(self, www_authenticate: str | None) -> None:
        super().__init__("the MCP endpoint requires sign-in (401)")
        self.www_authenticate = www_authenticate


def _same_origin(first_url: str, second_url: str) -> tuple[bool, str]:
    first = urlsplit(first_url)
    second = urlsplit(second_url)
    first_scheme = first.scheme.lower()
    second_scheme = second.scheme.lower()
    first_host = (first.hostname or "").lower()
    second_host = (second.hostname or "").lower()
    first_port = first.port or (443 if first_scheme == "https" else 80 if first_scheme == "http" else None)
    second_port = second.port or (443 if second_scheme == "https" else 80 if second_scheme == "http" else None)
    return (first_scheme, first_host, first_port) == (second_scheme, second_host, second_port), (second.hostname or second_url)


class HttpTransport:
    kind = "http"

    def __init__(self, *, url: str, headers: Mapping[str, str] | None = None, token_source: "TokenSource | None" = None,
                 client: httpx.Client | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS, allow_local: bool = False) -> None:
        self._allow_local = allow_local
        self._url = self._checked(url)
        self._headers = dict(headers or {})
        self._token_source = token_source
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        # Set by the client: receives the server's own requests and notifications (W5).
        self.on_message: Any = None

    def _checked(self, url: str) -> str:
        if self._allow_local:
            # W5: a server the person wrote in a configuration file may be local or plain http (a development server).
            if not str(url).lower().startswith(("http://", "https://")):
                raise HttpTransportRefused("an MCP endpoint must use http or https")
            return str(url)
        try:
            normalized = _public_url(url, resolve_dns=True)
        except Exception as error:  # the policy raises its own refusal type
            raise HttpTransportRefused(str(error)) from error
        if not normalized.lower().startswith("https://"):
            raise HttpTransportRefused("an MCP endpoint must use https")
        return normalized

    def open(self) -> None:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout, follow_redirects=False)

    def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None:
        # Re-validate immediately before every request; see the module
        # docstring for why a one-time check at construction is not enough.
        self._checked(self._url)
        self.open()
        token = self._token_source.current() if self._token_source is not None else None
        response = self._post(frame, token)
        try:
            if response.status_code == 401:
                if self._token_source is None or token is None:
                    raise HttpUnauthorized(response.headers.get("www-authenticate"))
                # One renewal and one retry: a token the server rejects right after
                # renewing means the grant itself is gone.
                response.close()
                token = self._token_source.force_refresh(token)
                response = self._post(frame, token)
                if response.status_code == 401:
                    self._token_source.invalidate("Reconexão necessária: o servidor recusou o acesso renovado")
            self.session_id = response.headers.get("mcp-session-id") or self.session_id
            if response.status_code >= 400:
                raise HttpTransportError(f"the MCP endpoint answered {response.status_code}")
            if "id" not in frame:
                return None
            if "text/event-stream" in response.headers.get("content-type", ""):
                parsed = self._answer_from_events(response, frame.get("id"), token)
                self._record_negotiated_version(frame, parsed)
                return parsed
            body = _bounded_body(response)
        finally:
            response.close()
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as error:
            raise HttpTransportError("the MCP endpoint answered with invalid JSON") from error
        self._record_negotiated_version(frame, parsed)
        return parsed

    def _record_negotiated_version(self, frame: Mapping[str, Any], parsed: Any) -> None:
        if frame.get("method") == "initialize" and isinstance(parsed, Mapping):
            result = parsed.get("result")
            if isinstance(result, Mapping):
                version = result.get("protocolVersion")
                if isinstance(version, str) and version.strip():
                    self.protocol_version = version.strip()

    def _redact(self, text: str, token: str | None = None) -> str:
        values = list(self._headers.values())
        if token:
            values.append(token)
        return redact_secret_values(text, values, url=self._url)

    def _answer_from_events(self, response: httpx.Response, request_id: Any, token: str | None) -> dict[str, Any]:
        """Read the event stream until the answer to ``request_id``, answering the server's own requests on the way."""
        fallback: dict[str, Any] | None = None
        size = 0
        try:
            for data in _events(response):
                size += len(data)
                if size > MAX_RESPONSE_BYTES:
                    raise HttpTransportError("the event stream is larger than 4 MB")
                try:
                    message = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, Mapping):
                    continue
                if message.get("id") == request_id and ("result" in message or "error" in message):
                    return dict(message)
                if "method" in message:
                    reply = dispatch_server_message(message, self.on_message)
                    if reply is not None:
                        self._post_reply(reply, token)
                elif fallback is None and ("result" in message or "error" in message):
                    fallback = dict(message)
        except httpx.HTTPError as error:
            raise HttpTransportError(self._redact(f"the event stream broke: {error}", token)) from error
        if fallback is not None:
            return fallback
        raise HttpTransportError("the event stream carried no answer")

    def _post_reply(self, reply: Mapping[str, Any], token: str | None) -> None:
        response = self._post(reply, token)
        try:
            if response.status_code >= 400:
                raise HttpTransportError(f"the MCP endpoint refused our answer ({response.status_code})")
        finally:
            response.close()

    def _post(self, frame: Mapping[str, Any], token: str | None) -> httpx.Response:
        assert self._client is not None
        headers = {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            **self._headers,
        }
        if token:
            headers["authorization"] = f"Bearer {token}"
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        if self.protocol_version and frame.get("method") != "initialize":
            headers["mcp-protocol-version"] = self.protocol_version
        current_url = self._url
        for hop in range(MAX_REDIRECTS + 1):
            try:
                request = self._client.build_request("POST", current_url, json=dict(frame), headers=headers, timeout=self._timeout)
                response = self._client.send(request, stream=True)
            except httpx.HTTPError as error:
                raise HttpTransportError(self._redact(f"the MCP endpoint did not answer: {error}", token)) from error
            if response.status_code not in REDIRECT_STATUS_CODES:
                return response
            location = response.headers.get("location")
            if not location:
                return response
            response.close()
            if hop >= MAX_REDIRECTS:
                raise HttpTransportError(f"the MCP endpoint redirected more than {MAX_REDIRECTS} times")
            target_url = str(httpx.URL(current_url).join(location))
            same_host, target_host = _same_origin(current_url, target_url)
            if not same_host:
                raise HttpTransportRefused(
                    f"the MCP endpoint redirected to another host ({target_host}), which is not allowed"
                )
            current_url = self._checked(target_url)
        raise HttpTransportError(f"the MCP endpoint redirected more than {MAX_REDIRECTS} times")

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
        self._client = None


def _bounded_body(response: httpx.Response) -> str:
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= MAX_RESPONSE_BYTES:
            break
    return b"".join(chunks)[:MAX_RESPONSE_BYTES].decode("utf-8", "replace")


def _events(response: httpx.Response):
    """Each server-sent event's data (its ``data:`` lines joined), as it arrives."""
    lines: list[str] = []
    for line in response.iter_lines():
        if not line.strip():
            if lines:
                yield "\n".join(lines)
                lines = []
            continue
        if line.startswith("data:"):
            chunk = line[5:]
            lines.append(chunk[1:] if chunk.startswith(" ") else chunk)
    if lines:
        yield "\n".join(lines)



__all__ = ["HttpTransport", "HttpTransportError", "HttpTransportRefused", "HttpUnauthorized"]

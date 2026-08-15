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
from typing import Any, Mapping

import httpx

from agentos.agentic.agent_tools import _public_url

DEFAULT_TIMEOUT_SECONDS = 45.0
MAX_RESPONSE_BYTES = 4_000_000


class HttpTransportRefused(RuntimeError):
    """The endpoint is not allowed by the network policy."""


class HttpTransportError(RuntimeError):
    """The server was reachable but the exchange failed."""


class HttpTransport:
    kind = "http"

    def __init__(self, *, url: str, headers: Mapping[str, str],
                 client: httpx.Client | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._url = self._checked(url)
        self._headers = dict(headers)
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
        self.session_id: str | None = None

    @staticmethod
    def _checked(url: str) -> str:
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
        assert self._client is not None
        headers = {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            **self._headers,
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        try:
            response = self._client.post(self._url, json=dict(frame), headers=headers, timeout=self._timeout)
        except httpx.HTTPError as error:
            raise HttpTransportError(f"the MCP endpoint did not answer: {error}") from error
        self.session_id = response.headers.get("mcp-session-id") or self.session_id
        if response.status_code >= 400:
            raise HttpTransportError(f"the MCP endpoint answered {response.status_code}")
        if "id" not in frame:
            return None
        body = response.content[:MAX_RESPONSE_BYTES].decode("utf-8", "replace")
        if "text/event-stream" in response.headers.get("content-type", ""):
            body = _first_sse_payload(body)
        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise HttpTransportError("the MCP endpoint answered with invalid JSON") from error

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
        self._client = None


def _first_sse_payload(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("data:"):
            return line[5:].strip()
    raise HttpTransportError("the event stream carried no data frame")


__all__ = ["HttpTransport", "HttpTransportError", "HttpTransportRefused"]

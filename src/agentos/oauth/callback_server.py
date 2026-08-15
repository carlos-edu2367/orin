"""A single-use HTTP listener on 127.0.0.1 for the OAuth redirect.

Bound before the authorization URL is built (so the exact port is known and
can be embedded in ``redirect_uri``), it serves exactly one request — the
provider's redirect carrying ``code``/``state`` or ``error`` — then stops.
Binding to 127.0.0.1 is what keeps this loopback-only: nothing off-host can
reach it.
"""
from __future__ import annotations

import http.server
from urllib.parse import parse_qs, urlparse

from .flow import OAuthFlowError

_RESPONSE_BODY = b"<html><body>You may close this window and return to the app.</body></html>"


class CallbackTimeout(RuntimeError):
    """No redirect arrived before the configured timeout."""


class LoopbackCallbackServer:
    def __init__(self, *, path: str = "/callback", timeout: float = 300.0) -> None:
        self._path = path
        self._timeout = timeout
        self._result: dict[str, str | None] | None = None
        self._httpd = http.server.HTTPServer(("127.0.0.1", 0), self._handler_class())
        self._httpd.timeout = timeout

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}{self._path}"

    def _handler_class(self) -> type[http.server.BaseHTTPRequestHandler]:
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802 - required name by http.server
                parsed = urlparse(self.path)
                if parsed.path != server._path:
                    self.send_response(404)
                    self.end_headers()
                    return
                query = parse_qs(parsed.query)
                server._result = {
                    "code": query.get("code", [None])[0],
                    "state": query.get("state", [None])[0],
                    "error": query.get("error", [None])[0],
                }
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_RESPONSE_BODY)

        return Handler

    def wait_for_code(self, *, expected_state: str) -> str:
        self._httpd.handle_request()
        if self._result is None:
            raise CallbackTimeout(f"no OAuth callback arrived within {self._timeout}s")
        if self._result["error"]:
            raise OAuthFlowError(f"authorization was denied: {self._result['error']}")
        if self._result["state"] != expected_state:
            raise OAuthFlowError("the callback state did not match the request — possible CSRF")
        code = self._result["code"]
        if not code:
            raise OAuthFlowError("the callback carried no authorization code")
        return code

    def close(self) -> None:
        self._httpd.server_close()


__all__ = ["CallbackTimeout", "LoopbackCallbackServer"]

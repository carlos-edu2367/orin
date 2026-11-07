"""Preview tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
from urllib.parse import urlsplit, urlunsplit

from agentos.agentic.tools._shared import AgentToolError, ToolOutcome, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec

# Dev servers that hot-reload the page themselves: the Bancada skips its own auto-reload for them.
_HMR_COMMAND = re.compile(
    r"\b(vite|next|nuxt|astro|webpack(?:-dev-server)?|react-scripts|vue-cli-service|svelte-kit|"
    r"ng\s+serve|parcel|remix|gatsby|expo|rsbuild|rspack)\b",
    re.IGNORECASE,
)
_LAN_NETWORKS = tuple(
    ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)
_MAX_URL_CHARS = 2048


def _has_hmr(command: object) -> bool:
    return isinstance(command, str) and bool(_HMR_COMMAND.search(command))


def _is_allowed_preview_host(host: str) -> bool:
    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    if address.is_loopback or address.is_unspecified:
        return True
    return any(address in network for network in _LAN_NETWORKS if network.version == address.version)


def _has_control_chars(text: str) -> bool:
    return any(ord(ch) <= 0x1F or ord(ch) == 0x7F for ch in text)


def normalize_preview_url(url: object, path: object = None) -> str:
    """Validate a URL the Bancada may show: http(s) on this machine or on the private LAN only."""
    if not isinstance(url, str) or not url.strip():
        raise AgentToolError("PreviewOpen needs a url such as http://localhost:5173")
    raw = url.strip()
    if len(raw) > _MAX_URL_CHARS or _has_control_chars(raw):
        raise AgentToolError("preview url is too long or contains control characters")
    if "://" not in raw and not raw.lower().startswith(("javascript:", "data:", "file:", "vbscript:")):
        raw = f"http://{raw}"
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as error:
        raise AgentToolError(f"invalid preview url: {error}") from error
    if parts.scheme.lower() not in {"http", "https"}:
        raise AgentToolError("preview url must use http:// or https://")
    if parts.username is not None or parts.password is not None:
        raise AgentToolError("preview url must not contain credentials")
    host = (parts.hostname or "").rstrip(".")
    if not host or not _is_allowed_preview_host(host):
        raise AgentToolError(
            f"preview url host '{host or '?'}' is not allowed: use localhost, 127.0.0.0/8, ::1, 0.0.0.0 "
            "or a private LAN address (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)"
        )
    if host == "0.0.0.0":  # bind-all address: the browser reaches it as this machine
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    url_path, query, fragment = parts.path or "/", parts.query, parts.fragment
    if path is not None and str(path).strip():
        extra = str(path).strip()
        if "://" in extra or extra.startswith(("//", "\\")) or "\\" in extra or _has_control_chars(extra):
            raise AgentToolError("preview path must be a path on the same server, e.g. /settings")
        extra_parts = urlsplit("/" + extra.lstrip("/"))
        url_path, query, fragment = extra_parts.path, extra_parts.query, extra_parts.fragment
    return urlunsplit((parts.scheme.lower(), netloc, url_path, query, fragment))


def _wait_until_reachable(url: str, timeout: float) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            infos = []
        for family, kind, proto, _canon, address in infos:
            try:
                with socket.socket(family, kind, proto) as probe:
                    probe.settimeout(0.3)
                    probe.connect(address)
                    return True
            except OSError:
                continue
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


class PreviewTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def preview_start(
        self,
        name: str | None = None,
        wait_timeout: float = 10.0,
    ) -> ToolOutcome:
        from agentos.code_mode.preview_server import PreviewServerError, PreviewServerManager

        manager = PreviewServerManager(self.workspace.root, browser=self._browser)
        try:
            result = manager.start_server(name=name, wait_timeout=float(wait_timeout))
        except PreviewServerError as error:
            raise AgentToolError(str(error)) from error
        summary = f"Preview server iniciado: {result.get('name')} ({result.get('url')})"
        # The activity payload drives the Bancada (it opens on Preview with this URL); logs stay in the content.
        preview = {
            **{k: v for k, v in result.items() if k not in {"logs_tail", "browser"}},
            "hmr": _has_hmr(result.get("command")),
        }
        return ToolOutcome(
            "succeeded",
            summary[:240],
            json.dumps(result, ensure_ascii=False, indent=2),
            {"tool_kind": "terminal", "preview": preview},
        )

    def preview_open(
        self,
        url: str,
        path: str | None = None,
        wait_timeout: float = 5.0,
    ) -> ToolOutcome:
        """Show an already running local dev server in the user's Bancada. UI only: it starts nothing."""
        target = normalize_preview_url(url, path)
        try:
            timeout = min(max(float(wait_timeout), 0.0), 30.0)
        except (TypeError, ValueError):
            timeout = 5.0
        if not _wait_until_reachable(target, timeout):
            raise AgentToolError(
                f"nothing is listening at {target}: start the dev server first (Bash with run_in_background, "
                "or PreviewStart), then call PreviewOpen again"
            )
        preview = {"url": target, "name": urlsplit(target).netloc, "status": "running", "ready": True}
        return ToolOutcome(
            "succeeded",
            f"Prévia aberta na Bancada: {target}"[:240],
            json.dumps({**preview, "shown_in": "bancada"}, ensure_ascii=False, indent=2),
            {"tool_kind": "terminal", "preview": preview},
        )

    def preview_stop(
        self,
        name: str | None = None,
    ) -> ToolOutcome:
        from agentos.code_mode.preview_server import PreviewServerError, PreviewServerManager

        manager = PreviewServerManager(self.workspace.root, browser=self._browser)
        try:
            result = manager.stop_server(name=name)
        except PreviewServerError as error:
            raise AgentToolError(str(error)) from error
        summary = f"Preview server encerrado: {result.get('name') or 'default'}"
        return ToolOutcome(
            "succeeded",
            summary[:240],
            json.dumps(result, ensure_ascii=False, indent=2),
            {"tool_kind": "terminal", "preview": result},
        )

    def preview_logs(
        self,
        name: str | None = None,
        tail_lines: int = 100,
    ) -> ToolOutcome:
        from agentos.code_mode.preview_server import PreviewServerError, PreviewServerManager

        manager = PreviewServerManager(self.workspace.root, browser=self._browser)
        try:
            result = manager.read_logs(name=name, tail_lines=int(tail_lines))
        except PreviewServerError as error:
            raise AgentToolError(str(error)) from error
        summary = f"Logs do preview server: {result.get('name') or 'default'}"
        return ToolOutcome(
            "succeeded",
            summary[:240],
            json.dumps(result, ensure_ascii=False, indent=2),
            {"tool_kind": "terminal", "preview": {k: v for k, v in result.items() if k != "logs"}},
        )


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="preview_start",
        description=(
            "Start a local development/preview server from .orin/launch.json (or auto-detected from package.json / "
            "pyproject.toml) and wait for its port. The running app opens automatically in the user's Bancada "
            "(Preview tab), so use it after building or changing a frontend to let the user see the result live. "
            "For a server you already started another way (e.g. Bash in background), call PreviewOpen with its URL."
        ),
        legacy_schema=_schema({
            "name": {**_TEXT, "description": "Optional configuration name from launch.json. Uses the first configuration when omitted."},
            "wait_timeout": {"type": "number", "minimum": 0.1, "maximum": 60.0, "description": "Seconds to wait for the server port to open. Defaults to 10."},
        }),
        handler="preview_start",
        kind="terminal",
        policy_tags=("mutates",),
        available=lambda self: (self._enable_terminal),
        order=300,
    ),
    ToolSpec(
        key="preview_open",
        description=(
            "Show an already running local dev server (e.g. one you started with Bash in background) in the user's "
            "Bancada Preview tab so they see the frontend live; it starts nothing. Call it once the server is "
            "listening, after starting or changing a frontend. Accepts only http(s) URLs on localhost, 127.0.0.0/8, "
            "::1, 0.0.0.0 or a private LAN address."
        ),
        legacy_schema=_schema({
            "url": {**_TEXT, "description": "URL of the running server, e.g. http://localhost:5173."},
            "path": {**_TEXT, "description": "Optional path to show on that server, e.g. /settings."},
            "wait_timeout": {"type": "number", "minimum": 0, "maximum": 30.0, "description": "Seconds to wait for the server to accept connections. Defaults to 5."},
        }, ("url",)),
        handler="preview_open",
        kind="terminal",
        read_only=True,
        available=lambda self: (self._enable_terminal),
        order=305,
    ),
    ToolSpec(
        key="preview_stop",
        description="Stop a running local preview server started with preview_start.",
        legacy_schema=_schema({
            "name": {**_TEXT, "description": "Optional server configuration name to stop. Stops all tracked preview servers when omitted."},
        }),
        handler="preview_stop",
        kind="terminal",
        policy_tags=("mutates",),
        available=lambda self: (self._enable_terminal),
        order=310,
    ),
    ToolSpec(
        key="preview_logs",
        description="Read recent stdout/stderr logs from a local preview server started with preview_start.",
        legacy_schema=_schema({
            "name": {**_TEXT, "description": "Optional server configuration name."},
            "tail_lines": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Number of trailing log lines to return. Defaults to 100."},
        }),
        handler="preview_logs",
        kind="terminal",
        read_only=True,
        available=lambda self: (self._enable_terminal),
        order=320,
    ),
)

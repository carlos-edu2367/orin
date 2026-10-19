"""Connect the MCP servers written in files (W5): tool bundles for the turn, with their tools listed once and cached.

Listing a server's tools means starting it (or calling it) once; the answer is kept per exact configuration for
``DISCOVERY_TTL_SECONDS`` (a failure for ``FAILURE_TTL_SECONDS``), so a turn after the first one starts at once.
Servers are listed in parallel and the turn waits at most ``MCP_TIMEOUT`` milliseconds (default 30 s) for them; one
that answers later is ready for the next turn. A failure's text never carries the values of the server's
environment, headers or URL query (they are often credentials).
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, wait

from .file_config import FileServer, McpConfigError
from .models import McpServerConfig, McpServerState, McpToolDescriptor, McpTransport, slugify
from .toolset import ServerBundle, discover

DISCOVERY_TTL_SECONDS = 600.0
FAILURE_TTL_SECONDS = 60.0
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_CACHE_ENTRIES = 256
MAX_PARALLEL_DISCOVERY = 8

_cache: dict[str, tuple[float, tuple[McpToolDescriptor, ...], str]] = {}
_cache_lock = threading.Lock()


def clear_discovery_cache() -> None:
    with _cache_lock:
        _cache.clear()


def discovery_timeout(environ: Mapping[str, str] | None = None) -> float:
    raw = (os.environ if environ is None else environ).get("MCP_TIMEOUT", "")
    try:
        return max(1.0, min(float(raw) / 1000.0, 120.0)) if str(raw).strip() else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def file_bundle(server: FileServer, *, user_id: str) -> ServerBundle:
    """The server as a tool bundle (no tools yet). Its environment or headers travel as its secrets."""
    if server.transport not in ("stdio", "http"):
        raise McpConfigError(f"the {server.transport} transport is not supported yet")
    slug = slugify(server.name)
    stdio = server.transport == "stdio"
    config = McpServerConfig(
        server_id=f"file:{server.scope}:{slug}", user_id=user_id, slug=slug, display_name=server.name,
        transport=McpTransport.STDIO if stdio else McpTransport.HTTP, command=server.command or None, args=server.args,
        url=server.url or None, state=McpServerState.ACTIVE, origin=server.scope, cwd=server.cwd,
    )
    secrets = dict(server.env) if stdio else {f"header:{name}": value for name, value in server.headers}
    return config, (), secrets


def _redacted(text: str, server: FileServer) -> str:
    values = [value for _, value in (*server.env, *server.headers)]
    if "?" in server.url:
        values.append(server.url.split("?", 1)[1])
    for value in sorted(values, key=len, reverse=True):
        if len(value) >= 4:
            text = text.replace(value, "***")
    return text


def _cached(key: str, now: float) -> tuple[tuple[McpToolDescriptor, ...], str] | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        stored_at, tools, error = entry
        if now - stored_at > (FAILURE_TTL_SECONDS if error else DISCOVERY_TTL_SECONDS):
            _cache.pop(key, None)
            return None
        return tools, error


def _store(key: str, tools: tuple[McpToolDescriptor, ...], error: str) -> None:
    with _cache_lock:
        if len(_cache) >= MAX_CACHE_ENTRIES:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic(), tools, error)


def _discover_one(server: FileServer, config: McpServerConfig, secrets: Mapping[str, str], connect: Callable) -> tuple[tuple[McpToolDescriptor, ...], str]:
    try:
        _version, tools = connect(config, secrets)
        result: tuple[tuple[McpToolDescriptor, ...], str] = (tuple(tools), "")
    except Exception as error:  # noqa: BLE001 - any failure is reported for this server only
        result = ((), _redacted(f"could not connect: {str(error) or type(error).__name__}", server)[:300])
    _store(server.fingerprint(), *result)
    return result


def connect_file_servers(servers: Iterable[FileServer], *, user_id: str, connect: Callable = discover,
                         timeout: float | None = None) -> tuple[list[ServerBundle], list[tuple[str, str]]]:
    """``(bundles with their tools, [(server name, why it is not available)])``, in the servers' order."""
    ready: dict[str, ServerBundle] = {}
    failures: list[tuple[str, str]] = []
    todo: list[tuple[FileServer, McpServerConfig, Mapping[str, str]]] = []
    order: list[str] = []
    now = time.monotonic()
    for server in servers:
        try:
            config, _, secrets = file_bundle(server, user_id=user_id)
        except (McpConfigError, ValueError) as error:
            failures.append((server.name, str(error)[:300]))
            continue
        order.append(server.name)
        cached = _cached(server.fingerprint(), now)
        if cached is None:
            todo.append((server, config, secrets))
        elif cached[1]:
            failures.append((server.name, cached[1]))
        else:
            ready[server.name] = (config, cached[0], secrets)
    if todo:
        limit = discovery_timeout() if timeout is None else timeout
        pool = ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_DISCOVERY, len(todo)), thread_name_prefix="orin-mcp-discovery")
        try:
            futures = {pool.submit(_discover_one, server, config, secrets, connect): (server, config, secrets) for server, config, secrets in todo}
            done, _late = wait(futures, timeout=limit)
        finally:
            pool.shutdown(wait=False)  # a late server keeps starting in the background and is cached for the next turn
        for future, (server, config, secrets) in futures.items():
            if future not in done:
                failures.append((server.name, f"did not answer within {limit:g} s; it is tried again next turn"))
                continue
            tools, error = future.result()
            if error:
                failures.append((server.name, error))
            else:
                ready[server.name] = (config, tools, secrets)
    return [ready[name] for name in order if name in ready], failures


__all__ = [
    "DISCOVERY_TTL_SECONDS", "FAILURE_TTL_SECONDS", "clear_discovery_cache", "connect_file_servers", "discovery_timeout",
    "file_bundle",
]

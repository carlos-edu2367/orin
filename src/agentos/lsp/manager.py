"""The language servers of one turn (W5): each starts on the first file it handles and is turned off if it fails."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .client import LspClient, LspError
from .config import LspServerConfig

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 1_000_000
MAX_SHOWN = 30
_SEVERITY = {1: "error", 2: "warning", 3: "info", 4: "hint"}


def format_diagnostics(label: str, server: str, diagnostics: Sequence[Mapping[str, Any]]) -> str | None:
    """The errors and warnings as reminder text, or None when there are none."""
    shown: list[str] = []
    for item in diagnostics:
        severity = item.get("severity", 1)
        if severity not in (1, 2):
            continue
        start = (item.get("range") or {}).get("start") or {}
        code = f" [{item['code']}]" if item.get("code") not in (None, "") else ""
        message = " ".join(str(item.get("message") or "").split())[:300]
        shown.append(f"- line {int(start.get('line', 0)) + 1}, column {int(start.get('character', 0)) + 1} · {_SEVERITY[severity]} · {message}{code}")
    if not shown:
        return None
    more = f"\n- ... and {len(shown) - MAX_SHOWN} more" if len(shown) > MAX_SHOWN else ""
    return f"Diagnostics from the language server {server} for {label} after your change:\n" + "\n".join(shown[:MAX_SHOWN]) + more


class LspManager:
    def __init__(self, configs: Sequence[LspServerConfig], *, workspace_root: Path, timeout: float = 5.0,
                 client_factory: Callable[..., Any] = LspClient) -> None:
        self._configs = list(configs)
        self._root = Path(workspace_root)
        self._timeout = timeout
        self._client_factory = client_factory
        self._clients: dict[str, Any] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._failed: dict[str, str] = {}
        self._guard = threading.Lock()
        self._closed = False  # set by close(): no server starts afterwards, and one starting meanwhile is stopped

    def server_for(self, path: Path) -> tuple[LspServerConfig, str] | None:
        for config in self._configs:
            language = config.language_for(path)
            if language is not None:
                return config, language
        return None

    def check(self, path: Path) -> tuple[str, list[dict[str, Any]] | None, str]:
        """``(server name, diagnostics or None, why the server failed)``; a failed server is not tried again."""
        found = self.server_for(path)
        if found is None:
            return "", None, ""
        config, language = found
        with self._guard:
            if self._closed or config.name in self._failed:
                return config.name, None, ""
            lock = self._locks.setdefault(config.name, threading.Lock())
        with lock:
            try:
                target = Path(path)
                if target.stat().st_size > MAX_FILE_BYTES:
                    return config.name, None, ""
                text = target.read_text(encoding="utf-8", errors="replace")
                client = self._clients.get(config.name)
                if client is None:
                    client = self._client_factory(config, workspace_root=self._root)
                    with self._guard:
                        if self._closed:
                            return config.name, None, ""
                        self._clients[config.name] = client
                    client.start()
                    with self._guard:
                        closed_meanwhile = self._closed
                    if closed_meanwhile:  # the turn ended while the server was starting: stop it now
                        self._clients.pop(config.name, None)
                        client.close()
                        return config.name, None, ""
                return config.name, client.diagnostics(target, text, language, timeout=self._timeout), ""
            except (LspError, OSError) as error:
                reason = str(error)[:300] or type(error).__name__
                with self._guard:
                    self._failed[config.name] = reason
                broken = self._clients.pop(config.name, None)
                if broken is not None:
                    try:
                        broken.close()
                    except Exception:  # noqa: BLE001 - closing never fails a turn
                        pass
                return config.name, None, reason

    def close(self) -> None:
        with self._guard:
            self._closed = True
            clients, self._clients = list(self._clients.values()), {}
        for client in clients:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - closing never fails a turn
                logger.debug("a language server did not stop cleanly", exc_info=True)


__all__ = ["LspManager", "format_diagnostics"]

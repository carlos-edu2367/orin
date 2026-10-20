"""A small Language Server Protocol client (W5): open or change a file, wait for its diagnostics.

Speaks JSON-RPC over the server's stdin/stdout with ``Content-Length`` framing. The server's own requests are
answered (``workspace/configuration`` with the plugin's ``settings``, capability registration and progress with
an empty result; anything else "method not found"). A server that does not answer in time is stopped; the caller
then turns it off for the rest of the turn.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import subprocess
import threading
from collections.abc import Mapping
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from agentos.agentic.agent_tools import _terminate_process_tree

from .config import LspServerConfig

logger = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 8_000_000
SETTLE_SECONDS = 0.3
STDERR_TAIL_BYTES = 4096  # W5-D3: the end of the server's error output, for the notice when it fails


class LspError(RuntimeError):
    """The language server failed to start, answer or stay alive."""


def _environment(config: LspServerConfig) -> dict[str, str]:
    """The worker's environment without Orin's own variables or anything that looks like a credential, plus the
    server's own ``env``."""
    hidden = ("ORIN_", "AGENTOS_")
    secret_words = ("API_KEY", "SECRET", "TOKEN", "PASSWORD")
    environment = {
        name: value for name, value in os.environ.items()
        if not name.upper().startswith(hidden) and not any(word in name.upper() for word in secret_words)
    }
    environment.update(dict(config.env))
    return environment


class LspClient:
    def __init__(self, config: LspServerConfig, *, workspace_root: Path) -> None:
        self.config = config
        self.root = Path(workspace_root)
        self._process: subprocess.Popen[bytes] | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, Future] = {}
        # None: the server's last publish for the file was malformed (W5-D3)
        self._diagnostics: dict[str, list[dict[str, Any]] | None] = {}
        self._published: dict[str, threading.Event] = {}
        self._versions: dict[str, int] = {}
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._closed = False
        self._stderr = bytearray()
        self._stderr_thread: threading.Thread | None = None

    # -- process and framing ----------------------------------------------------

    def start(self) -> None:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self._process = subprocess.Popen(
                [self.config.command, *self.config.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=str(self.root), env=_environment(self.config), shell=False,
                creationflags=creationflags,
            )
        except OSError as error:
            raise LspError(f"could not start {self.config.command}: {error}") from error
        threading.Thread(target=self._read_loop, name=f"orin-lsp-{self.config.name}", daemon=True).start()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, name=f"orin-lsp-err-{self.config.name}", daemon=True)
        self._stderr_thread.start()
        result = self._request("initialize", {
            "processId": os.getpid(), "rootUri": self.root.as_uri(), "rootPath": str(self.root),
            "workspaceFolders": [{"uri": self.root.as_uri(), "name": self.root.name or "workspace"}],
            "capabilities": {
                "textDocument": {"publishDiagnostics": {"relatedInformation": False}, "synchronization": {"didSave": True}},
                "workspace": {"configuration": True, "workspaceFolders": True},
            },
            "initializationOptions": self.config.initialization_options,
        }, timeout=self.config.startup_timeout)
        if not isinstance(result, Mapping):
            raise LspError("the language server did not initialize")
        self._notify("initialized", {})
        if self.config.settings is not None:
            self._notify("workspace/didChangeConfiguration", {"settings": self.config.settings})

    def _drain_stderr(self) -> None:
        process = self._process
        stream = process.stderr if process is not None else None
        if stream is None:
            return
        reader = getattr(stream, "read1", None) or stream.read
        try:
            while True:
                chunk = reader(4096)
                if not chunk:
                    return
                with self._state_lock:
                    self._stderr.extend(chunk)
                    del self._stderr[:-STDERR_TAIL_BYTES]
        except (OSError, ValueError):
            return
        finally:
            try:
                stream.close()
            except (OSError, ValueError):
                pass

    def stderr_tail(self) -> str:
        """The last few KB the server wrote to its error output (W5-D3)."""
        process, thread = self._process, self._stderr_thread
        if thread is not None and (process is None or process.poll() is not None):
            thread.join(0.5)  # the server exited: let the last of its output arrive
        with self._state_lock:
            data = bytes(self._stderr)
        return data.decode("utf-8", errors="replace").replace("\x00", "").strip()

    def _send(self, message: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise LspError("the language server is not running")
        body = json.dumps(message).encode("utf-8")
        with self._write_lock:
            try:
                process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
                process.stdin.flush()
            except OSError as error:
                raise LspError(f"the language server closed its input: {error}") from error

    def _notify(self, method: str, params: Mapping[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": dict(params)})

    def _request(self, method: str, params: Mapping[str, Any], *, timeout: float) -> Any:
        request_id = next(self._ids)
        future: Future = Future()
        with self._state_lock:
            self._pending[request_id] = future
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)})
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            raise LspError(f"the language server did not answer {method} within {timeout:g}s") from None
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

    def _read_loop(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        stream = process.stdout
        try:
            while True:
                length = None
                while True:
                    line = stream.readline(4096)
                    if not line:
                        return
                    text = line.decode("ascii", errors="replace").strip()
                    if not text:
                        break
                    name, _, value = text.partition(":")
                    if name.strip().lower() == "content-length":
                        length = int(value.strip())
                if length is None or length < 0 or length > MAX_MESSAGE_BYTES:
                    return
                body = stream.read(length)
                try:
                    message = json.loads(body.decode("utf-8"))
                except ValueError:
                    continue
                if isinstance(message, dict):
                    try:
                        self._dispatch(message)
                    except Exception:  # noqa: BLE001 - one malformed message from a server never stops the reader
                        logger.debug("a language server sent a message Orin could not read", exc_info=True)
        except (OSError, ValueError):
            return
        finally:
            with self._state_lock:
                pending = list(self._pending.values())
            for future in pending:
                if not future.done():
                    future.set_exception(LspError("the language server stopped"))

    def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method is None:
            with self._state_lock:
                future = self._pending.get(message.get("id"))  # type: ignore[arg-type]
            if future is not None and not future.done():
                if "error" in message:
                    future.set_exception(LspError(str((message.get("error") or {}).get("message") or "error")[:300]))
                else:
                    future.set_result(message.get("result"))
            return
        params = message.get("params") if isinstance(message.get("params"), Mapping) else {}
        if method == "textDocument/publishDiagnostics":
            uri = str(params.get("uri") or "")
            raw = params.get("diagnostics")
            version = params.get("version")
            with self._state_lock:
                # W5-D3: a publish for an older version of the file is late and answers nothing.
                if isinstance(version, int) and not isinstance(version, bool) and version != self._versions.get(uri):
                    return
                if isinstance(raw, list):
                    self._diagnostics[uri] = [dict(item) for item in raw if isinstance(item, Mapping)]
                else:  # malformed: the wait ends now rather than at its timeout
                    self._diagnostics[uri] = None
                event = self._published.setdefault(uri, threading.Event())
            event.set()
            return
        if message.get("id") is None:
            return  # other notifications (logs, progress) are not needed
        if method == "workspace/configuration":
            count = len(params.get("items") or []) if isinstance(params.get("items"), list) else 1
            result: Any = [self.config.settings] * count
        elif method in {"client/registerCapability", "client/unregisterCapability", "window/workDoneProgress/create"}:
            result = None
        elif method == "workspace/workspaceFolders":
            result = [{"uri": self.root.as_uri(), "name": self.root.name or "workspace"}]
        else:
            try:
                self._send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": f"not supported: {str(method)[:80]}"}})
            except LspError:
                pass
            return
        try:
            self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})
        except LspError:
            pass

    # -- documents --------------------------------------------------------------

    def diagnostics(self, path: Path, text: str, language: str, *, timeout: float) -> list[dict[str, Any]] | None:
        """Send the file's new text and wait for its diagnostics; None when the server sent none in time."""
        uri = Path(path).resolve().as_uri()
        with self._state_lock:
            event = self._published.setdefault(uri, threading.Event())
            event.clear()
            version = self._versions.get(uri)
            self._versions[uri] = (version or 0) + 1
        if version is None:
            self._notify("textDocument/didOpen", {"textDocument": {"uri": uri, "languageId": language, "version": 1, "text": text}})
        else:
            self._notify("textDocument/didChange", {"textDocument": {"uri": uri, "version": version + 1}, "contentChanges": [{"text": text}]})
        self._notify("textDocument/didSave", {"textDocument": {"uri": uri}, "text": text})
        if not event.wait(timeout):
            return None
        event.clear()
        event.wait(SETTLE_SECONDS)  # a server may publish twice (a quick pass, then a full one): keep the last
        with self._state_lock:
            found = self._diagnostics.get(uri, [])
            return None if found is None else list(found)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process, self._process = self._process, None
        if process is None:
            return
        self._process = process
        try:
            self._request("shutdown", {}, timeout=1.0)
            self._notify("exit", {})
        except (LspError, OSError, ValueError):
            pass  # W7 (W6-R17): a server that already died must not make close() raise
        self._process = None
        try:
            # W7 (W6-R17): end of input lets a server that waits for it exit on its own instead of being killed.
            if process.stdin is not None:
                process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process)


__all__ = ["LspClient", "LspError"]

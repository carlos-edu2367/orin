"""Run a local MCP server as a child process speaking NDJSON on stdio.

The model never chooses the binary: only launchers from ALLOWED_COMMANDS may
start, the argument vector is passed without a shell, and the child gets an
explicit environment instead of the worker's own.

``shell=False`` alone is not the full story on Windows: npx/uvx/node's own
package-manager shims resolve to ``.cmd``/``.bat`` files, and the OS loader
transparently re-invokes ``cmd.exe`` to run those — so anything that ends up on
that re-parsed command line is subject to cmd.exe's own operator and ``%VAR%``
expansion rules regardless of ``shell=False``. That is why ``%`` and ``^`` are
forbidden alongside the POSIX shell metacharacters, and why the check runs
against the credential *values* the server is launched with, not just the
command and its arguments: a secret containing ``&`` combined with a literal
``%SECRET_NAME%`` placeholder in an argument is enough for cmd.exe to expand
and then re-parse into a second command, once the shim's own script runs it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from typing import Any, Mapping

from agentos.agentic.agent_tools import _terminate_process_tree

ALLOWED_COMMANDS = frozenset({"npx", "uvx", "node", "python", "python3", "uv", "deno", "bun"})
FORBIDDEN_CHARACTERS = frozenset(";&|`$><^%\n\r")
DEFAULT_TIMEOUT_SECONDS = 45.0
MAX_LINE_BYTES = 4_000_000


class StdioTransportRefused(RuntimeError):
    """The requested process is not something this host will start."""


class StdioTransportError(RuntimeError):
    """The child process died or answered with an unusable frame."""


class StdioTransport:
    kind = "stdio"

    def __init__(self, *, command: str, args: tuple[str, ...], env: Mapping[str, str],
                 cwd: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS,
                 allow_any_command: bool = False) -> None:
        base = os.path.basename(command).lower().removesuffix(".exe").removesuffix(".cmd")
        if not allow_any_command and base not in ALLOWED_COMMANDS:
            raise StdioTransportRefused(
                f"'{command}' is not an allowed MCP launcher. Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}."
            )
        for value in (command, *args):
            if FORBIDDEN_CHARACTERS & set(value):
                raise StdioTransportRefused("the command line carries shell metacharacters")
        for name, value in env.items():
            if FORBIDDEN_CHARACTERS & set(value):
                raise StdioTransportRefused(
                    f"the credential '{name}' contains a character a launcher shim could reinterpret; "
                    "the connection cannot be started with this value"
                )
        self._command = command
        self._args = tuple(args)
        self._env = dict(env)
        self._cwd = cwd
        self._timeout = timeout
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()

    def open(self) -> None:
        if self._process is not None:
            return
        executable = shutil.which(self._command) or self._command
        # A deliberately small environment: PATH so the launcher resolves its
        # own runtime, plus the secrets this server was approved with.
        environment = {"PATH": os.environ.get("PATH", ""), "SystemRoot": os.environ.get("SystemRoot", ""), **self._env}
        try:
            self._process = subprocess.Popen(
                [executable, *self._args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, cwd=self._cwd, env=environment, shell=False, bufsize=0,
            )
        except OSError as error:
            raise StdioTransportError(f"the MCP server process could not start: {error}") from error

    def send(self, frame: Mapping[str, Any]) -> dict[str, Any] | None:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise StdioTransportError("the MCP server process is not running")
        payload = (json.dumps(frame) + "\n").encode()
        with self._lock:
            try:
                process.stdin.write(payload)
                process.stdin.flush()
            except OSError as error:
                raise StdioTransportError(f"the MCP server closed its input: {error}") from error
            if "id" not in frame:
                return None
            line = process.stdout.readline(MAX_LINE_BYTES)
        if not line:
            raise StdioTransportError("the MCP server closed before answering")
        try:
            return json.loads(line)
        except json.JSONDecodeError as error:
            raise StdioTransportError("the MCP server answered with invalid JSON") from error

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        _terminate_process_tree(process)


__all__ = ["ALLOWED_COMMANDS", "StdioTransport", "StdioTransportError", "StdioTransportRefused"]

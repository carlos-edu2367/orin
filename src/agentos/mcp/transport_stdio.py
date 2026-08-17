"""Run a local MCP server as a child process speaking NDJSON on stdio.

The model never chooses the binary: only launchers from ALLOWED_COMMANDS may
start, the argument vector is passed without a shell, and the child gets an
explicit environment instead of the worker's own.

``shell=False`` alone is not the full story on Windows: npm's own launchers
(``npx``) are generated as ``.cmd``/``.bat`` "shim" files (npm's cmd-shim),
and the OS loader transparently re-invokes ``cmd.exe`` to run those — so
anything that ends up on that re-parsed command line is subject to cmd.exe's
own operator and ``%VAR%`` expansion rules regardless of ``shell=False``.
That is why ``%``/``^``/``<``/``>`` are forbidden for those shim-prone
launchers specifically, and why the check runs against the credential
*values* the server is launched with too, not just the command and its
arguments: a secret containing ``&`` combined with a literal
``%SECRET_NAME%`` placeholder in an argument is enough for cmd.exe to expand
and then re-parse into a second command, once the shim's own script runs it.
The rest of ALLOWED_COMMANDS (uv/uvx, node, python, python3, deno, bun) ship
as native PE binaries invoked directly with no shell in between, so those
four characters are inert argv content for them — and sometimes legitimate,
e.g. a pinned dependency spec like ``mcp<2``. POSIX shell metacharacters
(``;&|`$`` plus newlines) stay forbidden for every launcher regardless, as a
baseline that costs nothing since no real launch command needs them.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from typing import Any, Mapping

from agentos.agentic.agent_tools import _terminate_process_tree

# Real launchers need a writable temp directory and a resolvable user
# profile, not just PATH: uv/npm/node use these to locate their cache and
# config directories. Without TEMP/TMP on Windows, uv falls back to a
# location under C:\Windows a normal user cannot write to and exits before
# the server script ever runs - found live installing obsidian-second-brain's
# vault server: "Acesso negado ... at path C:\WINDOWS\.tmpXXXX", surfaced to
# StdioTransport as "the MCP server closed before answering".
PLATFORM_ENVIRONMENT_PASSTHROUGH = (
    ("TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA") if os.name == "nt" else ("HOME", "TMPDIR")
)

ALLOWED_COMMANDS = frozenset({"npx", "uvx", "node", "python", "python3", "uv", "deno", "bun"})

# Only npx ships as a Windows .cmd/.bat shim among ALLOWED_COMMANDS; the rest
# are native binaries that never route through cmd.exe.
CMD_SHIM_PRONE_COMMANDS = frozenset({"npx"})

POSIX_FORBIDDEN_CHARACTERS = frozenset(";&|`$\n\r")
CMD_SHIM_FORBIDDEN_CHARACTERS = frozenset("><^%")
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
        forbidden = POSIX_FORBIDDEN_CHARACTERS
        if base in CMD_SHIM_PRONE_COMMANDS:
            forbidden = forbidden | CMD_SHIM_FORBIDDEN_CHARACTERS
        for value in (command, *args):
            if forbidden & set(value):
                raise StdioTransportRefused("the command line carries shell metacharacters")
        for name, value in env.items():
            if forbidden & set(value):
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
        self._replies: queue.Queue[bytes] = queue.Queue()
        self._reader_thread: threading.Thread | None = None

    def open(self) -> None:
        if self._process is not None:
            return
        executable = shutil.which(self._command) or self._command
        # A deliberately small environment: PATH so the launcher resolves its
        # own runtime, the platform temp/profile variables real launchers
        # need to function at all (see PLATFORM_ENVIRONMENT_PASSTHROUGH),
        # plus the secrets this server was approved with.
        environment = {"PATH": os.environ.get("PATH", ""), "SystemRoot": os.environ.get("SystemRoot", "")}
        for name in PLATFORM_ENVIRONMENT_PASSTHROUGH:
            value = os.environ.get(name)
            if value:
                environment[name] = value
        environment.update(self._env)
        try:
            self._process = subprocess.Popen(
                [executable, *self._args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, cwd=self._cwd, env=environment, shell=False, bufsize=0,
            )
        except OSError as error:
            raise StdioTransportError(f"the MCP server process could not start: {error}") from error
        self._reader_thread = threading.Thread(target=self._read_loop, args=(self._process,), daemon=True)
        self._reader_thread.start()

    def _read_loop(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stdout is not None
        try:
            while True:
                line = process.stdout.readline(MAX_LINE_BYTES)
                self._replies.put(line)
                if not line:
                    return
        except (OSError, ValueError):
            self._replies.put(b"")

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
            try:
                line = self._replies.get(timeout=self._timeout)
            except queue.Empty:
                _terminate_process_tree(process)
                self._process = None
                raise StdioTransportError(
                    f"the MCP server did not answer within {self._timeout}s and was terminated"
                )
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

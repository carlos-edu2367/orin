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
import signal
import subprocess
import threading
import time
from typing import Any, Mapping

from agentos.agentic.agent_tools import _terminate_process_tree
from agentos.hooks.processes import _WindowsJob, _close_handles, _terminate_handles, _windows_descendants

from .protocol import MAX_SERVER_REQUESTS_PER_CALL, call_ceiling_seconds, dispatch_server_message

# Real launchers need a writable temp directory and a resolvable user
# profile, not just PATH: uv/npm/node use these to locate their cache and
# config directories. Without TEMP/TMP on Windows, uv falls back to a
# location under C:\Windows a normal user cannot write to and exits before
# the server script ever runs - found live installing obsidian-second-brain's
# vault server: "Acesso negado ... at path C:\WINDOWS\.tmpXXXX", surfaced to
# StdioTransport as "the MCP server closed before answering".
PLATFORM_ENVIRONMENT_PASSTHROUGH = (
    ("TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA")
    if os.name == "nt"
    else ("HOME", "TMPDIR")
)

ALLOWED_COMMANDS = frozenset({"npx", "uvx", "node", "python", "python3", "uv", "deno", "bun"})

# Only npx ships as a Windows .cmd/.bat shim among ALLOWED_COMMANDS; the rest
# are native binaries that never route through cmd.exe.
CMD_SHIM_PRONE_COMMANDS = frozenset({"npx"})

POSIX_FORBIDDEN_CHARACTERS = frozenset(";&|`$\n\r")
CMD_SHIM_FORBIDDEN_CHARACTERS = frozenset("><^%")
# W5-R16: for a configuration-file server launched through a .cmd or .bat shim, cmd.exe re-reads the arguments, so its
# operators (and the redirections and escapes above) are refused. $, ; and the backtick are inert for cmd.exe.
CMD_OPERATOR_CHARACTERS = CMD_SHIM_FORBIDDEN_CHARACTERS | frozenset("&|\n\r")
DEFAULT_TIMEOUT_SECONDS = 45.0
MAX_LINE_BYTES = 4_000_000
CREATE_SUSPENDED = 0x00000004  # Windows process creation flag (W7, W6-R8)
# Lines that are not JSON (a server printing a banner on stdout) are skipped, up to this many per call.
MAX_SKIPPED_LINES = 1000
# W5: closing a server closes its input first and gives it this long to exit on its own before its tree is killed.
GRACEFUL_EXIT_SECONDS = 0.3


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
        if allow_any_command:
            # W5-R16: a server the person wrote in a configuration file starts without a shell, so the POSIX characters
            # are inert; only a .cmd or .bat launcher is re-parsed by cmd.exe.
            resolved = shutil.which(command) or ""
            forbidden = CMD_OPERATOR_CHARACTERS if resolved.lower().endswith((".cmd", ".bat")) else frozenset()
        else:
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
        self._job: _WindowsJob | None = None
        self._lock = threading.Lock()
        self._replies: queue.Queue[bytes] = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        # Set by the client: receives the server's own requests and notifications (W5).
        self.on_message: Any = None

    def open(self) -> None:
        if self._process is not None:
            return
        executable = shutil.which(self._command) or self._command
        # A deliberately small environment: PATH so the launcher resolves its
        # own runtime, the platform temp/profile variables real launchers
        # need to function at all (see PLATFORM_ENVIRONMENT_PASSTHROUGH),
        # plus the secrets this server was approved with.
        environment = {"PATH": os.environ.get("PATH", ""), "SystemRoot": os.environ.get("SystemRoot", "")}
        if os.name == "nt":
            for sys_name in ("SYSTEMROOT", "SystemDrive", "SYSTEMDRIVE"):
                sys_value = os.environ.get(sys_name)
                if sys_value:
                    environment[sys_name] = sys_value
        for name in PLATFORM_ENVIRONMENT_PASSTHROUGH:
            value = os.environ.get(name)
            if value:
                environment[name] = value
        environment.update(self._env)
        # W7 (W6-R8): on Windows the server starts suspended, joins its Job Object, and only then runs, so no child it
        # starts can escape the Job (before, it ran first and was assigned after Popen returned).
        creationflags = (getattr(subprocess, "CREATE_NO_WINDOW", 0) | CREATE_SUSPENDED) if os.name == "nt" else 0
        try:
            # W5-D2: on POSIX the server leads its own process group, so its children can be ended with it.
            self._process = subprocess.Popen(
                [executable, *self._args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, cwd=self._cwd, env=environment, shell=False, bufsize=0,
                creationflags=creationflags, start_new_session=os.name != "nt",
            )
        except OSError as error:
            raise StdioTransportError(f"the MCP server process could not start: {error}") from error
        if os.name == "nt":
            self._contain(self._process)
        self._reader_thread = threading.Thread(target=self._read_loop, args=(self._process,), daemon=True)
        self._reader_thread.start()

    def _contain(self, process: subprocess.Popen[bytes]) -> None:
        """Put the suspended server in its Job, then let it run; a server that cannot be contained never runs."""
        reason = None
        try:
            self._job = _WindowsJob()
            if not self._job.assign(process.pid):
                reason = "it could not join its Job Object"
        except OSError:
            self._job, reason = None, "its Job Object could not be created"
        if reason is None and not _resume(process):
            reason = "it could not be resumed"
        if reason is None:
            return
        try:
            process.kill()  # still suspended: it has run no code
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if self._job is not None:
            self._job.close()
            self._job = None
        self._process = None
        raise StdioTransportError(f"the MCP server process was not started: {reason}")

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
        with self._lock:
            self._write(process, frame)
            if "id" not in frame:
                return None
            ceiling_seconds = call_ceiling_seconds(self._timeout)
            ceiling = time.monotonic() + ceiling_seconds
            deadline = time.monotonic() + self._timeout
            skipped = 0
            answered = 0
            while True:
                if time.monotonic() >= ceiling:
                    raise self._give_up(
                        process,
                        f"the MCP server kept one call open for longer than {ceiling_seconds:g}s and was terminated",
                    )
                try:
                    line = self._replies.get(timeout=max(0.0, min(deadline, ceiling) - time.monotonic()))
                except queue.Empty:
                    if time.monotonic() < deadline:
                        continue  # woke at the call ceiling, which the check above reports
                    raise self._give_up(
                        process, f"the MCP server did not answer within {self._timeout}s and was terminated"
                    ) from None
                if not line:
                    raise StdioTransportError("the MCP server closed before answering")
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as error:
                    skipped += 1
                    if skipped > MAX_SKIPPED_LINES:
                        raise StdioTransportError("the MCP server answered with invalid JSON") from error
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("id") == frame["id"] and ("result" in message or "error" in message):
                    return message
                if "method" in message:
                    reply = dispatch_server_message(message, self.on_message)
                    if reply is not None:
                        answered += 1
                        if answered > MAX_SERVER_REQUESTS_PER_CALL:
                            raise self._give_up(
                                process,
                                f"the MCP server sent more than {MAX_SERVER_REQUESTS_PER_CALL} requests while "
                                "answering one call and was terminated",
                            )
                        self._write(process, reply)
                        deadline = time.monotonic() + self._timeout  # the server waited on us: its clock starts again

    def _give_up(self, process: subprocess.Popen[bytes], reason: str) -> StdioTransportError:
        tree = self._tree(process)
        _terminate_process_tree(process)
        self._process = None
        self._end_children(process, tree)
        return StdioTransportError(reason)

    @staticmethod
    def _tree(process: subprocess.Popen[bytes]) -> dict[int, int]:
        """The server's live descendants (Windows), taken while the server still runs: an orphan has no parent chain."""
        if os.name != "nt":
            return {}
        try:
            return _windows_descendants(process)
        except (OSError, ValueError):
            return {}

    def _end_children(self, process: subprocess.Popen[bytes], tree: dict[int, int] | None = None) -> None:
        """W5-D2: whatever the server started dies with it, even when the server itself exited on its own."""
        job, self._job = self._job, None
        if os.name == "nt":
            # A child started before the Job was assigned (or one that left it) is found by its parent chain and
            # creation time; the handles were opened while it was validated, so a reused PID is never hit.
            descendants = dict(tree if tree is not None else self._tree(process))
            try:
                _terminate_handles(descendants)
            except (OSError, ValueError):
                pass
            finally:
                try:
                    _close_handles(descendants)
                except (OSError, ValueError):
                    pass
            if job is not None:
                try:
                    job.terminate()
                except OSError:
                    pass
                job.close()
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:  # the group is already empty
            pass

    @staticmethod
    def _write(process: subprocess.Popen[bytes], frame: Mapping[str, Any]) -> None:
        assert process.stdin is not None
        try:
            process.stdin.write((json.dumps(frame) + "\n").encode())
            process.stdin.flush()
        except OSError as error:
            raise StdioTransportError(f"the MCP server closed its input: {error}") from error

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        tree = self._tree(process)
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=GRACEFUL_EXIT_SECONDS)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process)
        self._end_children(process, tree)


def _resume(process: subprocess.Popen[bytes]) -> bool:
    """Resume a process started with CREATE_SUSPENDED (Popen keeps the process handle, not the thread's)."""
    import ctypes

    handle = getattr(process, "_handle", None)
    if not handle:
        return False
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.restype = ctypes.c_long
    ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
    return ntdll.NtResumeProcess(ctypes.c_void_p(int(handle))) == 0


__all__ = ["ALLOWED_COMMANDS", "StdioTransport", "StdioTransportError", "StdioTransportRefused"]

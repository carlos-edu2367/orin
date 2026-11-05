from __future__ import annotations

import io
from typing import Any, Mapping


class FakeClaudePopen:
    """Fake subprocess.Popen for testing `ClaudeCliProvider` without spawning `claude.exe`."""

    def __init__(
        self,
        cmd: list[str],
        *,
        ndjson_lines: list[str],
        stderr_text: str = "",
        returncode: int = 0,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        on_line_yielded: Any = None,
        gate_before_line: Mapping[int, Any] | None = None,
    ) -> None:
        self.argv = list(cmd)
        self.args = list(cmd)
        self.env = dict(env or {})
        self.cwd = cwd
        self.pid = 51515
        self.returncode: int | None = None
        self._final_returncode = returncode
        self.stdin_writes: list[str] = []
        self.killed = False
        self.terminated = False
        self._lines = list(ndjson_lines)
        self._on_line_yielded = on_line_yielded
        self._gate_before_line = dict(gate_before_line or {})
        self.stdin = self._make_stdin()
        self.stdout = self._make_stdout()
        self.stderr = io.StringIO(stderr_text)

    def _make_stdin(self) -> Any:
        parent = self

        class _Stdin:
            def __init__(self) -> None:
                self._buf: list[str] = []
                self.closed = False

            def write(self, data: str) -> int:
                self._buf.append(data)
                return len(data)

            def flush(self) -> None:
                pass

            def close(self) -> None:
                if not self.closed:
                    self.closed = True
                    parent.stdin_writes.append("".join(self._buf))

        return _Stdin()

    def _make_stdout(self) -> Any:
        parent = self

        class _Stdout:
            def __iter__(self) -> Any:
                for idx, line in enumerate(parent._lines):
                    gate = parent._gate_before_line.get(idx)
                    if gate is not None:
                        gate.wait(timeout=5.0)
                    if parent.killed or parent.terminated:
                        return
                    yield line if line.endswith("\n") else f"{line}\n"
                    if parent._on_line_yielded is not None:
                        parent._on_line_yielded(idx, line)
                parent.returncode = parent._final_returncode

            def close(self) -> None:
                pass

        return _Stdout()

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = self._final_returncode
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

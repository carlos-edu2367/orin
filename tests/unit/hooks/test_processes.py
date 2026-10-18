from __future__ import annotations

import os
import sys
import textwrap
import time
from pathlib import Path

from agentos.agentic.agent_tools import _process_is_running
from agentos.hooks.processes import MAX_OUTPUT_CHARS, run_shell_command


def _py(script: Path) -> str:
    return f'"{sys.executable}" "{script}"'


def test_stdin_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    script = tmp_path / "echo.py"
    script.write_text(textwrap.dedent("""
        import sys, json
        data = json.loads(sys.stdin.read())
        print("got", data["x"])
        print("warn", file=sys.stderr)
        sys.exit(3)
    """), encoding="utf-8")
    result = run_shell_command(_py(script), cwd=str(tmp_path), env=dict(os.environ), stdin_text='{"x": 7}', timeout_seconds=30)
    assert result.exit_code == 3 and not result.timed_out and result.error is None
    assert "got 7" in result.stdout and "warn" in result.stderr


def test_output_is_capped(tmp_path: Path) -> None:
    script = tmp_path / "big.py"
    script.write_text("print('x' * 100000)", encoding="utf-8")
    result = run_shell_command(_py(script), cwd=str(tmp_path), env=dict(os.environ), stdin_text="", timeout_seconds=30)
    assert len(result.stdout) <= MAX_OUTPUT_CHARS


def test_timeout_kills_the_whole_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "tree.py"
    script.write_text(textwrap.dedent(f"""
        import subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        open(r"{pid_file}", "w").write(str(child.pid))
        time.sleep(60)
    """), encoding="utf-8")
    started = time.monotonic()
    result = run_shell_command(_py(script), cwd=str(tmp_path), env=dict(os.environ), stdin_text="", timeout_seconds=3)
    assert result.timed_out and result.exit_code is None
    assert time.monotonic() - started < 20
    deadline = time.monotonic() + 10
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    child_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while _process_is_running(child_pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _process_is_running(child_pid), "grandchild survived the timeout"


def test_missing_cwd_is_an_error_not_an_exception(tmp_path: Path) -> None:
    result = run_shell_command("echo hi", cwd=str(tmp_path / "nope"), env=dict(os.environ), stdin_text="", timeout_seconds=5)
    assert result.error is not None and result.exit_code is None

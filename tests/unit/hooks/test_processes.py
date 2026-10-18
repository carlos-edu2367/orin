from __future__ import annotations

import os
import sys
import textwrap
import subprocess
import time
from pathlib import Path

import pytest

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


def test_descendants_rule_rejects_recycled_parent_pids() -> None:
    # Windows keeps th32ParentProcessID after the parent dies and recycles PIDs. A child counts
    # only when it was created no earlier than its parent and no earlier than the root.
    from agentos.hooks.processes import _descendants_from

    rows = [(500, 100, 10), (600, 100, 30), (700, 600, 35), (800, 600, 5)]
    assert _descendants_from(rows, 100, 20) == [600, 700]


def test_descendants_rule_never_descends_through_a_rejected_node() -> None:
    from agentos.hooks.processes import _descendants_from

    # 400 predates the root (rejected), so its child 401 is not reached even though it is newer.
    rows = [(400, 100, 1), (401, 400, 50)]
    assert _descendants_from(rows, 100, 20) == []


def test_descendants_rule_excludes_protected_pids() -> None:
    from agentos.hooks.processes import _descendants_from

    rows = [(600, 100, 30), (700, 600, 35)]
    assert _descendants_from(rows, 100, 20, exclude=frozenset({600})) == []


def test_windows_walk_never_returns_orin_or_its_ancestors() -> None:
    """Only our own processes are spawned here; the walk returns handles and nothing is terminated by it."""
    if os.name != "nt":
        pytest.skip("Windows process table")
    from agentos.hooks.processes import _close_handles, _terminate_handles, _windows_descendants

    root = subprocess.Popen([
        sys.executable, "-c",
        "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)",
    ])
    handles: dict[int, int] = {}
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            handles = _windows_descendants(root)
            if handles:
                break
            time.sleep(0.2)
        assert handles, "the walk did not find our own grandchild"
        assert os.getpid() not in handles
        assert root.pid not in handles
    finally:
        _terminate_handles(handles)
        _close_handles(handles)
        root.kill()
        root.wait()

"""W7 Batch F (W6-R8): a stdio MCP server joins its Job before it runs; one that cannot be contained never runs."""
from __future__ import annotations

import os
import sys
import time

import pytest

from agentos.mcp import transport_stdio
from agentos.mcp.transport_stdio import StdioTransport, StdioTransportError

from .test_w6_stdio_children import _alive

QUICK_EXIT = """
import subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open(sys.argv[1], "w").write(str(child.pid))
"""  # starts a child and exits at once: before W7 the child could start before the Job existed

MARKER_FIRST = """
import sys
open(sys.argv[1], "w").write("ran")
for line in sys.stdin:
    pass
"""


def _wait(predicate, seconds: float = 15) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def _quick_exit_server(tmp_path, marker) -> StdioTransport:
    if os.name == "nt":
        # A Microsoft Store Python's own children leave every Job (the store's app model), so on Windows the server
        # is PowerShell starting ping: a plain process tree, as most stdio servers are.
        command = ("$p = Start-Process -FilePath ping.exe -ArgumentList '-n','60','127.0.0.1' -WindowStyle Hidden -PassThru; "
                   f"[IO.File]::WriteAllText('{marker}', [string]$p.Id)")
        return StdioTransport(command="powershell.exe", args=("-NoProfile", "-NonInteractive", "-Command", command),
                              env={}, allow_any_command=True)
    script = tmp_path / "server.py"
    script.write_text(QUICK_EXIT, encoding="utf-8")
    return StdioTransport(command=sys.executable, args=(str(script), str(marker)), env={}, allow_any_command=True)


def test_the_child_of_a_server_that_exits_on_its_own_ends_with_it(tmp_path) -> None:
    marker = tmp_path / "child.pid"
    transport = _quick_exit_server(tmp_path, marker)
    transport.open()
    assert _wait(lambda: marker.is_file() and marker.read_text().strip().isdigit())
    child = int(marker.read_text())
    transport.close()
    assert _wait(lambda: not _alive(child)), "the server's child outlived it"


@pytest.mark.skipif(os.name != "nt", reason="Job Objects are Windows'")
def test_a_server_that_cannot_join_its_job_never_runs(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "server.py"
    script.write_text(MARKER_FIRST, encoding="utf-8")
    marker = tmp_path / "ran.txt"
    monkeypatch.setattr(transport_stdio._WindowsJob, "assign", lambda self, pid: False)
    transport = StdioTransport(command=sys.executable, args=(str(script), str(marker)), env={}, allow_any_command=True)
    with pytest.raises(StdioTransportError, match="Job Object"):
        transport.open()
    time.sleep(1.0)
    assert not marker.exists(), "the server ran outside its Job"
    assert transport._process is None


@pytest.mark.skipif(os.name != "nt", reason="Job Objects are Windows'")
def test_a_contained_server_runs_and_answers(tmp_path) -> None:
    script = tmp_path / "server.py"
    script.write_text(MARKER_FIRST, encoding="utf-8")
    marker = tmp_path / "ran.txt"
    transport = StdioTransport(command=sys.executable, args=(str(script), str(marker)), env={}, allow_any_command=True)
    transport.open()
    try:
        assert transport._job is not None
        assert _wait(marker.is_file), "the resumed server never ran"
    finally:
        transport.close()

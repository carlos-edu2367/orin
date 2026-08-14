"""Regression test for the PyInstaller entry point's multiprocessing bootstrap.

On Windows, a PyInstaller-frozen executable that spawns a ``multiprocessing``
child (the isolated conversational browser host, in this codebase) re-executes
itself with special ``--multiprocessing-fork`` arguments. ``sys.frozen`` is set
by the PyInstaller bootloader, and stdlib's ``multiprocessing.freeze_support()``
only intercepts those arguments (and hands off to ``spawn_main``) when it is
called *and* ``sys.frozen`` is true. Skipping that call leaves the fork argv to
fall through to the CLI's own ``argparse`` parser, which rejects it outright —
the child process dies before it ever does the work the parent is waiting for,
and the parent times out instead.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

FROZEN_ENTRY = Path(__file__).resolve().parents[3] / "packaging" / "frozen_entry.py"


def _run_frozen_entry_as_main(argv: list[str]) -> None:
    with patch.object(sys, "argv", argv), patch.object(sys, "frozen", True, create=True):
        try:
            runpy.run_path(str(FROZEN_ENTRY), run_name="__main__")
        except SystemExit:
            pass


def test_frozen_entry_diverts_a_multiprocessing_fork_before_the_cli_parses_argv() -> None:
    """A spawn-style child argv must reach spawn_main, never the CLI parser."""
    fork_argv = ["orin.exe", "--multiprocessing-fork", "parent_pid=4242", "pipe_handle=99"]
    captured: dict[str, object] = {}

    def fake_spawn_main(**kwds: object) -> None:
        captured["kwds"] = kwds
        raise SystemExit(0)

    with patch("multiprocessing.spawn.spawn_main", side_effect=fake_spawn_main) as spawn_main, \
         patch("agentos.launcher.cli.main") as cli_main:
        _run_frozen_entry_as_main(fork_argv)
        spawn_main.assert_called_once()
        cli_main.assert_not_called()

    assert captured["kwds"] == {"parent_pid": 4242, "pipe_handle": 99}


def test_frozen_entry_still_runs_the_cli_for_a_normal_invocation() -> None:
    """An ordinary launch (no fork argv) must be unaffected by the bootstrap call."""
    with patch("multiprocessing.spawn.spawn_main") as spawn_main, \
         patch("agentos.launcher.cli.main", return_value=0) as cli_main:
        _run_frozen_entry_as_main(["orin.exe", "status"])
        spawn_main.assert_not_called()
        cli_main.assert_called_once()


@pytest.mark.skipif(sys.platform != "win32", reason="multiprocessing spawn argv format is Windows-specific here")
def test_get_command_line_matches_the_argv_shape_this_test_simulates() -> None:
    """Guards the fixture itself against drifting from what CPython actually sends."""
    import multiprocessing.spawn as spawn

    with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", "orin.exe"):
        command = spawn.get_command_line(parent_pid=4242, pipe_handle=99)
    assert command == ["orin.exe", "--multiprocessing-fork", "parent_pid=4242", "pipe_handle=99"]

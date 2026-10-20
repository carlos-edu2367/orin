"""W6-R4: against a running Orin, a managed policy that disables bypass stops every bypass route the CLI has."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .live_runtime import LiveRuntime
from .test_headless import MODEL


def _executable() -> str:
    return str(Path(sys.executable).parent / ("orin-code.exe" if sys.platform == "win32" else "orin-code"))


def test_managed_disable_refuses_the_bypass_flags_and_writes_nothing(orin_runtime: LiveRuntime) -> None:
    managed = Path(orin_runtime.env["ORIN_MANAGED_DIR"]) / "managed-settings.json"
    managed.write_text(json.dumps({"permissions": {"disableBypassPermissionsMode": "disable"}}), encoding="utf-8")
    try:
        for index, flags in enumerate((["--dangerously-skip-permissions"], ["--permission-mode", "bypassPermissions"])):
            workspace = orin_runtime.workspace(f"r4-policy-{index}")
            done = subprocess.run([_executable(), "-p", "[[write:bypass.txt]]", *flags, "--output-format", "json", *MODEL],
                                  cwd=str(workspace), env=orin_runtime.env, capture_output=True, text=True,
                                  encoding="utf-8", timeout=180)
            assert done.returncode == 1, done.stderr
            assert "bypass mode is disabled by policy (permissions.disableBypassPermissionsMode)" in done.stderr
            assert not (workspace / "bypass.txt").exists()
    finally:
        managed.unlink()

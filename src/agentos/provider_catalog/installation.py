"""Explicit local installation support for the separately-managed OmniRoute CLI."""
from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable


class OmniRouteInstaller:
    """Run only OmniRoute's documented global npm installation command.

    The caller explicitly invokes this action. The installer deliberately does
    not start a server, read configuration files, or return process output.
    """

    def __init__(
        self,
        *,
        executable: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        finder: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._executable = executable or ("npm.cmd" if os.name == "nt" else "npm")
        self._runner = runner
        self._finder = finder

    def __repr__(self) -> str:
        return "OmniRouteInstaller()"

    def install(self) -> dict[str, object]:
        try:
            completed = self._runner(
                [self._executable, "install", "-g", "omniroute"],
                timeout=180,
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError("OmniRoute installation failed") from error
        if completed.returncode != 0:
            raise RuntimeError("OmniRoute installation failed")
        return {"installed": True, "next_step": "omniroute"}

    def installation_status(self) -> dict[str, object]:
        return {"installed": bool(self._finder("omniroute") or self._finder("omniroute.cmd"))}


__all__ = ["OmniRouteInstaller"]

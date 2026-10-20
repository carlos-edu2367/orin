"""The subcommands of ``orin code`` (spec §9.2)."""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

from ..errors import CliRuntimeError


def run_subcommand(name: str, argv: list[str], *, stdout: TextIO, stderr: TextIO, cwd: Path | None = None) -> int:
    cwd = (cwd or Path.cwd()).resolve()
    if name == "mcp":
        from .mcp import run

        return run(argv, cwd=cwd, stdout=stdout, stderr=stderr)
    if name == "plugin":
        from .plugin import run

        return run(argv, cwd=cwd, stdout=stdout, stderr=stderr)
    if name in {"agents", "doctor", "auto-mode"}:
        from . import info

        handler = {"agents": info.agents, "doctor": info.doctor, "auto-mode": info.auto_mode}[name]
        return handler(argv, cwd=cwd, stdout=stdout, stderr=stderr)
    if name in {"logs", "stop", "rm", "attach", "purge"}:
        from .background import run

        return run(name, argv, cwd=cwd, stdout=stdout, stderr=stderr)
    if name == "review":
        raise CliRuntimeError("`orin code review` arrives with W8 (multi-agent review)")
    raise CliRuntimeError(f"`orin code {name}` is not supported yet (importing other tools' settings is planned, not built)")


__all__ = ["run_subcommand"]

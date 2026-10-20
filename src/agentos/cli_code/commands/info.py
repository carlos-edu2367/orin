"""``orin code agents``, ``doctor`` and ``auto-mode``."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any, TextIO

from agentos.settings.paths import orin_user_dir
from agentos.settings.writer import update_settings_file

from ..args import CliUsageError
from ..errors import CliRuntimeError


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)


def _trusted(cwd: Path) -> bool:
    from agentos.permissions.trust import WorkspaceTrustStore

    try:
        return WorkspaceTrustStore().is_trusted("local-user", cwd)
    except Exception:  # noqa: BLE001
        return False


def agents(argv: list[str], *, cwd: Path, stdout: TextIO, stderr: TextIO) -> int:
    parser = _Parser(prog="orin code agents", allow_abbrev=False)
    parser.add_argument("--json", action="store_true")
    options = parser.parse_args(argv)
    from agentos.agentic.agent_definitions import load_agent_definitions

    from .background import background_runs

    catalog = load_agent_definitions(workspace_root=cwd, trusted=_trusted(cwd), plugin_folders=(), personal=True)
    definitions = [{"name": item.name, "origin": item.origin, "description": item.description,
                    "model": item.model, "tools": list(item.tools) if item.tools is not None else None}
                   for item in sorted(catalog.agents.values(), key=lambda value: value.name)]
    runs = background_runs()
    if options.json:
        stdout.write(json.dumps({"agents": definitions, "background": runs}, indent=2) + "\n")
        return 0
    for item in definitions:
        stdout.write(f"{item['name']} [{item['origin']}]: {item['description']}\n")
    for path, reason in catalog.errors[:20]:
        stderr.write(f"{path}: {reason}\n")
    if runs:
        stdout.write("\nBackground runs:\n")
        for run in runs:
            stdout.write(f"{run['id']}: {run['state']} ({run.get('cwd')})\n")
    return 0


def doctor(argv: list[str], *, cwd: Path, stdout: TextIO, stderr: TextIO) -> int:
    _Parser(prog="orin code doctor", allow_abbrev=False).parse_args(argv)
    from agentos.configuration.mode import RuntimeMode, current_mode
    from agentos.installation import orin_paths
    from agentos.installation.api_token import read_api_token
    from agentos.settings import load_effective_settings
    from agentos.version import __version__

    from ..runtime import connect_local, find_or_start_runtime
    from ..selection import ModelCatalog

    checks: list[tuple[str, bool, str]] = [("orin code", True, __version__)]
    checks.append(("local profile", current_mode() is not RuntimeMode.SERVER, f"ORIN_MODE={os.environ.get('ORIN_MODE') or 'local'}"))
    paths = orin_paths()
    try:
        state = find_or_start_runtime(paths, start=False)
        checks.append(("runtime", True, state.url))
    except CliRuntimeError as error:
        checks.append(("runtime", False, str(error)))
        state = None
    checks.append(("CLI token", read_api_token(paths.run) is not None, str(paths.run)))
    if state is not None:
        try:
            client = connect_local(paths, start=False)
        except CliRuntimeError as error:
            checks.append(("API", False, str(error)))
        else:
            try:
                enabled = ModelCatalog(client).enabled_providers()
                checks.append(("providers", bool(enabled), ", ".join(enabled) or "no provider is set up (Settings > Providers)"))
            finally:
                client.close()
    try:
        settings = load_effective_settings(cwd, trusted=_trusted(cwd))
        checks.append(("settings", not settings.errors, "; ".join(f"{error.path}: {error.message}" for error in settings.errors)[:300] or "readable"))
        model = load_effective_settings(None, sources=("user",)).get("model")
        checks.append(("default model", True, str(model) if model else "none (pass --model)"))
    except Exception as error:  # noqa: BLE001
        checks.append(("settings", False, str(error)[:200]))
    checks.append(("git", shutil.which("git") is not None, shutil.which("git") or "not found"))
    checks.append(("this folder", True, "trusted" if _trusted(cwd) else "not trusted (its settings, hooks and MCP servers are off)"))
    failed = False
    for name, ok, detail in checks:
        failed = failed or not ok
        stdout.write(f"{'ok  ' if ok else 'FAIL'} {name}: {detail}\n")
    return 1 if failed else 0


def auto_mode(argv: list[str], *, cwd: Path, stdout: TextIO, stderr: TextIO) -> int:
    parser = _Parser(prog="orin code auto-mode", allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("config")
    commands.add_parser("defaults")
    commands.add_parser("reset")
    critique = commands.add_parser("critique")
    critique.add_argument("--model")
    options = parser.parse_args(argv)
    from agentos.permissions.auto_mode import DEFAULT_HARD_DENY_PATTERNS, _SYSTEM
    from agentos.settings import load_effective_settings

    defaults: dict[str, Any] = {"hard_deny_patterns": list(DEFAULT_HARD_DENY_PATTERNS), "classifier_instructions": _SYSTEM}
    if options.command == "defaults":
        stdout.write(json.dumps(defaults, indent=2) + "\n")
        return 0
    configured = load_effective_settings(cwd, trusted=_trusted(cwd)).get("autoMode") or {}
    if options.command == "config":
        stdout.write(json.dumps({"autoMode": configured, "defaults": defaults}, indent=2) + "\n")
        return 0
    if options.command == "reset":
        path = orin_user_dir() / "settings.json"

        def forget(data: dict[str, Any]) -> None:
            data.pop("autoMode", None)

        update_settings_file(path, forget)
        stdout.write(f"Your autoMode settings were removed from {path}; the defaults apply.\n")
        return 0
    from ..args import parse_code_arguments
    from ..main import run_headless

    prompt = ("Review these auto mode rules for an AI coding agent: say which risky actions they would let through without "
              "asking, which safe actions they would block, and what you would change, concisely.\n\n"
              + json.dumps({"configured": configured, "defaults": defaults}, indent=1)[:12000])
    flags = ["-p", prompt, "--permission-mode", "plan", "--tools", ""] + (["--model", options.model] if options.model else [])
    code_options, _ = parse_code_arguments(flags)
    import sys

    return run_headless(code_options, stdin=sys.stdin, stdout=stdout, stderr=stderr)


__all__ = ["agents", "auto_mode", "doctor"]

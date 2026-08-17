"""The only module in the plugin system that starts a process.

Two properties are enforced here rather than by convention elsewhere:

* **No shell.** The declared command is split with ``shlex`` and any shell
  metacharacter refuses the hook outright. This is the same rule the local
  terminal adapter already applies (``agentos.terminal.local``).
* **Package confinement.** Whatever is executed must live inside the plugin's
  own installation directory, either as ``argv[0]`` or as the first argument
  to a small allow-list of interpreters. ``python3 -c`` and anything pointing
  outside the package are refused.

A hook's exit code is captured for reporting and is never returned in a form
that could deny an action; v1 hooks cannot veto, and that is a property of
this module's return type.
"""
from __future__ import annotations

import os
from pathlib import Path
import shlex

DENIED_TOKENS = ("|", ";", "&&", "||", ">", "<", "`", "$(", "\n", "\r")
INTERPRETERS = frozenset({"python", "python3", "node", "sh", "bash"})


class HookRejected(ValueError):
    """The declared command cannot be confined, so it will not be run."""


def _inside(candidate: Path, root: Path) -> Path:
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root.resolve(strict=False)):
        raise HookRejected(f"hook target '{candidate}' is outside the plugin package")
    if not resolved.is_file():
        raise HookRejected(f"hook target '{candidate}' is not a file in the plugin package")
    return resolved


def resolve_argv(command: str, *, install_path: Path) -> list[str]:
    if any(token in command for token in DENIED_TOKENS):
        raise HookRejected("hook commands may not use shell operators")
    expanded = command.replace("${CLAUDE_PLUGIN_ROOT}", str(Path(install_path)))
    try:
        argv = shlex.split(expanded, posix=True)
    except ValueError as error:
        raise HookRejected("hook command could not be parsed") from error
    if not argv:
        raise HookRejected("hook command is empty")
    head = os.path.basename(argv[0]).lower()
    if head.endswith(".exe"):
        head = head[:-4]
    if head in INTERPRETERS:
        if len(argv) < 2:
            raise HookRejected("an interpreter hook must name a script inside the plugin package")
        if argv[1].startswith("-"):
            raise HookRejected("an interpreter hook may not take inline flags such as -c")
        return [argv[0], str(_inside(Path(argv[1]), Path(install_path))), *argv[2:]]
    return [str(_inside(Path(argv[0]), Path(install_path))), *argv[1:]]

"""Which publishing actions a shell command performs, for Code mode's push and PR guards (W4-R5)."""
from __future__ import annotations

import re
import shlex

from agentos.permissions.powershell import analyze_for
from agentos.permissions.shell import default_shell

_PUBLISH_ACTIONS = ("push", "pr", "repo")
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})


def _git_subcommand_is_push(words: list[str]) -> bool:
    """True when git's first non-option word is push; git's global options, some with a value, come before it."""
    index = 0
    while index < len(words):
        word = words[index]
        if word in _GIT_VALUE_OPTIONS:
            index += 2
            continue
        if word.startswith("-"):
            index += 1
            continue
        return word == "push"
    return False


def publish_actions(command: str, tool: str) -> set[str]:
    """What a shell command publishes: "push", "pr" or "repo" (W4-R5).

    Decided from the shell analysis, so quotes, a .exe suffix and git's global options (-C dir, --no-pager) do
    not hide an action. A command the analysis cannot read counts every action it mentions: fail closed.
    """
    dialect = "powershell" if tool == "powershell" else default_shell()
    plain = re.sub(r"[\"'`\\]", "", command)
    analysis = analyze_for(command, dialect)
    if analysis.complex:
        actions = {"push"} if re.search(r"\bpush\b", plain, re.IGNORECASE) else set()
        if re.search(r"\bgh\b", plain, re.IGNORECASE):
            actions.update(action for action in ("pr", "repo") if re.search(rf"\b{action}\b", plain, re.IGNORECASE))
        return actions
    actions = set()
    for sub in analysis.subcommands:
        try:
            words = [word.strip("\"'") for word in shlex.split(sub, posix=False)]
        except ValueError:
            return set(_PUBLISH_ACTIONS)
        if not words:
            continue
        program = re.split(r"[\\/]", words[0])[-1].lower().removesuffix(".exe")
        if program == "git" and _git_subcommand_is_push(words[1:]):
            actions.add("push")
        elif program == "gh":
            actions.update(action for action in ("pr", "repo") if action in words[1:])
    return actions

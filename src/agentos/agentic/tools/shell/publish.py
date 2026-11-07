"""Which publishing actions a shell command performs, for Code mode's push and PR guards (W4-R5)."""
from __future__ import annotations

import re
import shlex

from agentos.permissions.powershell import analyze_for
from agentos.permissions.shell import default_shell

_PUBLISH_ACTIONS = ("push", "pr", "repo")
_GH_READ_ONLY = {
    "pr": frozenset({"view", "list", "diff", "checks", "status", "checkout"}),
    "repo": frozenset({"view", "list", "clone"}),
}
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


def publish_actions(command: str, tool: str, *, _depth: int = 0) -> set[str]:
    """What a shell command publishes: "push", "pr" or "repo" (W4-R5).

    Decided from the commands the shell runs, so quotes, a .exe suffix and git's global options (-C dir,
    --no-pager) do not hide an action, and a word in a commit message, a grep pattern or a file name never
    counts as one. Strings run by a nested shell or ``eval`` and command substitutions are read too; here-document
    bodies are data. Only a command the analysis cannot split at all counts every action it mentions.
    """
    from agentos.agentic.tools.shell.production import _inner_commands, _substitutions, _unwrap, strip_heredocs

    dialect = "powershell" if tool == "powershell" else default_shell()
    command = strip_heredocs(command)
    plain = re.sub(r"[\"'`\\]", "", command)
    analysis = analyze_for(command, dialect)
    if not analysis.subcommands:
        if not plain.strip():
            return set()
        actions = {"push"} if re.search(r"\bgit\b.*\bpush\b", plain, re.IGNORECASE) else set()
        if re.search(r"\bgh\b", plain, re.IGNORECASE):
            actions.update(action for action in ("pr", "repo") if re.search(rf"\bgh\s+{action}\b", plain, re.IGNORECASE))
        return actions
    actions = set()
    nested: list[str] = []
    for sub in analysis.subcommands:
        try:
            words = [word.strip("\"'") for word in shlex.split(sub, posix=False)]
        except ValueError:
            words = [word.strip("\"'") for word in sub.split()]
        words = _unwrap(words)
        if not words:
            continue
        program = re.split(r"[\\/]", words[0])[-1].lower().removesuffix(".exe")
        if program == "git" and _git_subcommand_is_push(words[1:]):
            actions.add("push")
        elif program == "gh":
            positionals: list[str] = []
            rest = iter(words[1:])
            for word in rest:
                if word in {"-R", "--repo", "--hostname"}:
                    next(rest, None)  # the option's value is not a subcommand
                elif not word.startswith("-"):
                    positionals.append(word)
            # Reading (gh pr view/list/diff/checks, gh repo view/clone) publishes nothing.
            if positionals and positionals[0] in {"pr", "repo"} and not (
                len(positionals) > 1 and positionals[1] in _GH_READ_ONLY.get(positionals[0], frozenset())
            ):
                actions.add(positionals[0])
        nested.extend(_inner_commands(words))
    nested.extend(_substitutions(command))
    if _depth < 3:
        for inner in nested:
            actions |= publish_actions(inner, tool, _depth=_depth + 1)
    return actions

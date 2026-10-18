"""Claude Code-compatible permission rules: ``Tool`` or ``Tool(specifier)``."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from agentos.agentic.tools.public_names import internal_name

from .paths import path_rule_matches
from .shell import ShellAnalysis, analyze_command

_ASSIGNMENT_WORD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_RULE = re.compile(r"^\s*([A-Za-z0-9_.*-]+)(?:\((.*)\))?\s*$", re.DOTALL)
_LEGACY = {"shell": "run_command", "bash": "run_command", "grep": "search_files", "grep_files": "search_files"}

EDIT_FAMILY: frozenset[str] = frozenset({"edit_file", "write_file", "notebook_edit"})
READ_FAMILY: frozenset[str] = frozenset({"read_file", "view_file", "transcribe_pdf", "list_files", "glob_files", "search_files"})
_SUBAGENT_TYPES = {"explore": "explore", "plan": "plan", "general-purpose": "general", "general": "general", "fork": "fork"}
_COMPOUND_PREFIXES = frozenset({
    "git", "npm", "pnpm", "yarn", "npx", "bun", "cargo", "poetry", "uv", "pip", "python", "python3",
    "pytest", "go", "docker", "make", "dotnet", "gradle", "mvn",
})


@dataclass(frozen=True, slots=True)
class PermissionRule:
    raw: str
    kind: str
    source: str
    tool: str
    specifier: str | None


@dataclass(frozen=True, slots=True)
class MatchContext:
    workspace_root: Path
    home: Path
    shell: str


def parse_rule(raw: str, *, kind: str, source: str) -> PermissionRule | None:
    if not isinstance(raw, str):
        return None
    match = _RULE.match(raw)
    if match is None:
        return None
    name, spec = match.group(1), match.group(2)
    if name.startswith("mcp__") or name == "*":
        tool = name
    else:
        tool = _LEGACY.get(name.lower()) or internal_name(name)
    specifier = spec.strip() if spec is not None else None
    if specifier in {"", "*"}:
        specifier = None
    return PermissionRule(raw.strip(), kind, source, tool, specifier)


def rule_applies_to_tool(rule: PermissionRule, internal: str) -> bool:
    if rule.tool == "*":
        return True
    if rule.tool.startswith("mcp__"):
        if not internal.startswith("mcp__"):
            return False
        if rule.tool.endswith("__*"):
            return internal.startswith(rule.tool[:-1])
        if rule.tool.count("__") == 1:
            return internal.startswith(rule.tool + "__")
        return internal == rule.tool
    if rule.tool == "edit_file":
        return internal in EDIT_FAMILY
    if rule.tool == "read_file":
        return internal in READ_FAMILY
    return rule.tool == internal


_EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat", ".com")
# Wrappers that only re-dispatch to another program. Deny and ask matching peels
# all of them (options and VAR=value arguments are skipped as well).
_DENY_WRAPPERS = frozenset({"command", "builtin", "exec", "env", "sudo", "nice", "xargs"})
# Allow matching is strict: only wrappers that run the named program unchanged.
_ALLOW_WRAPPERS = frozenset({"command", "builtin", "exec", "env"})
_MAX_FORMS_SCANNED = 8


def _unquote(word: str) -> str:
    return word.replace('"', "").replace("'", "")


def _program(word: str, shell: str) -> str:
    """Name of the program a first word runs: quotes, path, ``.exe`` and case removed."""
    text = _unquote(word).replace("\\", "/")
    name = PurePosixPath(text).name if text else ""
    lowered = name.lower()
    for suffix in _EXECUTABLE_SUFFIXES:
        if lowered.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
            break
    return name.lower() if shell == "cmd" else name


def _deny_forms(subcommand: str, shell: str) -> list[str]:
    """Every reading of a simple command that a deny or ask rule must see.

    The literal text is kept, and so is each form obtained by normalizing the
    program name at a position that wrappers could make the program run from.
    Over-matching here only blocks more, never allows more.
    """
    text = " ".join(str(subcommand).split())
    if not text:
        return [text]
    tokens = text.split(" ")
    forms = [text]
    after_option = False
    index = 0
    while index < min(len(tokens), _MAX_FORMS_SCANNED):
        token = tokens[index]
        forms.append(" ".join([_program(token, shell), *tokens[index + 1:]]))
        skippable = (
            after_option
            or token.startswith("-")
            or bool(_ASSIGNMENT_WORD.match(token))
            or _program(token, shell).lower() in _DENY_WRAPPERS
        )
        if not skippable:
            break
        after_option = token.startswith("-") and not after_option
        index += 1
    return list(dict.fromkeys(forms))


def _allow_forms(subcommand: str, shell: str) -> list[str]:
    """Readings an allow rule may approve: the literal text, plus one exact normalized form.

    A path-qualified program (``./git``, ``/tmp/x/git``) is never reduced to its
    name: a planted binary would otherwise inherit the rule of the real one. The
    normalized form must re-analyse as one simple, non-complex command.
    """
    text = " ".join(str(subcommand).split())
    tokens = text.split(" ") if text else []
    if not tokens:
        return [text]
    index = 0
    env_seen = False
    while index < len(tokens) - 1 and _unquote(tokens[index]) in _ALLOW_WRAPPERS:
        env_seen = env_seen or _unquote(tokens[index]) == "env"
        index += 1
        while env_seen and index < len(tokens) - 1 and _ASSIGNMENT_WORD.match(tokens[index]):
            index += 1
    head = _unquote(tokens[index])
    if not head or "/" in head or "\\" in head:
        return [text]
    if shell == "cmd":
        head = head.lower()
    candidate = " ".join([head, *tokens[index + 1:]])
    if candidate == text:
        return [text]
    analysis = analyze_command(candidate, shell=shell)
    if analysis.complex or analysis.subcommands != (candidate,):
        return [text]
    return [text, candidate]


def _spec_matches(spec: str, command: str) -> bool:
    if spec.endswith(":*"):
        prefix = spec[:-2].strip()
        return not prefix or command == prefix or command.startswith(prefix + " ")
    if spec.endswith(" *"):
        prefix = spec[:-2].strip()
        if command == prefix or command.startswith(prefix + " "):
            return True
    return command == spec or fnmatchcase(command, spec)


def command_matches(specifier: str | None, subcommand: str, *, shell: str = "posix") -> bool:
    """Deny/ask matching: the literal command or any normalized reading of it matches."""
    if specifier is None:
        return True
    spec = " ".join(specifier.split())
    if not spec or spec == "*":
        return True
    return any(_spec_matches(spec, form) for form in _deny_forms(subcommand, shell))


def command_allowed(specifier: str | None, subcommand: str, *, shell: str = "posix") -> bool:
    """Allow matching: strict, see ``_allow_forms``."""
    if specifier is None:
        return True
    spec = " ".join(specifier.split())
    if not spec or spec == "*":
        return True
    return any(_spec_matches(spec, form) for form in _allow_forms(subcommand, shell))


def _target(internal: str, arguments: Mapping[str, Any], ctx: MatchContext) -> Path | None:
    raw = arguments.get("path") or arguments.get("file_path") or arguments.get("notebook_path")
    if not raw and internal in {"glob_files", "search_files", "list_files"}:
        return ctx.workspace_root
    if not raw:
        return None
    candidate = Path(str(raw))
    return candidate if candidate.is_absolute() else ctx.workspace_root / candidate


def rule_matches(
    rule: PermissionRule,
    internal: str,
    arguments: Mapping[str, Any],
    ctx: MatchContext,
    *,
    analysis: ShellAnalysis | None = None,
) -> bool:
    if not rule_applies_to_tool(rule, internal):
        return False
    if rule.specifier is None:
        return True
    spec = rule.specifier
    args = dict(arguments or {})
    if internal == "run_command":
        command = str(args.get("command") or "")
        parsed = analysis or analyze_command(command, shell=ctx.shell)
        if command_matches(spec, command, shell=ctx.shell):
            return True
        return any(command_matches(spec, sub, shell=ctx.shell) for sub in parsed.subcommands)
    if internal in EDIT_FAMILY or internal in READ_FAMILY:
        target = _target(internal, args, ctx)
        return target is not None and path_rule_matches(spec, target, workspace_root=ctx.workspace_root, home=ctx.home)
    if internal == "fetch_url":
        url = str(args.get("url") or "")
        if spec.startswith("domain:"):
            host = (urlparse(url).hostname or "").lower().rstrip(".")
            wanted = spec[len("domain:"):].strip().lower().rstrip(".")
            return bool(host) and bool(wanted) and (host == wanted or host.endswith("." + wanted))
        return fnmatchcase(url, spec)
    if internal == "run_subagent":
        current = str(args.get("subagent_type") or "").strip().lower()
        wanted = _SUBAGENT_TYPES.get(spec.strip().lower(), spec.strip().lower())
        return current == wanted
    if internal == "use_skill":
        skill = str(args.get("skill_id") or "")
        if spec.endswith(":*"):
            return skill.startswith(spec[:-1]) or skill == spec[:-2]
        return skill == spec
    primary = next(iter(args.values()), "") if args else ""
    return fnmatchcase(str(primary), spec)


def suggest_rule(internal: str, arguments: Mapping[str, Any], ctx: MatchContext) -> str:
    from agentos.agentic.tools.public_names import public_name

    args = dict(arguments or {})
    if internal == "run_command":
        analysis = analyze_command(str(args.get("command") or ""), shell=ctx.shell)
        first = analysis.subcommands[0] if analysis.subcommands else ""
        tokens = first.split(" ") if first else []
        if not tokens:
            return "Bash"
        if tokens[0] in _COMPOUND_PREFIXES and len(tokens) >= 2 and not tokens[1].startswith("-"):
            return f"Bash({tokens[0]} {tokens[1]}:*)"
        return f"Bash({tokens[0]}:*)"
    if internal in EDIT_FAMILY:
        target = _target(internal, args, ctx)
        if target is None:
            return "Edit"
        try:
            relative = PurePosixPath(target.resolve().relative_to(ctx.workspace_root.resolve()).as_posix())
        except (OSError, ValueError):
            return "Edit"
        if len(relative.parts) > 1:
            return f"Edit({relative.parts[0]}/**)"
        if relative.suffix:
            return f"Edit(*{relative.suffix})"
        return f"Edit({relative.as_posix()})"
    if internal == "fetch_url":
        host = urlparse(str(args.get("url") or "")).hostname
        return f"WebFetch(domain:{host})" if host else "WebFetch"
    return public_name(internal)

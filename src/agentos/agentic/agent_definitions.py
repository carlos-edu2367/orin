"""Subagent definitions read from disk, in Claude Code's format (W5).

An agent is a ``.md`` file under ``agents/``: YAML frontmatter (``name`` and ``description`` required) and a body
that becomes the subagent's own instructions. Sources, highest precedence first: managed, the project (only once
the workspace is trusted), the user (``~/.orin/agents``, ``~/.claude/agents``), then Orin's builtins. A definition
named like a builtin (``Explore``, ``Plan``, ``general-purpose``) replaces it.

Nothing here runs anything. What a definition may change is bounded by the session: its tools only narrow the
parent's tools, its permission mode can never be more permissive than the parent's (unless the parent bypasses
permissions), and an unknown model alias falls back to the parent's model.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from agentos.skills.disk import split_frontmatter

logger = logging.getLogger(__name__)

MAX_AGENT_DEFINITIONS = 100
MAX_AGENT_FILE_BYTES = 100_000
MAX_AGENT_PROMPT_CHARS = 40_000
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_TOOL = re.compile(r"[A-Za-z_][\w.:-]*")
MODEL_ALIASES = ("sonnet", "opus", "haiku", "fable")
# Least to most permissive. dontAsk refuses whatever would prompt, so it ranks with plan mode below the default.
# Names of Orin's builtin agents. A repository may not replace them (a read-only Explore stays read-only); a
# person's own (user) or managed definition may.
RESERVED_NAMES = frozenset({"explore", "plan", "general-purpose", "general", "fork", "verifier"})
_PERMISSIVENESS = {"plan_mode": 0, "dont_ask": 0, "ask_permissions": 1, "accept_edits": 2, "auto": 3, "full_autonomy": 4}


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    name: str
    description: str
    prompt: str
    origin: str  # builtin | managed | project | user | plugin
    path: Path | None = None
    tools: tuple[str, ...] | None = None  # None: every tool the parent may publish
    disallowed_tools: tuple[str, ...] = ()
    model: str | None = None  # None or "inherit": the parent's model
    permission_mode: str | None = None
    max_turns: int | None = None
    skills: tuple[str, ...] = ()
    background: bool = False
    omit_claude_md: bool = False
    isolation: str | None = None
    effort: str | None = None
    color: str | None = None
    hooks: Mapping[str, Any] | None = None
    mcp_servers: Any = None
    memory: str | None = None
    digest: str = ""


@dataclass(slots=True)
class AgentCatalog:
    agents: dict[str, AgentDefinition] = field(default_factory=dict)
    shadowed: list[tuple[str, str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)

    def get(self, name: str) -> AgentDefinition | None:
        key = str(name or "").strip().lower()
        if key == "general":
            key = "general-purpose"
        return self.agents.get(key)

    def custom(self) -> list[AgentDefinition]:
        return [item for item in self.agents.values() if item.origin != "builtin"]


def _list(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        raw = [str(item) for item in value]
    elif isinstance(value, str):
        raw = _TOOL.findall(value)
    else:
        return ()
    names = []
    for item in raw:
        match = _TOOL.match(item.strip())
        if match:
            names.append(match.group(0))
    return tuple(dict.fromkeys(names))[:128]


def _flag(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in {"true", "yes", "1"})


def _text(value: object, maximum: int = 200) -> str | None:
    if isinstance(value, (str, int, float)) and str(value).strip():
        return str(value).strip()[:maximum]
    return None


def parse_agent_file(path: Path, *, origin: str) -> AgentDefinition:
    data = path.read_bytes() if path.stat().st_size <= MAX_AGENT_FILE_BYTES else None
    if data is None:
        raise ValueError(f"larger than {MAX_AGENT_FILE_BYTES} bytes")
    meta, body = split_frontmatter(data.decode("utf-8-sig"))
    name = (_text(meta.get("name"), 64) or "").lower()
    if not _NAME.fullmatch(name):
        raise ValueError("an agent needs a name of lowercase letters, digits and hyphens")
    description = _text(meta.get("description"), 1_024)
    if not description:
        raise ValueError("an agent needs a description")
    tools = _list(meta.get("tools")) if meta.get("tools") not in (None, "", "*") else None
    turns = meta.get("maxTurns")
    max_turns = int(turns) if isinstance(turns, int) and not isinstance(turns, bool) and 0 < turns <= 500 else None
    return AgentDefinition(
        name=name, description=description, prompt=body.strip()[:MAX_AGENT_PROMPT_CHARS], origin=origin, path=path,
        tools=tools, disallowed_tools=_list(meta.get("disallowedTools")),
        model=_text(meta.get("model"), 120), permission_mode=_text(meta.get("permissionMode"), 40),
        max_turns=max_turns, skills=_list(meta.get("skills")), background=_flag(meta.get("background")),
        omit_claude_md=_flag(meta.get("omitClaudeMd")), isolation=_text(meta.get("isolation"), 20),
        effort=_text(meta.get("effort"), 20), color=_text(meta.get("color"), 20),
        hooks=meta.get("hooks") if isinstance(meta.get("hooks"), dict) else None,
        mcp_servers=meta.get("mcpServers"), memory=_text(meta.get("memory"), 20),
        digest=hashlib.sha256(data).hexdigest(),
    )


VERIFIER = AgentDefinition(
    name="verifier",
    description=(
        "Checks finished work independently: reads what changed, runs the project's tests and checks, and reports "
        "whether the goal is met, with evidence. It never edits files."
    ),
    prompt=(
        "You verify work another agent says is done. Read the relevant changes, run the project's own tests, linters "
        "and builds when they exist, and try the behaviour that was asked for. Report plainly: what you checked, what "
        "passed, what failed (with the exact command and output), and anything you could not check. Do not fix "
        "anything and do not change files."
    ),
    origin="builtin",
    tools=("Read", "Glob", "Grep", "Bash", "PowerShell", "TaskOutput", "WebFetch"),
)
BUILTIN_DEFINITIONS = (VERIFIER,)


def agent_roots(*, workspace_root: Path | None, trusted: bool) -> list[tuple[str, Path]]:
    from agentos.settings.paths import claude_managed_dir, claude_user_dir, orin_managed_dir, orin_user_dir

    roots = [("managed", orin_managed_dir() / "agents"), ("managed", claude_managed_dir() / "agents")]
    if workspace_root is not None and trusted:
        roots += [("project", workspace_root / ".orin" / "agents"), ("project", workspace_root / ".claude" / "agents")]
    roots += [("user", orin_user_dir() / "agents"), ("user", claude_user_dir() / "agents")]
    return roots


def load_agent_definitions(*, workspace_root: Path | None, trusted: bool,
                           plugin_folders: Sequence[tuple[str, Path]] = ()) -> AgentCatalog:
    """Managed, project (trusted), user, then active plugins' agents (W5). A plugin's agent is named
    ``plugin:agent`` and loses ``hooks``, ``mcpServers`` and ``permissionMode``, as in Claude Code."""
    catalog = AgentCatalog()
    roots = [(origin, folder, "") for origin, folder in agent_roots(workspace_root=workspace_root, trusted=trusted)]
    roots += [("plugin", Path(folder), f"{plugin_id}:") for plugin_id, folder in plugin_folders]
    for origin, folder, prefix in roots:
        if not folder.is_dir():
            continue
        try:
            files = sorted(path for path in folder.glob("*.md") if path.is_file() and not path.is_symlink())
        except OSError as error:
            catalog.errors.append((str(folder), f"unreadable: {type(error).__name__}"))
            continue
        for path in files:
            if len(catalog.agents) >= MAX_AGENT_DEFINITIONS:
                catalog.errors.append((str(path), f"more than {MAX_AGENT_DEFINITIONS} agents; the rest are ignored"))
                break
            try:
                definition = parse_agent_file(path, origin=origin)
            except (OSError, ValueError, UnicodeDecodeError) as error:
                catalog.errors.append((str(path), str(error)[:200]))
                continue
            if prefix:
                definition = replace(definition, name=prefix + definition.name, hooks=None, mcp_servers=None, permission_mode=None)
            if origin == "project" and definition.name in RESERVED_NAMES:
                catalog.errors.append((str(path), f"a repository cannot replace Orin's builtin agent '{definition.name}'"))
                continue
            kept = catalog.agents.get(definition.name)
            if kept is not None:
                catalog.shadowed.append((definition.name, str(kept.path), str(path)))
                continue
            catalog.agents[definition.name] = definition
    for builtin in BUILTIN_DEFINITIONS:
        catalog.agents.setdefault(builtin.name, builtin)
    return catalog


def child_permission_mode(parent: str, requested: str | None) -> str:
    """A subagent's mode: the definition's when it is not more permissive than the parent's (W5: no escalation).

    A parent that bypasses permissions may run a child in any mode. An unknown requested mode keeps the parent's.
    """
    from agentos.code_mode.models import normalize_permission_mode

    parent_mode = normalize_permission_mode(parent).value
    if not requested:
        return parent_mode
    try:
        wanted = normalize_permission_mode(str(requested)).value
    except Exception:  # noqa: BLE001 - an unknown mode never widens anything
        return parent_mode
    if parent_mode == "full_autonomy":
        return wanted
    if _PERMISSIVENESS.get(wanted, 99) <= _PERMISSIVENESS.get(parent_mode, -1):
        return wanted
    return parent_mode


def resolve_agent_model(requested: str | None, aliases: Mapping[str, str] | None) -> str | None:
    """The model id a definition asks for: ``inherit`` or nothing is None (the parent's); an alias maps through
    ``modelAliases``, and an alias with no mapping is None too (deviation: no automatic fast model)."""
    value = str(requested or "").strip()
    if not value or value.lower() == "inherit":
        return None
    mapping = {str(key).lower(): str(item) for key, item in dict(aliases or {}).items() if isinstance(item, str) and item.strip()}
    if value.lower() in mapping:
        return mapping[value.lower()].strip()
    if value.lower() in MODEL_ALIASES:
        return None
    return value


class AgentToolPolicy:
    """Narrows the tools a subagent may publish to its definition's ``tools`` minus ``disallowedTools``."""

    def __init__(self, base: object | None, definition: AgentDefinition) -> None:
        from agentos.agentic.tools.public_names import internal_name

        self._base = base
        self._allowed = None if definition.tools is None else {internal_name(item) for item in definition.tools} | set(definition.tools)
        self._denied = {internal_name(item) for item in definition.disallowed_tools} | set(definition.disallowed_tools)

    def _matches(self, name: str, names: set[str]) -> bool:
        return name in names or any(item.startswith("mcp__") and (name == item or name.startswith(item.rstrip("*") if item.endswith("*") else item + "__")) for item in names)

    def allows(self, name: str, tags: tuple[str, ...]) -> bool:
        if self._base is not None and not self._base.allows(name, tags):
            return False
        if self._matches(name, self._denied):
            return False
        return self._allowed is None or self._matches(name, self._allowed)


__all__ = [
    "AgentCatalog", "AgentDefinition", "AgentToolPolicy", "BUILTIN_DEFINITIONS", "MAX_AGENT_DEFINITIONS", "VERIFIER",
    "agent_roots", "child_permission_mode", "load_agent_definitions", "parse_agent_file", "resolve_agent_model",
]

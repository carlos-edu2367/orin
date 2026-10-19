from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from enum import StrEnum
import hashlib
import json
import re

MAX_PLUGIN_ID_LENGTH = 64


def plugin_id_from_name(name: str) -> str:
    value = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", str(name).lower())).strip("-")
    if not value:
        raise ValueError("plugin name does not produce an identifier")
    return value[:MAX_PLUGIN_ID_LENGTH].rstrip("-")


class PluginState(StrEnum):
    INSPECTED = "inspected"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    DISABLED = "disabled"
    REJECTED = "rejected"

    def can_transition_to(self, target: "PluginState") -> bool:
        return PluginState(target) in _TRANSITIONS[self]


_TRANSITIONS = {
    PluginState.INSPECTED: frozenset({PluginState.PENDING_APPROVAL, PluginState.REJECTED}),
    PluginState.PENDING_APPROVAL: frozenset({PluginState.ACTIVE, PluginState.REJECTED}),
    PluginState.ACTIVE: frozenset({PluginState.DISABLED}),
    PluginState.DISABLED: frozenset({PluginState.ACTIVE, PluginState.REJECTED}),
    PluginState.REJECTED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class PluginRef:
    plugin_id: str
    version: str

    def __str__(self) -> str:
        return f"{self.plugin_id}@{self.version}"


@dataclass(frozen=True, slots=True)
class SkillContribution:
    skill_id: str
    name: str
    description: str
    relative_path: str


@dataclass(frozen=True, slots=True)
class McpServerContribution:
    slug: str
    display_name: str
    transport: str
    command: str | None
    args: tuple[str, ...]
    url: str | None
    secret_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentContribution:
    agent_id: str
    name: str
    role: str
    relative_path: str


@dataclass(frozen=True, slots=True)
class CommandContribution:
    command_id: str        # "{plugin_id}:{slug}"
    slug: str
    description: str
    argument_hint: str
    relative_path: str


@dataclass(frozen=True, slots=True)
class HookContribution:
    hook_id: str            # "{plugin_id}:{event}:{index}" in declaration order
    event: str               # any hook event (W5), e.g. PreToolUse, SessionStart
    matcher: str              # the event's matcher, as in settings hooks; "" matches everything
    command: str               # raw declared string, ${CLAUDE_PLUGIN_ROOT} unresolved
    timeout_seconds: int
    run_async: bool = False   # W5: "async": true runs it in the background, like a settings hook

    @property
    def consent_reference(self) -> str:
        """The consent row's reference: the hook id plus a digest of what it runs (event, matcher, command, timeout).

        A hook runs only while a row with this exact reference is enabled, so a changed hook needs a new consent.
        """
        content = json.dumps([self.event, self.matcher, self.command, self.timeout_seconds])
        return f"{self.hook_id}#{hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]}"


@dataclass(frozen=True, slots=True)
class LspServerContribution:
    """A language server a plugin declares in ``.lsp.json`` (W5-R6). It is run by the plugin, so it needs its own consent."""

    name: str                 # the key it is declared under
    command: str              # as declared, ${CLAUDE_PLUGIN_ROOT} unresolved
    args: tuple[str, ...]
    extensions: tuple[tuple[str, str], ...]  # (".py", "python")
    env: tuple[tuple[str, str], ...]
    initialization_options: Any
    settings: Any
    startup_timeout: float
    digest: str               # first 16 hex of sha256 over the declared entry: a changed entry needs a new consent

    @property
    def consent_reference(self) -> str:
        return f"{self.name}#{self.digest}"


@dataclass(frozen=True, slots=True)
class PluginInspection:
    ref: PluginRef
    display_name: str
    description: str
    author: str
    homepage: str | None
    package_digest: str
    skills: tuple[SkillContribution, ...] = ()
    mcp_servers: tuple[McpServerContribution, ...] = ()
    agents: tuple[AgentContribution, ...] = ()
    commands: tuple[CommandContribution, ...] = ()
    hooks: tuple[HookContribution, ...] = ()
    warnings: tuple[str, ...] = ()
    options: tuple[Any, ...] = ()  # W5: the manifest's userConfig options (manifest.UserConfigOption)
    lsp_servers: tuple[LspServerContribution, ...] = ()  # W5-R6: the language servers in .lsp.json

    @property
    def contribution_count(self) -> int:
        return len(self.skills) + len(self.mcp_servers) + len(self.agents) + len(self.commands) + len(self.hooks) + len(self.lsp_servers)

    @property
    def is_installable(self) -> bool:
        return self.contribution_count > 0

    @property
    def requires_approval(self) -> bool:
        return self.is_installable

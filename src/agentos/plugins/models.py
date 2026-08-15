from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
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
    warnings: tuple[str, ...] = ()

    @property
    def contribution_count(self) -> int:
        return len(self.skills) + len(self.mcp_servers) + len(self.agents)

    @property
    def is_installable(self) -> bool:
        return self.contribution_count > 0

    @property
    def requires_approval(self) -> bool:
        return self.is_installable

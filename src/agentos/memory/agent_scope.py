"""Per-agent memory scope keys, mode resolution, settings helpers, and scoped view (WS-D5 / D-RF9).

Domain-only module: contains no concrete persistence or transport dependencies.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Any, Literal

from agentos.settings.registry import resolve as resolve_setting

VALID_AGENT_MEMORY_MODES: tuple[str, ...] = ("project", "user", "local", "none")
ACTIVE_AGENT_MEMORY_MODES: tuple[str, ...] = ("project", "user", "local")
AgentMemoryMode = Literal["project", "user", "local"]

BUILTIN_AGENTS_WITHOUT_MEMORY: frozenset[str] = frozenset({
    "explore",
    "plan",
    "general",
    "general-purpose",
    "fork",
})
BUILTIN_AGENTS_WITH_DEFAULT_PROJECT_MEMORY: frozenset[str] = frozenset({"verifier"})


def workspace_root_hash(workspace_root: Path | str | None) -> str:
    """Return a stable deterministic hash for the normalized workspace root path."""
    if workspace_root is None or not str(workspace_root).strip():
        return "default"
    try:
        resolved = Path(workspace_root).resolve(strict=False)
        normalized = os.path.normcase(str(resolved)).replace("\\", "/").rstrip("/") or "/"
    except Exception:  # noqa: BLE001
        normalized = str(workspace_root).strip().replace("\\", "/").rstrip("/") or "default"
    return hashlib.blake2b(normalized.encode("utf-8"), digest_size=16).hexdigest()


def resolve_agent_memory_mode(
    agent_name: str,
    declared_memory: str | None,
    *,
    is_builtin: bool = False,
) -> str:
    """Resolve and validate the effective memory mode for an agent definition."""
    if declared_memory is not None and str(declared_memory).strip():
        candidate = str(declared_memory).strip().lower()
        if candidate not in VALID_AGENT_MEMORY_MODES:
            allowed = ", ".join(VALID_AGENT_MEMORY_MODES)
            raise ValueError(f"memory must be one of: {allowed}")
        return candidate

    normalized_name = str(agent_name or "").strip().lower()
    if is_builtin:
        if normalized_name in BUILTIN_AGENTS_WITH_DEFAULT_PROJECT_MEMORY:
            return "project"
        return "none"
    return "project"


def _extract_settings_data(effective_settings: Any | None) -> Mapping[str, Any]:
    if effective_settings is None:
        return {}
    if isinstance(effective_settings, Mapping):
        return effective_settings
    data = getattr(effective_settings, "data", None)
    return data if isinstance(data, Mapping) else {}


def is_agent_memory_enabled(effective_settings: Any | None = None, *, default: bool = False) -> bool:
    """Return effective value of ``memory.agent.enabled`` (defaults to False)."""
    if effective_settings is None:
        return default
    try:
        return bool(resolve_setting(effective_settings, "memory.agent.enabled").value)
    except Exception:  # noqa: BLE001
        raw = _extract_settings_data(effective_settings).get("memory")
        if isinstance(raw, Mapping):
            agent_cfg = raw.get("agent")
            if isinstance(agent_cfg, Mapping) and "enabled" in agent_cfg:
                return bool(agent_cfg["enabled"])
        return default


def agent_memory_max_prompt_chars(effective_settings: Any | None = None, *, default: int = 4000) -> int:
    """Return effective value of ``memory.agent.maxPromptChars`` (defaults to 4000)."""
    if effective_settings is None:
        return max(0, int(default))
    try:
        return max(0, int(resolve_setting(effective_settings, "memory.agent.maxPromptChars").value))
    except Exception:  # noqa: BLE001
        raw = _extract_settings_data(effective_settings).get("memory")
        if isinstance(raw, Mapping):
            agent_cfg = raw.get("agent")
            if isinstance(agent_cfg, Mapping) and agent_cfg.get("maxPromptChars") is not None:
                try:
                    return max(0, int(agent_cfg["maxPromptChars"]))
                except (TypeError, ValueError):
                    pass
        return max(0, int(default))


def is_learn_from_verified_only(effective_settings: Any | None = None, *, default: bool = True) -> bool:
    """Return effective value of ``memory.agent.learnFromVerifiedOnly`` (defaults to True)."""
    if effective_settings is None:
        return default
    try:
        return bool(resolve_setting(effective_settings, "memory.agent.learnFromVerifiedOnly").value)
    except Exception:  # noqa: BLE001
        raw = _extract_settings_data(effective_settings).get("memory")
        if isinstance(raw, Mapping):
            agent_cfg = raw.get("agent")
            if isinstance(agent_cfg, Mapping) and "learnFromVerifiedOnly" in agent_cfg:
                return bool(agent_cfg["learnFromVerifiedOnly"])
        return default


@dataclass(frozen=True, slots=True)
class AgentMemoryScope:
    """Canonical scope descriptor for an agent's own memory partition (D-RF9)."""

    user_id: str
    project_id: str | None
    workspace_hash: str | None
    agent_name: str
    mode: AgentMemoryMode = "project"

    def __post_init__(self) -> None:
        clean_user = str(self.user_id or "").strip()
        if not clean_user:
            raise ValueError("user_id must be a non-empty string")
        clean_agent = str(self.agent_name or "").strip().lower()
        if not clean_agent:
            raise ValueError("agent_name must be a non-empty string")
        clean_mode = str(self.mode or "").strip().lower()
        if clean_mode not in ACTIVE_AGENT_MEMORY_MODES:
            raise ValueError(f"AgentMemoryScope.mode must be one of {ACTIVE_AGENT_MEMORY_MODES}, got {self.mode!r}")

        clean_project = str(self.project_id).strip() if self.project_id and str(self.project_id).strip() else None
        clean_ws = str(self.workspace_hash).strip() if self.workspace_hash and str(self.workspace_hash).strip() else None

        # Spec D-RF9: a conversation without a project falls back from 'project' to 'local'.
        if clean_mode == "project" and clean_project is None:
            clean_mode = "local"

        if clean_mode == "user":
            clean_project = None
        elif clean_mode == "local":
            clean_project = None
            if clean_ws is None:
                clean_ws = "default"

        object.__setattr__(self, "user_id", clean_user)
        object.__setattr__(self, "agent_name", clean_agent)
        object.__setattr__(self, "mode", clean_mode)
        object.__setattr__(self, "project_id", clean_project)
        object.__setattr__(self, "workspace_hash", clean_ws)

    @property
    def scope_type(self) -> Literal["agent"]:
        return "agent"

    @property
    def agent_key(self) -> str:
        return self.agent_name

    @property
    def effective_project_id(self) -> str | None:
        return self.project_id if self.mode == "project" else None

    @property
    def scope_id(self) -> str:
        if self.mode == "project":
            return f"project:{self.project_id}:{self.agent_name}"
        if self.mode == "user":
            return f"user:{self.agent_name}"
        return f"local:{self.workspace_hash or 'default'}:{self.agent_name}"

    @property
    def lock_key(self) -> str:
        return f"{self.user_id}:{self.scope_id}"

    @classmethod
    def from_context(
        cls,
        *,
        user_id: str,
        agent_name: str,
        mode: str | None,
        project_id: str | None = None,
        workspace_root: Path | str | None = None,
        workspace_hash: str | None = None,
        is_builtin: bool = False,
    ) -> AgentMemoryScope | None:
        effective = resolve_agent_memory_mode(agent_name, mode, is_builtin=is_builtin)
        if effective == "none":
            return None
        ws_hash = (
            str(workspace_hash).strip()
            if workspace_hash and str(workspace_hash).strip()
            else workspace_root_hash(workspace_root)
        )
        return cls(
            user_id=user_id,
            project_id=project_id,
            workspace_hash=ws_hash,
            agent_name=agent_name,
            mode=effective,  # type: ignore[arg-type]
        )


class AgentScopedMemoryView:
    """Memory facade handed to a subagent with its own memory enabled (D-RF9 / D-RF10).

    - Writes are restricted strictly to the child's ``agent`` scope (as proposals).
    - Reads combine the child's active ``agent`` memories with the parent's
      read-only ``project`` and ``user`` scopes (agent > project > user priority).
    """

    def __init__(
        self,
        parent_store: Any,
        agent_scope: AgentMemoryScope,
        *,
        propose_only: bool = True,
    ) -> None:
        self._parent = parent_store
        self._agent_scope = agent_scope
        self._propose_only = bool(propose_only)

    @property
    def agent_scope(self) -> AgentMemoryScope:
        return self._agent_scope

    @property
    def parent_store(self) -> Any:
        return self._parent

    def active_agent_memories(self) -> list[dict[str, Any]]:
        loader = getattr(self._parent, "active_for_agent_scope", None)
        if callable(loader):
            return list(loader(self._agent_scope))
        return []

    def save(
        self,
        fact: str,
        tags: Sequence[str] = (),
        *,
        scope_type: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        requested = str(scope_type).strip().lower() if scope_type is not None and str(scope_type).strip() else "agent"
        if requested != "agent":
            raise PermissionError(
                "Parent 'user' and 'project' memory scopes are read-only inside this subagent; use scope='agent'."
            )
        save_kwargs = dict(kwargs)
        save_kwargs["scope_type"] = "agent"
        save_kwargs["agent_key"] = self._agent_scope.agent_key
        save_kwargs["agent_scope"] = self._agent_scope
        save_kwargs["scope_id"] = self._agent_scope.scope_id
        if self._propose_only:
            save_kwargs["propose_only"] = True
        return self._parent.save(fact, tags, **save_kwargs)

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        scope: str | None = None,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._parent.search(
            query,
            limit=limit,
            scope=scope,
            conversation_id=conversation_id,
            agent_scope=self._agent_scope,
            agent_key=self._agent_scope.agent_key,
        )

    def relevant(
        self,
        task: str,
        *,
        limit: int = 12,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._parent.relevant(
            task,
            limit=limit,
            conversation_id=conversation_id,
            agent_scope=self._agent_scope,
            agent_key=self._agent_scope.agent_key,
        )

    def get_memory(
        self,
        memory_id: str,
        *,
        conversation_id: str | None = None,
        record_usage: bool = True,
    ) -> dict[str, Any] | None:
        item = self._parent.get_memory(
            memory_id,
            conversation_id=conversation_id,
            record_usage=record_usage,
        )
        if not item:
            return None
        if str(item.get("scope_type") or "") == "agent":
            if str(item.get("agent_key") or "").strip().lower() != self._agent_scope.agent_key:
                return None
            item_scope_id = item.get("scope_id")
            if item_scope_id and str(item_scope_id) != self._agent_scope.scope_id:
                return None
        return item

    def forget(self, memory_id: str, *, actor: str = "agent") -> bool:
        _ = (memory_id, actor)
        raise PermissionError("Subagents cannot delete parent memories.")


__all__ = [
    "ACTIVE_AGENT_MEMORY_MODES",
    "BUILTIN_AGENTS_WITHOUT_MEMORY",
    "BUILTIN_AGENTS_WITH_DEFAULT_PROJECT_MEMORY",
    "VALID_AGENT_MEMORY_MODES",
    "AgentMemoryMode",
    "AgentMemoryScope",
    "AgentScopedMemoryView",
    "agent_memory_max_prompt_chars",
    "is_agent_memory_enabled",
    "is_learn_from_verified_only",
    "resolve_agent_memory_mode",
    "workspace_root_hash",
]

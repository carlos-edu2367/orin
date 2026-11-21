"""Subagent worktree isolation & merge lifecycle glue for TurnSession (WS-B1/WS-B2)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentos.agentic.background_agents import BACKGROUND_AGENTS
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.agent_worktree import (
    AgentWorktree,
    AgentWorktreeManager,
    IsolationUnavailableError,
)
from agentos.code_mode.merge_queue import MergeQueue, MergeResult
from agentos.permissions.engine import PermissionEngine
from agentos.persistence.postgres.agent_worktrees import AgentWorktreeStore
from agentos.settings.keys_worktree import DEFAULT_WORKTREE_LINK_DIRS
from agentos.settings.registry import resolve as resolve_setting


@dataclass(slots=True)
class ActiveSubagentWorktree:
    worktree: AgentWorktree
    manager: AgentWorktreeManager
    workspace: ConversationWorkspace
    link_dirs: tuple[str, ...]
    merge_check_command: str | None


def _worktree_store(session: Any) -> AgentWorktreeStore | None:
    store = getattr(session, "_agent_worktree_store", None)
    if store is not None:
        return store
    for candidate in (getattr(session, "store", None), getattr(session, "agents_store", None)):
        engine = getattr(candidate, "_engine", None) or getattr(candidate, "engine", None)
        if engine is not None:
            created = AgentWorktreeStore(engine)
            session._agent_worktree_store = created
            return created
    return None


def _worktree_settings(session: Any) -> tuple[bool, tuple[str, ...], str | None]:
    settings = getattr(session, "effective_settings", None)
    if settings is None:
        return False, DEFAULT_WORKTREE_LINK_DIRS, None
    enabled = bool(resolve_setting(settings, "orchestration.worktree.enabled").value)
    raw_links = resolve_setting(settings, "orchestration.worktree.linkDirs").value
    link_dirs = (
        tuple(str(item) for item in raw_links if isinstance(item, str) and item.strip())
        if isinstance(raw_links, (list, tuple))
        else DEFAULT_WORKTREE_LINK_DIRS
    )
    raw_cmd = resolve_setting(settings, "orchestration.worktree.mergeCheckCommand").value
    merge_cmd = str(raw_cmd).strip() if isinstance(raw_cmd, str) and raw_cmd.strip() else None
    if merge_cmd is not None:
        trusted_fn = getattr(session, "_workspace_trusted", None)
        if callable(trusted_fn) and not trusted_fn():
            merge_cmd = None
    return enabled, link_dirs, merge_cmd


def prepare_subagent_worktree(
    session: Any,
    *,
    agent_id: str,
    parent_agent_id: str | None,
    requested_isolation: str | None,
    definition: Any,
) -> ActiveSubagentWorktree | None:
    """Create an isolated worktree when enabled and requested, or emit ``isolation.unavailable``."""
    enabled, link_dirs, merge_cmd = _worktree_settings(session)
    if not enabled:
        return None
    effective_isolation = (
        str(requested_isolation).strip().lower()
        if requested_isolation is not None and str(requested_isolation).strip()
        else str(getattr(definition, "isolation", None) or "").strip().lower()
    )
    if effective_isolation != "worktree":
        return None

    parent_root = Path(session.workspace.root).resolve()
    store = _worktree_store(session)
    manager = AgentWorktreeManager(parent_root, store=store)
    try:
        wt = manager.create_for_agent(agent_id, parent_root, link_dirs=link_dirs, store=store)
    except IsolationUnavailableError as exc:
        session._record(
            AgentActivityEventType.ISOLATION_UNAVAILABLE,
            f"Isolamento por worktree indisponível ({exc.reason}); executando no workspace principal",
            {"agent_id": agent_id, "reason": exc.reason, "message": str(exc)},
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
        )
        return None

    # Update BackgroundAgentEntry.worktree_path when running in background.
    conv_id = str(session.turn.get("conversation_id") or "")
    bg_entry = BACKGROUND_AGENTS.get(conv_id, agent_id) if conv_id else None
    if bg_entry is not None:
        bg_entry.worktree_path = str(wt.path)

    session._record(
        AgentActivityEventType.WORKTREE_CREATED,
        f"Worktree isolada criada ({wt.branch})",
        {"agent_id": agent_id, "worktree_path": str(wt.path), "branch": wt.branch, "base_commit": wt.base_commit},
        agent_id=agent_id,
        parent_agent_id=parent_agent_id,
    )
    child_workspace = ConversationWorkspace.at_root(
        wt.path,
        unrestricted=bool(getattr(session.workspace, "unrestricted", False)),
    )
    return ActiveSubagentWorktree(
        worktree=wt,
        manager=manager,
        workspace=child_workspace,
        link_dirs=link_dirs,
        merge_check_command=merge_cmd,
    )


def worktree_prompt_notice(active: ActiveSubagentWorktree | None) -> str:
    """Warning injected into the subagent system prompt about shared read-only ``linkDirs``."""
    if active is None:
        return ""
    dirs = ", ".join(active.link_dirs) if active.link_dirs else "node_modules, .venv"
    return (
        f"\nYou are running inside an isolated Git worktree at `{active.worktree.path}` (branch `{active.worktree.branch}`). "
        f"Shared dependency directories ({dirs}) are linked read-only to the parent workspace: "
        "do NOT run `npm install`, `uv sync`, `pip install`, or mutate files inside those linked directories."
    )


def _authorize_merge_check_command(session: Any, command: str) -> tuple[bool, str]:
    """Check ``mergeCheckCommand`` through the exact same permission policy as ``Bash``."""
    settings = getattr(session, "effective_settings", None)
    parent_root = Path(session.workspace.root).resolve()
    perm_mode = str(getattr(session, "permission_mode", "ask_permissions") or "ask_permissions")
    args = {"command": command}

    if settings is not None:
        engine = PermissionEngine(parent_root, settings)
        decision = engine.decide("run_command", args, mode=perm_mode)
        if decision.behavior == "allow":
            return True, decision.reason
        if decision.behavior == "deny":
            return False, decision.reason

        broker = getattr(session, "_active_permission_broker", None)
        interactive = bool(session.turn.get("interactive_permissions"))
        if broker is not None and interactive:
            reply = broker.request_and_wait(
                tool_name="run_command",
                arguments=args,
                turn_id=str(session.turn.get("turn_id") or ""),
                conversation_id=str(session.turn.get("conversation_id") or ""),
                permission_mode=perm_mode,
            )
            allowed = bool(reply.get("allowed"))
            return allowed, str(reply.get("reason") or ("approved" if allowed else "denied by user"))
        return False, decision.reason or "command requires interactive approval"

    return perm_mode in {"full_autonomy", "bypassPermissions"}, "permission policy requires approval"


def finalize_subagent_worktree(
    session: Any,
    active: ActiveSubagentWorktree | None,
    *,
    agent_id: str,
    parent_agent_id: str | None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Clean up or integrate ``active`` worktree when a subagent completes, emitting events."""
    if active is None:
        return None, ""

    store = _worktree_store(session)
    if not active.manager.has_agent_changes(active.worktree.path):
        active.manager.cleanup_if_unchanged(agent_id, store=store)
        session._record(
            AgentActivityEventType.WORKTREE_REMOVED,
            f"Worktree removida sem alterações ({active.worktree.branch})",
            {"agent_id": agent_id, "branch": active.worktree.branch, "merge_state": "empty"},
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
        )
        return {"merge_state": "empty", "merge_detail": {"reason": "no_changes"}, "worktree_path": None}, ""

    queue = MergeQueue(
        active.worktree.parent_root,
        manager=active.manager,
        store=store,
        merge_check_command=active.merge_check_command,
        command_authorizer=lambda cmd: _authorize_merge_check_command(session, cmd),
    )
    result: MergeResult = queue.enqueue(agent_id, cancelled=cancelled)
    wt_payload = result.to_payload()

    if result.state == "applied":
        session._record(
            AgentActivityEventType.MERGE_APPLIED,
            f"Alterações da worktree integradas ({len(result.files)} arquivo(s))",
            {"agent_id": agent_id, "files": list(result.files), "branch": active.worktree.branch},
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
        )
        session._record(
            AgentActivityEventType.WORKTREE_REMOVED,
            f"Worktree removida após integração ({active.worktree.branch})",
            {"agent_id": agent_id, "branch": active.worktree.branch, "merge_state": "applied"},
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
        )
        return wt_payload, f"\n\n[Worktree merged: {len(result.files)} file(s) applied: {', '.join(result.files)}]"

    if result.state == "conflict":
        session._record(
            AgentActivityEventType.MERGE_CONFLICT,
            f"Conflito ao integrar worktree ({', '.join(result.files)})",
            {
                "agent_id": agent_id,
                "files": list(result.files),
                "worktree_path": result.worktree_path,
                "branch": active.worktree.branch,
                "conflict_detail": result.conflict_detail,
            },
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
        )
        note = (
            f"\n\n[Worktree merge CONFLICT in {', '.join(result.files)}. "
            f"Nothing was applied to the parent workspace. Worktree kept at `{result.worktree_path}`.]"
        )
        return wt_payload, note

    if result.state == "check_failed":
        session._record(
            AgentActivityEventType.MERGE_CHECK_FAILED,
            "Checagem de integração falhou; alterações revertidas",
            {
                "agent_id": agent_id,
                "files": list(result.files),
                "worktree_path": result.worktree_path,
                "branch": active.worktree.branch,
                "detail": result.conflict_detail,
            },
            agent_id=agent_id,
            parent_agent_id=parent_agent_id,
        )
        note = (
            f"\n\n[Worktree merge check FAILED; parent workspace was reverted. "
            f"Worktree kept at `{result.worktree_path}`.]"
        )
        return wt_payload, note

    session._record(
        AgentActivityEventType.WORKTREE_REMOVED,
        f"Worktree removida sem alterações ({active.worktree.branch})",
        {"agent_id": agent_id, "branch": active.worktree.branch, "merge_state": "empty"},
        agent_id=agent_id,
        parent_agent_id=parent_agent_id,
    )
    return wt_payload, ""


__all__ = [
    "ActiveSubagentWorktree",
    "finalize_subagent_worktree",
    "prepare_subagent_worktree",
    "worktree_prompt_notice",
]

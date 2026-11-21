"""Session helpers bridging TurnSession with WS-D5 per-agent memory and promotion."""

from __future__ import annotations

from typing import Any

from agentos.memory.agent_scope import (
    AgentMemoryScope,
    AgentScopedMemoryView,
    agent_memory_max_prompt_chars,
    is_agent_memory_enabled,
    is_learn_from_verified_only,
)
from agentos.memory.agent_summary import freeze_summary
from agentos.memory.promotion import MemoryPromotionGate


def build_subagent_memory_context(
    session: Any,
    clean: str,
    builtin_key: str,
    definition: Any,
) -> tuple[AgentScopedMemoryView | None, str]:
    """Return ``(scoped_memory_view, frozen_prompt_summary)`` for a subagent run.

    Returns ``(None, "")`` whenever ``memory.agent.enabled`` is False or the
    agent's effective memory mode is ``none``, preserving 0.12.31 behaviour.
    """
    parent_memory = getattr(session, "memory", None)
    effective_settings = getattr(session, "effective_settings", None)
    if parent_memory is None or not is_agent_memory_enabled(effective_settings):
        return None, ""

    turn = getattr(session, "turn", None) or {}
    user_id = str(turn.get("user_id") or "").strip()
    if not user_id:
        return None, ""
    project_id = str(turn.get("project_id") or "").strip() or None
    workspace = getattr(session, "workspace", None)
    workspace_root = getattr(workspace, "root", None) if workspace is not None else None

    is_builtin = definition is None or getattr(definition, "origin", None) == "builtin"
    agent_name = str(getattr(definition, "name", None) or builtin_key or clean)
    declared_mode = getattr(definition, "memory", None) if definition is not None else None

    scope = AgentMemoryScope.from_context(
        user_id=user_id,
        agent_name=agent_name,
        mode=declared_mode,
        project_id=project_id,
        workspace_root=workspace_root,
        is_builtin=is_builtin,
    )
    if scope is None:
        return None, ""

    view = AgentScopedMemoryView(parent_memory, scope, propose_only=True)
    max_chars = agent_memory_max_prompt_chars(effective_settings)
    summary = freeze_summary(view.active_agent_memories(), scope.agent_name, max_chars=max_chars)
    return view, summary


def subagent_memory_turn_state(session: Any, scope: AgentMemoryScope) -> dict[str, Any]:
    """Isolated provenance state for a subagent with its own memory scope."""
    turn = getattr(session, "turn", None) or {}
    workspace = getattr(session, "workspace", None)
    workspace_root = getattr(workspace, "root", None) if workspace is not None else None
    return {
        "effective_settings": getattr(session, "effective_settings", None),
        "turn_id": str(turn.get("turn_id") or "") or None,
        "conversation_id": str(turn.get("conversation_id") or "") or None,
        "workspace_root": str(workspace_root) if workspace_root is not None else None,
        "agent_key": scope.agent_key,
        "actor": f"agent:{scope.agent_key}",
    }


def promote_session_agent_memories(session: Any, verdict: bool | str | None) -> None:
    """Invoke ``MemoryPromotionGate.on_verified`` for the session's current turn."""
    if isinstance(verdict, bool):
        session._last_verification_passed = verdict
    parent_memory = getattr(session, "memory", None)
    effective_settings = getattr(session, "effective_settings", None)
    if parent_memory is None or not is_agent_memory_enabled(effective_settings):
        return
    engine = getattr(parent_memory, "_engine", None)
    turn = getattr(session, "turn", None) or {}
    turn_id = str(turn.get("turn_id") or "").strip()
    if engine is None or not turn_id:
        return
    user_id = str(turn.get("user_id") or "").strip() or None
    learn_only = is_learn_from_verified_only(effective_settings, default=True)
    MemoryPromotionGate(engine, user_id=user_id).on_verified(
        turn_id,
        verdict,
        learn_from_verified_only=learn_only,
    )


def maybe_promote_on_turn_complete(session: Any) -> None:
    """Promote turn proposals on successful turn completion when ``learnFromVerifiedOnly`` is False."""
    effective_settings = getattr(session, "effective_settings", None)
    if getattr(session, "_last_verification_passed", None) is False:
        return
    if not is_learn_from_verified_only(effective_settings, default=True):
        promote_session_agent_memories(session, None)


def propagate_subagent_external_taint(session: Any, caller: Any, subagent_tools: Any) -> None:
    """Propagate untrusted external tool taint (web/tool/file) from a subagent run to the parent turn.

    Purely local subagent work (reading project files, running local grep/tests) propagates nothing,
    so parent Remember calls remain clean ``source_kind='agent'``.
    """
    from agentos.memory.provenance import infer_source_kind

    sub_history = getattr(subagent_tools, "_turn_tool_history", None) or ()
    if not sub_history:
        return
    ws = getattr(subagent_tools, "workspace", None) or getattr(session, "workspace", None)
    root = getattr(ws, "root", None) if ws is not None else None
    ws_root_str = str(root) if root is not None else None
    tainted_entries: list[dict[str, Any]] = []
    for entry in sub_history:
        if not isinstance(entry, dict):
            continue
        sk, _, _ = infer_source_kind({"tool_calls": [entry], "workspace_root": ws_root_str})
        if sk in {"web", "tool", "file"}:
            tainted_entries.append(dict(entry))
    if not tainted_entries:
        return
    caller_toolset = getattr(caller, "toolset", None) if caller is not None else None
    caller_hist = getattr(caller_toolset, "_turn_tool_history", None)
    if isinstance(caller_hist, list):
        caller_hist.extend(tainted_entries)
    existing = getattr(session, "_turn_external_tool_entries", None)
    if not isinstance(existing, list):
        existing = []
        setattr(session, "_turn_external_tool_entries", existing)
    existing.extend(tainted_entries)


__all__ = [
    "build_subagent_memory_context",
    "maybe_promote_on_turn_complete",
    "promote_session_agent_memories",
    "propagate_subagent_external_taint",
    "subagent_memory_turn_state",
]

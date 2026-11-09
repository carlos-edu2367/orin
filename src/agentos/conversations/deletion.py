"""Deleting a conversation: every row that exists only because of it goes, in one transaction.

The schema has no foreign keys between conversation tables, so each one is cleaned here by name. What is *not* removed:

* files on disk (project folders, the private workspace, attachment files) — the interface promises they stay;
* durable memories (``agent_memories``) — they are facts about the person, scoped to the user, a project or the
  Friend; only their ``conversation_id`` provenance is cleared;
* the person's own schedules — they lose the binding and their next run opens a new conversation, as a schedule that
  never ran does; the schedules a tool made for this conversation (wakeups, monitor wakes, subagents) are cancelled;
* the execution ledger (``persistence_*``, ``dispatches``, ``schedule_occurrences``, ``event_stream_bindings``): it is
  the system of record and audit trail for executions, carries no conversation id and is not read per conversation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine

from agentos.api.contracts import ApplicationConflictError, ApplicationNotFoundError
from agentos.persistence.postgres.schema import (
    agent_mailbox, agent_memories, code_mode_checks, code_mode_runs, conversation_activity_events, conversation_agent_usage,
    conversation_agents, conversation_compact_boundaries, conversation_directories, conversation_dispatches, conversation_events,
    conversation_hook_context, conversation_input_queue, conversation_message_attachments, conversation_message_commands,
    conversation_messages, conversation_prompt_snapshots, conversation_sessions, conversation_tool_records, conversation_turn_steps,
    conversation_turns, conversations, execution_checkpoints, execution_effects, execution_skills, multi_agent_events,
    scheduled_chat_tasks, schedules, tool_activity_events, tool_invocations, turn_quality_metrics,
)

# A turn in one of these states still has a worker (or will soon): the person must stop the session first.
LIVE_STATES = ("queued", "starting", "running", "cancelling")
# Mirrors agentos.scheduler.scheduled_chats.TOOL_SCHEDULE_SOURCES (that module imports chat, so it cannot be imported here).
TOOL_SCHEDULE_SOURCES = frozenset({"wakeup", "monitor", "subagent"})
BUSY_MESSAGE = "Esta sessão ainda está em andamento: pare a sessão antes de apagá-la."
FRIEND_MESSAGE = "A conversa do Orin Friend não pode ser apagada."

# Tables whose rows carry the conversation id (and the user id) and mean nothing without the conversation.
_BY_CONVERSATION = (
    conversation_messages, conversation_message_commands, conversation_hook_context, code_mode_runs, conversation_tool_records,
    conversation_message_attachments, conversation_input_queue, conversation_prompt_snapshots, conversation_compact_boundaries,
    conversation_sessions, conversation_directories, agent_mailbox, turn_quality_metrics, conversation_turn_steps,
    conversation_events, conversation_activity_events, conversation_agents, conversation_agent_usage, conversation_turns,
)
# Tables keyed by (user_id, execution_id) for the executions this conversation's turns and agents ran.
_BY_EXECUTION = (execution_checkpoints, execution_effects, execution_skills, tool_invocations, tool_activity_events, multi_agent_events)


def _scoped(table, conversation_id: str, user_id: str):
    clauses = [table.c.conversation_id == conversation_id]
    if "user_id" in table.c:
        clauses.append(table.c.user_id == user_id)
    return clauses


def delete_conversation(engine: Engine, conversation_id: str, user_id: str) -> dict[str, object]:
    """Hard-delete one of ``user_id``'s conversations. 404 when it is not theirs, 409 while it runs or for the Friend."""
    now = datetime.now(UTC)
    with engine.begin() as c:
        row = c.execute(select(conversations.c.mode, conversations.c.state).where(
            conversations.c.conversation_id == conversation_id, conversations.c.user_id == user_id,
        )).mappings().first()
        if row is None:
            raise ApplicationNotFoundError(conversation_id)
        if str(row["mode"] or "") == "friend":
            raise ApplicationConflictError(conversation_id, 0, code="conversation_protected", message=FRIEND_MESSAGE, retryable=False)
        live = c.execute(select(conversation_turns.c.turn_id).where(
            conversation_turns.c.conversation_id == conversation_id, conversation_turns.c.state.in_(LIVE_STATES),
        ).limit(1)).first()
        if live is not None or str(row["state"] or "") in LIVE_STATES:
            raise ApplicationConflictError(conversation_id, 0, code="conversation_running", message=BUSY_MESSAGE, retryable=True)

        turn_rows = c.execute(select(conversation_turns.c.turn_id, conversation_turns.c.execution_id).where(
            conversation_turns.c.conversation_id == conversation_id, conversation_turns.c.user_id == user_id,
        )).all()
        turn_ids = {str(item.turn_id) for item in turn_rows}
        execution_ids = {str(item.execution_id) for item in turn_rows}
        execution_ids.update(str(value) for value in c.execute(select(conversation_activity_events.c.execution_id).where(
            *_scoped(conversation_activity_events, conversation_id, user_id)).distinct()).scalars() if value)
        runs = c.execute(select(code_mode_runs.c.run_id, code_mode_runs.c.execution_id).where(
            *_scoped(code_mode_runs, conversation_id, user_id))).all()
        execution_ids.update(str(item.execution_id) for item in runs)

        if runs:
            c.execute(delete(code_mode_checks).where(code_mode_checks.c.run_id.in_([str(item.run_id) for item in runs])))
        if turn_ids:
            c.execute(delete(conversation_dispatches).where(conversation_dispatches.c.turn_id.in_(sorted(turn_ids))))
        if execution_ids:
            for table in _BY_EXECUTION:
                c.execute(delete(table).where(table.c.user_id == user_id, table.c.execution_id.in_(sorted(execution_ids))))
        for table in _BY_CONVERSATION:
            c.execute(delete(table).where(*_scoped(table, conversation_id, user_id)))

        # Memories are the person's facts, not the conversation's: keep them, forget where they were learned.
        c.execute(update(agent_memories).where(*_scoped(agent_memories, conversation_id, user_id)).values(conversation_id=None))

        bound = c.execute(select(schedules.c.schedule_id, schedules.c.rule).join(
            scheduled_chat_tasks, scheduled_chat_tasks.c.schedule_id == schedules.c.schedule_id,
        ).where(*_scoped(scheduled_chat_tasks, conversation_id, user_id), schedules.c.user_id == user_id)).all()
        tool_made = [str(item.schedule_id) for item in bound if str(dict(item.rule or {}).get("source") or "user") in TOOL_SCHEDULE_SOURCES]
        if tool_made:
            c.execute(update(schedules).where(
                schedules.c.schedule_id.in_(tool_made), schedules.c.user_id == user_id, schedules.c.state.in_(("ACTIVE", "PAUSED")),
            ).values(state="CANCELLED", version=schedules.c.version + 1, next_fire_at=None, updated_at=now))
        if bound:
            c.execute(update(scheduled_chat_tasks).where(*_scoped(scheduled_chat_tasks, conversation_id, user_id))
                      .values(conversation_id=None, updated_at=now))

        c.execute(delete(conversations).where(conversations.c.conversation_id == conversation_id, conversations.c.user_id == user_id))
    return {"conversation_id": conversation_id, "deleted": True, "turns": len(turn_ids), "cancelled_schedules": len(tool_made)}


__all__ = ["BUSY_MESSAGE", "FRIEND_MESSAGE", "LIVE_STATES", "delete_conversation"]

"""Turn resume and current-turn history replay helpers for PostgresChatStore."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from sqlalchemy import insert, select, update

from agentos.agentic import transcript as turn_transcript
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.reminders import system_reminder
from agentos.agentic.runtime import STREAM_RESUME_REMINDER
from agentos.api.contracts import ApplicationConflictError, ApplicationNotFoundError
from agentos.execution.journal import ExecutionEffectKind, ExecutionJournalScope
from agentos.persistence.postgres.execution_adapters import (
    ExecutionApplicationAdapter,
    ExecutionQueryAdapter,
)
from agentos.persistence.postgres.execution_journal import PostgresExecutionJournal
from agentos.persistence.postgres.schema import (
    conversation_dispatches,
    conversation_events,
    conversation_messages,
    conversation_turn_steps,
    conversation_turns,
    conversations,
    projects,
)

MAX_QUEUED_REPLAY_CHARS = 16_000


def append_resumed_turn_history(
    store,
    turn: Mapping[str, object],
    history: list[dict[str, object]],
    *,
    rehydration_budget_tokens: int = 0,
) -> None:
    """Append the current turn's tool trajectory, partial assistant text, and resume note when resumed."""
    current_turn_id = str(turn.get("turn_id") or "")
    if not current_turn_id:
        return
    current_steps = store.turn_steps(
        str(turn["conversation_id"]), turn_ids=[current_turn_id]
    ).get(current_turn_id, [])
    # A normal run already holds its own steps in memory; replay them only into a resumed or re-dispatched turn.
    resumed = any(
        step.get("kind") == turn_transcript.STEP_QUEUED_INPUT
        and isinstance(step.get("payload"), Mapping)
        and bool(step["payload"].get("resume_reason"))
        for step in current_steps
    )
    if not resumed and int(turn.get("dispatch_attempts") or 1) <= 1:
        return
    tool_steps = [
        step
        for step in current_steps
        if step.get("kind") != turn_transcript.STEP_QUEUED_INPUT
    ]
    replayed_current: list[dict[str, object]] = []
    if tool_steps:
        budget = rehydration_budget_tokens if rehydration_budget_tokens > 0 else 64_000
        kept_current = turn_transcript.within_budget(tool_steps, budget)
        replayed_current = [
            dict(msg)
            for msg in turn_transcript.project(
                kept_current, str(turn.get("provider") or "")
            )
        ]
        history.extend(replayed_current)
    assistant_msg_id = str(turn.get("assistant_message_id") or "")
    if assistant_msg_id:
        with store._engine.connect() as c:
            partial_assistant = str(
                c.execute(
                    select(conversation_messages.c.content).where(
                        conversation_messages.c.message_id == assistant_msg_id
                    )
                ).scalar()
                or ""
            )
        if replayed_current and partial_assistant:
            step_texts = [
                str(step["payload"].get("text") or "")
                for step in tool_steps
                if step.get("kind") == turn_transcript.STEP_ASSISTANT_TOOL_CALL
                and isinstance(step.get("payload"), Mapping)
                and str(step["payload"].get("text") or "")
            ]
            partial_assistant = store._strip_replayed_assistant_text(
                partial_assistant,
                replayed_current,
                step_texts=step_texts or None,
            )
        if partial_assistant.strip():
            history.append({"role": "assistant", "content": partial_assistant})
    for step in current_steps:
        if step.get("kind") == turn_transcript.STEP_QUEUED_INPUT and isinstance(
            step.get("payload"), Mapping
        ):
            note_content = str(step["payload"].get("content") or "").strip()
            if note_content:
                history.append(
                    {"role": "user", "content": note_content[:MAX_QUEUED_REPLAY_CHARS]}
                )


def recent_interrupted_paused_turns(
    store, *, maximum_age: timedelta = timedelta(minutes=30)
) -> tuple[dict[str, object], ...]:
    """Paused turns interrupted by PROVIDER_STREAM_INTERRUPTED within maximum_age."""
    cutoff = datetime.now(UTC) - maximum_age
    with store._engine.connect() as c:
        rows = (
            c.execute(
                select(
                    conversation_turns,
                    projects.c.workspace_id.label("project_workspace_id"),
                    conversation_dispatches.c.last_error.label("last_error"),
                    conversation_dispatches.c.updated_at.label("dispatch_updated_at"),
                )
                .join(
                    conversations,
                    conversations.c.conversation_id
                    == conversation_turns.c.conversation_id,
                )
                .outerjoin(projects, projects.c.project_id == conversations.c.project_id)
                .join(
                    conversation_dispatches,
                    conversation_dispatches.c.turn_id == conversation_turns.c.turn_id,
                )
                .where(
                    conversation_turns.c.state == "paused",
                    conversation_dispatches.c.state == "paused",
                    conversation_dispatches.c.last_error
                    == "PROVIDER_STREAM_INTERRUPTED",
                    conversation_dispatches.c.updated_at >= cutoff,
                )
            )
            .mappings()
            .all()
        )
    return tuple(dict(row) for row in rows)


def resume_paused_turn(
    store,
    conversation_id: str,
    turn_id: str,
    user_id: str,
    *,
    reason: str = "user_resumed",
) -> dict[str, object]:
    """Acknowledge unknown effects on a paused turn and re-queue it to continue."""
    now = datetime.now(UTC)
    with store._engine.connect() as c:
        store._owned(c, conversation_id, user_id)
        turn_row = (
            c.execute(
                select(
                    conversation_turns,
                    projects.c.workspace_id.label("project_workspace_id"),
                )
                .join(
                    conversations,
                    conversations.c.conversation_id
                    == conversation_turns.c.conversation_id,
                )
                .outerjoin(projects, projects.c.project_id == conversations.c.project_id)
                .where(
                    conversation_turns.c.conversation_id == conversation_id,
                    conversation_turns.c.turn_id == turn_id,
                    conversation_turns.c.user_id == user_id,
                )
            )
            .mappings()
            .first()
        )
        if turn_row is None:
            raise ApplicationNotFoundError(turn_id)
        turn = dict(turn_row)
        current_state = str(turn.get("state") or "")
        if current_state in {"queued", "starting", "running"}:
            return {
                "resumed": True,
                "conversation_id": conversation_id,
                "turn_id": turn_id,
                "state": current_state,
                "acknowledged_effects": [],
            }
        if current_state != "paused":
            raise ApplicationConflictError(
                "only a paused turn can be resumed",
                code="TURN_NOT_PAUSED",
                details={"turn_id": turn_id, "state": current_state},
            )
        step_rows = (
            c.execute(
                select(
                    conversation_turn_steps.c.tool_call_id,
                    conversation_turn_steps.c.tool_name,
                    conversation_turn_steps.c.payload,
                ).where(
                    conversation_turn_steps.c.conversation_id == conversation_id,
                    conversation_turn_steps.c.turn_id == turn_id,
                    conversation_turn_steps.c.tool_call_id.is_not(None),
                )
            )
            .mappings()
            .all()
        )
    tool_by_inv = {
        str(r["tool_call_id"]): (str(r["tool_name"] or ""), str(r["payload"] or ""))
        for r in step_rows
    }
    scope = ExecutionJournalScope(
        execution_id=str(turn["execution_id"]),
        user_id=str(turn["user_id"]),
        workspace_id=(
            str(turn["project_workspace_id"])
            if turn.get("project_workspace_id")
            else None
        ),
        agent_id=str(store.main_agent_id(turn)),
        correlation_id=f"corr_{turn['execution_id']}",
    )
    journal = PostgresExecutionJournal(store._engine)
    reconciliation_ref = f"{reason}:{turn_id}:{int(now.timestamp())}"
    acknowledged = journal.acknowledge_unresolved(
        scope, reconciliation_ref=reconciliation_ref, now=now
    )
    unknown_tools: list[str] = []
    for eff in acknowledged:
        if eff.kind is ExecutionEffectKind.TOOL:
            inv_ref = str(eff.invocation_ref)
            req_ref = str(eff.request_ref)
            inv_id = (
                inv_ref.split("tool:", 1)[-1]
                if inv_ref.startswith("tool:")
                else inv_ref
            )
            req_name = (
                req_ref.split("tool:", 1)[-1]
                if req_ref.startswith("tool:")
                else req_ref
            )
            if inv_id in tool_by_inv:
                t_name, t_args = tool_by_inv[inv_id]
                unknown_tools.append(f"{t_name or req_name} ({inv_ref}) {t_args[:200]}")
            else:
                unknown_tools.append(f"{req_name} ({inv_ref})")
    query = ExecutionQueryAdapter(store._engine)
    control = ExecutionApplicationAdapter(store._engine)
    execution_id = str(turn["execution_id"])
    try:
        view = query.get(
            {
                "resource_id": execution_id,
                "user_id": user_id,
                "purpose": "execution.read",
            }
        )
        if view["state"] == "PAUSED":
            control.control(
                {
                    "context": {"user_id": user_id, "execution_id": execution_id},
                    "action": "RESUME",
                    "expected_state_version": view["state_version"],
                    "reason": reason,
                    "idempotency_key": f"turn-resume:{turn_id}:{view['state_version']}",
                    "requested_at": now,
                }
            )
    except LookupError:
        pass
    if unknown_tools:
        listed = ", ".join(unknown_tools)
        note_text = system_reminder(
            "A sessão anterior foi interrompida enquanto uma ou mais chamadas de ferramenta ficaram em "
            f"estado desconhecido (UNKNOWN_ACKED) e foram reconhecidas pelo usuário: {listed}. Antes de "
            "repetir qualquer uma dessas ações, verifique no workspace/estado atual se o efeito já ocorreu "
            "e continue a tarefa do ponto em que parou."
        )
    else:
        note_text = system_reminder(STREAM_RESUME_REMINDER)
    store.record_step(
        turn,
        kind=turn_transcript.STEP_QUEUED_INPUT,
        payload={
            "content": note_text,
            "resume_reason": reason,
            "unknown_tools": unknown_tools,
        },
    )
    with store._engine.begin() as c:
        c.execute(
            update(conversation_dispatches)
            .where(conversation_dispatches.c.turn_id == turn_id)
            .values(
                state="pending",
                last_error=reason,
                queued_at=now,
                acquired_at=None,
                updated_at=now,
            )
        )
        c.execute(
            update(conversation_turns)
            .where(conversation_turns.c.turn_id == turn_id)
            .values(state="queued", updated_at=now)
        )
        c.execute(
            update(conversation_messages)
            .where(conversation_messages.c.message_id == turn["assistant_message_id"])
            .values(status="queued", updated_at=now)
        )
        c.execute(
            update(conversations)
            .where(conversations.c.conversation_id == conversation_id)
            .values(state="queued", updated_at=now)
        )
        c.execute(
            insert(conversation_events).values(
                conversation_id=conversation_id,
                user_id=user_id,
                event_type="turn.resumed",
                message_id=turn["assistant_message_id"],
                payload={
                    "state": "queued",
                    "turn_id": turn_id,
                    "reason": reason,
                    "acknowledged_effects": [eff.invocation_ref for eff in acknowledged],
                },
                created_at=now,
            )
        )
    store._activity(
        turn,
        AgentActivityEventType.TURN_STARTED,
        "Retomando execução",
        {
            "state": "queued",
            "resumed": True,
            "reason": reason,
            "acknowledged_effects": [eff.invocation_ref for eff in acknowledged],
        },
    )
    return {
        "resumed": True,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "state": "queued",
        "acknowledged_effects": [eff.invocation_ref for eff in acknowledged],
    }

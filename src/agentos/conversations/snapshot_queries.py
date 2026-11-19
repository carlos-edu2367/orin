"""Optimized conversation snapshot, header, message window, and activity compaction queries."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Connection, Engine

from agentos.agentic.events import AgentActivityEventType
from agentos.api.contracts import ApplicationNotFoundError
from agentos.persistence.postgres.schema import (
    conversation_activity_events,
    conversation_message_attachments,
    conversation_message_commands,
    conversation_messages,
    conversation_turns,
    conversations,
    projects,
)

_CONTEXT_USAGE_COMPONENTS = (
    "history_tokens",
    "input_tokens",
    "tools_tokens",
    "skills_tokens",
    "mcps_tokens",
)

_SETTLED_TURN_STATES = ("completed", "failed", "cancelled")

_SETTLED_SKIP_EVENTS = (
    "tool.requested",
    "tool.progressed",
    "code_mode.validation_started",
    "code_mode.validation_finished",
    "code_mode.stage_changed",
    "speak.block_started",
    "speak.delta",
    "speak.chunk_ready",
    "speak.filler_triggered",
    "speak.interrupted",
    "speak.finished",
)

_SETTLED_SKIP_EVENTS_SET = frozenset(_SETTLED_SKIP_EVENTS)

_NON_BREAKING_DELTA_EVENTS = frozenset({
    "context.updated",
    "context.compacted",
    "code_mode.validation_started",
    "code_mode.validation_finished",
    "code_mode.stage_changed",
    "speak.block_started",
    "speak.delta",
    "speak.chunk_ready",
    "speak.filler_triggered",
    "speak.interrupted",
    "speak.finished",
})

_TOOL_COMPLETION_EVENTS = frozenset({
    "tool.finished",
    "tool.completed",
    "tool.failed",
})

_TOOL_ENRICH_KEYS = (
    "path",
    "file_path",
    "target_path",
    "command",
    "description",
    "tool_name",
    "tool_kind",
    "invocation_id",
    "call_id",
)


def plain_payload(value: object) -> object:
    """Convert frozen activity payload containers into mutable JSON-ready Python containers."""
    if isinstance(value, Mapping):
        return {str(key): plain_payload(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain_payload(item) for item in value]
    return value


def repair_context_usage(payload: Mapping[str, object]) -> dict[str, object]:
    """Recover system_prompt_tokens from events written before telemetry redaction was fixed."""
    repaired = {str(key): value for key, value in payload.items()}
    system_prompt_tokens = repaired.get("system_prompt_tokens")
    if isinstance(system_prompt_tokens, (int, float)) and not isinstance(system_prompt_tokens, bool):
        return repaired

    used_tokens = repaired.get("used_tokens")
    components = [repaired.get(key) for key in _CONTEXT_USAGE_COMPONENTS]
    if not isinstance(used_tokens, (int, float)) or isinstance(used_tokens, bool):
        return repaired
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in components):
        return repaired
    inferred = int(used_tokens) - sum(int(value) for value in components)
    memory_tokens = repaired.get("memory_tokens")
    if isinstance(memory_tokens, (int, float)) and not isinstance(memory_tokens, bool):
        inferred -= int(memory_tokens)
    if inferred >= 0:
        repaired["system_prompt_tokens"] = inferred
    return repaired


def _format_occurred_at(created_at: object) -> str:
    if isinstance(created_at, datetime):
        return (created_at.replace(tzinfo=UTC) if created_at.tzinfo is None else created_at).isoformat()
    return str(created_at)


def _prepare_activity_batch(desc_batch: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Convert a descending SQL batch into ascending pre-normalized items, collapsing adjacent pure deltas."""
    prepared: list[dict[str, object]] = []
    assistant_delta_type = AgentActivityEventType.ASSISTANT_DELTA.value
    thought_delta_type = AgentActivityEventType.AGENT_THOUGHT_DELTA.value

    for row in reversed(desc_batch):
        ev_type = str(row["event_type"])
        t_id = str(row["turn_id"])
        agent_id = str(row["agent_id"])
        r_id = int(row["id"])
        seq = int(row["sequence"])
        raw_payload = row["payload"]
        payload = dict(plain_payload(raw_payload)) if isinstance(raw_payload, Mapping) else {}
        occurred_at = _format_occurred_at(row["created_at"])

        if prepared and ev_type in (assistant_delta_type, thought_delta_type):
            prev = prepared[-1]
            if (
                prev["event_type"] == ev_type
                and prev["turn_id"] == t_id
                and prev["agent_id"] == agent_id
            ):
                chunk = str(payload.get("content") or payload.get("delta") or "")
                chunks = prev.get("_chunks")
                if isinstance(chunks, list):
                    chunks.append(chunk)
                else:
                    prev_payload = prev["payload"]
                    base = str(prev_payload.get("content") or prev_payload.get("delta") or "") if isinstance(prev_payload, dict) else ""
                    prev["_chunks"] = [base, chunk]
                prev["sequence"] = seq
                prev["_cursor_id"] = r_id
                prev["occurred_at"] = occurred_at
                continue

        prepared.append({
            "id": r_id,
            "_cursor_id": r_id,
            "event_id": str(row["event_id"]),
            "event_type": ev_type,
            "sequence": seq,
            "summary": str(row["summary"]),
            "payload": payload,
            "created_at": occurred_at,
            "occurred_at": occurred_at,
            "turn_id": t_id,
            "execution_id": str(row["execution_id"]),
            "agent_id": agent_id,
            "parent_agent_id": row["parent_agent_id"],
        })

    for item in prepared:
        chunks = item.pop("_chunks", None)
        if isinstance(chunks, list):
            merged = "".join(chunks)
            p = item["payload"]
            if isinstance(p, dict):
                p["content"] = merged
                if item["event_type"] == thought_delta_type:
                    p["delta"] = merged
    return prepared


def compact_snapshot_activities(
    rows: Sequence[Mapping[str, object]],
    turns: Sequence[Mapping[str, object]],
    *,
    keep_db_id: bool = False,
    encode_cursor: Callable[[int], str] | None = None,
    defer_cursor_encoding: bool = False,
) -> list[dict[str, object]]:
    settled_turns = {
        str(t["turn_id"])
        for t in turns
        if str(t.get("state") or "") in _SETTLED_TURN_STATES
    }
    last_context_id_by_turn: dict[str, int] = {}
    context_count_by_turn: dict[str, int] = {}
    last_validation_id_by_turn: dict[str, int] = {}
    for row in rows:
        ev_type = str(row["event_type"])
        t_id = str(row["turn_id"])
        r_id = int(row["id"])
        if ev_type == AgentActivityEventType.CONTEXT_UPDATED.value:
            last_context_id_by_turn[t_id] = r_id
            context_count_by_turn[t_id] = context_count_by_turn.get(t_id, 0) + 1
        elif ev_type == AgentActivityEventType.CODE_MODE_VALIDATION_STARTED.value:
            last_validation_id_by_turn[t_id] = r_id

    compacted: list[dict[str, object]] = []
    last_assistant_delta_idx: dict[tuple[str, str], int] = {}
    last_thought_delta_idx: dict[tuple[str, str], int] = {}
    open_tool_started_by_turn: dict[tuple[str, str], list[int]] = {}
    seen_artifacts_by_turn: dict[str, set[str]] = {}
    artifact_count_by_turn: dict[str, int] = {}

    for row in rows:
        ev_type = str(row["event_type"])
        t_id = str(row["turn_id"])
        r_id = int(row["id"])
        cursor_id = int(row.get("_cursor_id", r_id))
        raw_payload = row["payload"]
        if "_cursor_id" in row and isinstance(raw_payload, dict):
            payload: dict[str, object] = dict(raw_payload)
        else:
            payload = dict(plain_payload(raw_payload)) if isinstance(raw_payload, Mapping) else {}

        if t_id in settled_turns and ev_type in _SETTLED_SKIP_EVENTS_SET:
            continue
        if ev_type == AgentActivityEventType.ARTIFACT_CREATED.value:
            raw_art_path = str(payload.get("path") or payload.get("file_path") or "").replace("\\", "/").strip()
            if raw_art_path.startswith(".orin/") or "/.orin/" in raw_art_path:
                continue
            if t_id in settled_turns:
                seen_paths = seen_artifacts_by_turn.setdefault(t_id, set())
                if raw_art_path and raw_art_path in seen_paths:
                    continue
                if artifact_count_by_turn.get(t_id, 0) >= 10:
                    continue
                if raw_art_path:
                    seen_paths.add(raw_art_path)
                artifact_count_by_turn[t_id] = artifact_count_by_turn.get(t_id, 0) + 1
        if (
            ev_type == AgentActivityEventType.CONTEXT_UPDATED.value
            and context_count_by_turn.get(t_id, 0) > 3
            and r_id != last_context_id_by_turn.get(t_id)
        ):
            continue
        if (
            ev_type in {"code_mode.validation_started", "code_mode.validation_finished"}
            and r_id < last_validation_id_by_turn.get(t_id, 0)
        ):
            continue
        if ev_type in {
            AgentActivityEventType.CONTEXT_UPDATED.value,
            AgentActivityEventType.CONTEXT_COMPACTED.value,
        } and isinstance(payload, dict):
            payload = repair_context_usage(payload)

        occurred_at = (
            str(row["occurred_at"])
            if "occurred_at" in row
            else _format_occurred_at(row["created_at"])
        )
        agent_id = str(row["agent_id"])
        stream_key = (t_id, agent_id)
        seq = int(row["sequence"])

        if ev_type == AgentActivityEventType.ASSISTANT_DELTA.value:
            last_thought_delta_idx.pop(stream_key, None)
            prev_idx = last_assistant_delta_idx.get(stream_key)
            if prev_idx is not None:
                prev_entry = compacted[prev_idx]
                prev_payload = prev_entry.get("payload")
                if isinstance(prev_payload, dict):
                    chunk_content = str(payload.get("content") or payload.get("delta") or "")
                    chunks = prev_entry.get("_delta_chunks")
                    if isinstance(chunks, list):
                        chunks.append(chunk_content)
                    else:
                        prev_content = str(prev_payload.get("content") or prev_payload.get("delta") or "")
                        prev_entry["_delta_chunks"] = [prev_content, chunk_content]
                    if prev_idx == len(compacted) - 1:
                        prev_entry["sequence"] = seq
                        prev_entry["_cursor_id"] = cursor_id
                        prev_entry["_cursor_seq"] = seq
                    prev_entry["occurred_at"] = occurred_at
                    continue

        elif ev_type == AgentActivityEventType.AGENT_THOUGHT_DELTA.value:
            last_assistant_delta_idx.pop(stream_key, None)
            prev_idx = last_thought_delta_idx.get(stream_key)
            if prev_idx is not None:
                prev_entry = compacted[prev_idx]
                prev_payload = prev_entry.get("payload")
                if isinstance(prev_payload, dict):
                    chunk_content = str(payload.get("content") or payload.get("delta") or "")
                    chunks = prev_entry.get("_delta_chunks")
                    if isinstance(chunks, list):
                        chunks.append(chunk_content)
                    else:
                        prev_content = str(prev_payload.get("content") or prev_payload.get("delta") or "")
                        prev_entry["_delta_chunks"] = [prev_content, chunk_content]
                    if prev_idx == len(compacted) - 1:
                        prev_entry["sequence"] = seq
                        prev_entry["_cursor_id"] = cursor_id
                        prev_entry["_cursor_seq"] = seq
                    prev_entry["occurred_at"] = occurred_at
                    continue

        elif ev_type not in _NON_BREAKING_DELTA_EVENTS:
            last_assistant_delta_idx.pop(stream_key, None)
            last_thought_delta_idx.pop(stream_key, None)

        absorbed_started_db_id: int | None = None
        if t_id in settled_turns and ev_type in _TOOL_COMPLETION_EVENTS:
            open_list = open_tool_started_by_turn.get(stream_key)
            if open_list:
                inv_id = str(payload.get("invocation_id") or payload.get("call_id") or "")
                t_name = str(payload.get("tool_name") or "")
                matched_pos = None
                if inv_id:
                    for pos, idx in enumerate(open_list):
                        sp = compacted[idx].get("payload")
                        if isinstance(sp, dict) and str(sp.get("invocation_id") or sp.get("call_id") or "") == inv_id:
                            matched_pos = pos
                            break
                if matched_pos is None and t_name:
                    for pos, idx in enumerate(open_list):
                        sp = compacted[idx].get("payload")
                        if not isinstance(sp, dict):
                            continue
                        sp_inv_id = str(sp.get("invocation_id") or sp.get("call_id") or "")
                        sp_t_name = str(sp.get("tool_name") or "")
                        if inv_id and sp_inv_id and inv_id != sp_inv_id:
                            continue
                        if sp_t_name == t_name:
                            matched_pos = pos
                            break
                if matched_pos is None and not inv_id and not t_name:
                    for pos, idx in enumerate(open_list):
                        sp = compacted[idx].get("payload")
                        sp_inv_id = str(sp.get("invocation_id") or sp.get("call_id") or "") if isinstance(sp, dict) else ""
                        sp_t_name = str(sp.get("tool_name") or "") if isinstance(sp, dict) else ""
                        if not sp_inv_id and not sp_t_name:
                            matched_pos = pos
                            break
                if matched_pos is not None:
                    matched_idx = open_list.pop(matched_pos)
                    started_entry = compacted[matched_idx]
                    started_payload = started_entry.get("payload")
                    if isinstance(started_payload, dict):
                        for k in _TOOL_ENRICH_KEYS:
                            if not payload.get(k) and started_payload.get(k):
                                payload[k] = started_payload[k]
                    started_entry["_drop_settled_started"] = True
                    raw_started_db_id = started_entry.get("_db_id")
                    if isinstance(raw_started_db_id, int):
                        absorbed_started_db_id = raw_started_db_id

        entry: dict[str, object] = {
            "event_id": str(row["event_id"]),
            "event_type": ev_type,
            "sequence": seq,
            "summary": str(row["summary"]),
            "payload": payload,
            "occurred_at": occurred_at,
            "turn_id": t_id,
            "execution_id": str(row["execution_id"]),
            "agent_id": agent_id,
            "parent_agent_id": row["parent_agent_id"],
            "_cursor_id": cursor_id,
            "_cursor_seq": seq,
        }
        if keep_db_id:
            entry["_db_id"] = r_id
            if absorbed_started_db_id is not None:
                entry["_absorbed_started_db_id"] = absorbed_started_db_id
        compacted.append(entry)
        new_idx = len(compacted) - 1
        if ev_type == AgentActivityEventType.ASSISTANT_DELTA.value:
            last_assistant_delta_idx[stream_key] = new_idx
        elif ev_type == AgentActivityEventType.AGENT_THOUGHT_DELTA.value:
            last_thought_delta_idx[stream_key] = new_idx
        elif t_id in settled_turns and ev_type == AgentActivityEventType.TOOL_STARTED.value:
            open_tool_started_by_turn.setdefault(stream_key, []).append(new_idx)

    survivors: list[dict[str, object]] = []
    for item in compacted:
        if item.pop("_drop_settled_started", False):
            continue
        delta_chunks = item.pop("_delta_chunks", None)
        if isinstance(delta_chunks, list):
            merged_text = "".join(delta_chunks)
            p = item.get("payload")
            if isinstance(p, dict):
                p["content"] = merged_text
                if item.get("event_type") == AgentActivityEventType.AGENT_THOUGHT_DELTA.value:
                    p["delta"] = merged_text
        if not defer_cursor_encoding:
            cid = int(item.pop("_cursor_id"))
            cseq = int(item.pop("_cursor_seq"))
            item["cursor"] = encode_cursor(cid) if callable(encode_cursor) else str(cseq)
        survivors.append(item)
    return survivors


def query_activity_window(
    c: Connection,
    *,
    user_id: str,
    conversation_id: str,
    turns: Sequence[Mapping[str, object]],
    limit: int = 150,
    before_id: int | None = None,
    encode_cursor: Callable[[int], str] | None = None,
    allowed_turn_ids: set[str] | None = None,
) -> tuple[list[dict[str, object]], bool, int | None]:
    has_settled_turns = any(
        str(t.get("state") or "") in _SETTLED_TURN_STATES for t in turns
    )
    stmt = select(conversation_activity_events).where(
        conversation_activity_events.c.user_id == user_id,
        conversation_activity_events.c.conversation_id == conversation_id,
    )
    if has_settled_turns:
        settled_turns_subq = select(conversation_turns.c.turn_id).where(
            conversation_turns.c.conversation_id == conversation_id,
            conversation_turns.c.state.in_(_SETTLED_TURN_STATES),
        )
        stmt = stmt.where(
            ~(
                conversation_activity_events.c.turn_id.in_(settled_turns_subq)
                & conversation_activity_events.c.event_type.in_(_SETTLED_SKIP_EVENTS)
            )
        )

    fetch_limit = max(limit * 4, 400)
    batches_reversed: list[list[dict[str, object]]] = []
    cursor_before = before_id
    reached_start = False
    stopped_at_turn_boundary = False
    compacted: list[dict[str, object]] = []

    while True:
        batch_stmt = stmt
        if cursor_before is not None:
            batch_stmt = batch_stmt.where(conversation_activity_events.c.id < cursor_before)
        desc_batch = c.execute(
            batch_stmt.order_by(conversation_activity_events.c.id.desc()).limit(fetch_limit)
        ).mappings().all()
        if not desc_batch:
            reached_start = True
            break
        if allowed_turn_ids is not None:
            filtered_desc: list[Mapping[str, object]] = []
            for idx_row, row in enumerate(desc_batch):
                row_turn = str(row.get("turn_id") or "")
                if not row_turn or row_turn in allowed_turn_ids:
                    filtered_desc.append(row)
                else:
                    # Since turns are chronological, once older rows outside allowed_turn_ids appear
                    # and none of the remaining rows in this descending batch belong to allowed_turn_ids,
                    # we have crossed past the visible message window.
                    if not any(
                        (not str(rem.get("turn_id") or "")) or str(rem.get("turn_id") or "") in allowed_turn_ids
                        for rem in desc_batch[idx_row + 1 :]
                    ):
                        stopped_at_turn_boundary = True
                        break
            if filtered_desc:
                batches_reversed.append(_prepare_activity_batch(filtered_desc))
        else:
            batches_reversed.append(_prepare_activity_batch(desc_batch))
        cursor_before = int(desc_batch[-1]["id"])
        if len(desc_batch) < fetch_limit:
            reached_start = True
        collected_rows = [
            row
            for batch in reversed(batches_reversed)
            for row in batch
        ]
        compacted = compact_snapshot_activities(
            collected_rows,
            turns,
            keep_db_id=True,
            encode_cursor=encode_cursor,
            defer_cursor_encoding=True,
        )
        if len(compacted) > limit or reached_start or stopped_at_turn_boundary:
            break
        fetch_limit = min(fetch_limit * 3, 6000)

    if not compacted:
        return [], False, None
    has_older = (len(compacted) > limit) or stopped_at_turn_boundary
    window = compacted[-limit:]
    if has_older:
        boundary_ids = [int(window[0]["_db_id"])]
        for item in window:
            absorbed = item.get("_absorbed_started_db_id")
            if isinstance(absorbed, int):
                boundary_ids.append(absorbed)
        oldest_id: int | None = min(boundary_ids)
    else:
        oldest_id = None
    for item in window:
        item.pop("_db_id", None)
        item.pop("_absorbed_started_db_id", None)
        cid = int(item.pop("_cursor_id"))
        cseq = int(item.pop("_cursor_seq"))
        item["cursor"] = encode_cursor(cid) if callable(encode_cursor) else str(cseq)
    return window, has_older, oldest_id


def query_conversation_header(
    engine: Engine,
    conversation_id: str,
    user_id: str,
) -> dict[str, object]:
    with engine.connect() as c:
        conv = c.execute(
            select(conversations).where(
                conversations.c.conversation_id == conversation_id,
                conversations.c.user_id == user_id,
            )
        ).mappings().first()
        if conv is None:
            raise ApplicationNotFoundError(conversation_id)
        project_id = conv.get("project_id")
        project_name: str | None = None
        if project_id:
            project_name = c.execute(
                select(projects.c.name).where(
                    projects.c.project_id == project_id,
                    projects.c.user_id == user_id,
                    projects.c.archived_at.is_(None),
                )
            ).scalar_one_or_none()
    raw_conv_mode = str(conv.get("mode") or "chat").strip().lower()
    conv_mode = raw_conv_mode if raw_conv_mode in {"chat", "code", "friend"} else "chat"
    conv_perm = str(conv.get("permission_mode") or ("accept_edits" if conv_mode == "code" else "ask_permissions"))
    return {
        "conversation_id": conv["conversation_id"],
        "title": conv["title"],
        "state": conv["state"],
        "mode": conv_mode,
        "permission_mode": conv_perm,
        "provider": conv["provider"],
        "model_id": conv["model_id"],
        "project_id": str(project_id) if project_id else None,
        "project_name": str(project_name) if project_name else None,
    }


def _serialize_attachments(attachment_rows: Sequence[Mapping[str, object]]) -> dict[str, list[dict[str, object]]]:
    attachments_by_message: dict[str, list[dict[str, object]]] = {}
    for row in attachment_rows:
        attachments_by_message.setdefault(str(row["message_id"]), []).append({
            "path": str(row["path"]),
            "original_name": str(row["original_name"]),
            "media_type": str(row["media_type"]),
            "kind": str(row["kind"]),
            "bytes": int(row["bytes"]),
        })
    return attachments_by_message


def _serialize_commands(command_rows: Sequence[Mapping[str, object]]) -> dict[str, dict[str, object]]:
    return {
        str(row["message_id"]): {
            "command_id": str(row["command_id"]),
            "slug": str(row["command_id"]).split(":", 1)[-1],
            "arguments": str(row["arguments"]),
        }
        for row in command_rows
    }


def _serialize_turns(turns: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "turn_id": t["turn_id"],
            "state": t["state"],
            "created_at": t["created_at"].isoformat(),
            "started_at": t["started_at"].isoformat() if t["started_at"] else None,
            "finished_at": t["finished_at"].isoformat() if t["finished_at"] else None,
            "scheduled_by_schedule_id": t["scheduled_by_schedule_id"],
            "code_mode": t.get("code_mode"),
        }
        for t in turns
    ]


def query_conversation_snapshot(
    engine: Engine,
    activity_store: Any,
    conversation_id: str,
    user_id: str,
    *,
    message_limit: int | None = None,
    activity_limit: int = 300,
    public_activity_fn: Callable[..., dict[str, object]],
) -> dict[str, object]:
    bounded_msg_limit = (
        min(max(int(message_limit), 1), 500)
        if message_limit is not None and int(message_limit) > 0
        else None
    )
    bounded_act_limit = min(max(int(activity_limit), 1), 500)
    has_older_messages = False
    oldest_message_sequence: int | None = None

    with engine.connect() as c:
        conv = c.execute(
            select(conversations).where(
                conversations.c.conversation_id == conversation_id,
                conversations.c.user_id == user_id,
            )
        ).mappings().first()
        if conv is None:
            raise ApplicationNotFoundError(conversation_id)
        project_id = conv.get("project_id")
        project_name: str | None = None
        if project_id:
            project_name = c.execute(
                select(projects.c.name).where(
                    projects.c.project_id == project_id,
                    projects.c.user_id == user_id,
                    projects.c.archived_at.is_(None),
                )
            ).scalar_one_or_none()

        if bounded_msg_limit is not None:
            desc_messages = c.execute(
                select(conversation_messages)
                .where(conversation_messages.c.conversation_id == conversation_id)
                .order_by(conversation_messages.c.sequence.desc())
                .limit(bounded_msg_limit + 1)
            ).mappings().all()
            has_older_messages = len(desc_messages) > bounded_msg_limit
            messages = list(reversed(desc_messages[:bounded_msg_limit]))
            if has_older_messages and messages:
                oldest_message_sequence = int(messages[0]["sequence"])
            window_msg_ids = [str(m["message_id"]) for m in messages]
            if window_msg_ids:
                attachment_rows = c.execute(
                    select(conversation_message_attachments)
                    .where(
                        conversation_message_attachments.c.conversation_id == conversation_id,
                        conversation_message_attachments.c.message_id.in_(window_msg_ids),
                    )
                    .order_by(conversation_message_attachments.c.id)
                ).mappings().all()
                command_rows = c.execute(
                    select(conversation_message_commands).where(
                        conversation_message_commands.c.conversation_id == conversation_id,
                        conversation_message_commands.c.message_id.in_(window_msg_ids),
                    )
                ).mappings().all()
            else:
                attachment_rows = []
                command_rows = []
        else:
            messages = c.execute(
                select(conversation_messages)
                .where(conversation_messages.c.conversation_id == conversation_id)
                .order_by(conversation_messages.c.sequence)
            ).mappings().all()
            attachment_rows = c.execute(
                select(conversation_message_attachments)
                .where(conversation_message_attachments.c.conversation_id == conversation_id)
                .order_by(conversation_message_attachments.c.id)
            ).mappings().all()
            command_rows = c.execute(
                select(conversation_message_commands).where(
                    conversation_message_commands.c.conversation_id == conversation_id
                )
            ).mappings().all()

        turns = c.execute(
            select(conversation_turns)
            .where(conversation_turns.c.conversation_id == conversation_id)
            .order_by(conversation_turns.c.created_at)
        ).mappings().all()

        turn_by_message: dict[str, str] = {}
        for t in turns:
            t_id_str = str(t["turn_id"])
            if t.get("user_message_id"):
                turn_by_message[str(t["user_message_id"])] = t_id_str
            if t.get("assistant_message_id"):
                turn_by_message[str(t["assistant_message_id"])] = t_id_str

        allowed_activity_turns: set[str] | None = None
        if bounded_msg_limit is not None and has_older_messages:
            allowed_activity_turns = {
                str(m["turn_id"]) if m.get("turn_id") else turn_by_message.get(str(m["message_id"]), "")
                for m in messages
            }
            allowed_activity_turns.discard("")
            for t in turns[-3:]:
                allowed_activity_turns.add(str(t["turn_id"]))
            for t in turns:
                if str(t.get("state") or "") not in _SETTLED_TURN_STATES:
                    allowed_activity_turns.add(str(t["turn_id"]))

        activities: list[dict[str, object]] = []
        activity_cursor = "0"
        has_older_activities = False
        oldest_activity_id: int | None = None
        context_usage: dict[str, object] | None = None

        if activity_store is not None and hasattr(activity_store, "_encode_cursor"):
            max_id = c.execute(
                select(func.max(conversation_activity_events.c.id)).where(
                    conversation_activity_events.c.user_id == user_id,
                    conversation_activity_events.c.conversation_id == conversation_id,
                )
            ).scalar()
            if max_id is not None:
                activity_cursor = activity_store._encode_cursor(
                    user_id, conversation_id, int(max_id)
                )
                encode_fn = lambda pos: activity_store._encode_cursor(
                    user_id, conversation_id, int(pos)
                )
                activities, has_older_activities, oldest_activity_id = query_activity_window(
                    c,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    turns=turns,
                    limit=bounded_act_limit,
                    before_id=None,
                    encode_cursor=encode_fn,
                    allowed_turn_ids=allowed_activity_turns,
                )
                ctx_rows = c.execute(
                    select(conversation_activity_events.c.payload)
                    .where(
                        conversation_activity_events.c.user_id == user_id,
                        conversation_activity_events.c.conversation_id == conversation_id,
                        conversation_activity_events.c.event_type.in_((
                            AgentActivityEventType.CONTEXT_UPDATED.value,
                            AgentActivityEventType.CONTEXT_COMPACTED.value,
                        )),
                    )
                    .order_by(conversation_activity_events.c.id.desc())
                    .limit(5)
                ).scalars().all()
                for raw_ctx in ctx_rows:
                    plain_ctx = plain_payload(raw_ctx)
                    if isinstance(plain_ctx, dict) and "used_tokens" in plain_ctx:
                        context_usage = repair_context_usage(plain_ctx)
                        break

    if activity_store is not None and not hasattr(activity_store, "_encode_cursor"):
        cursor: str = "0"
        while True:
            page = activity_store.replay(user_id, conversation_id, cursor=cursor, limit=500)
            if not page.events:
                break
            cursors = page.cursors or tuple(str(item.sequence) for item in page.events)
            activities.extend(
                public_activity_fn(item, item_cursor)
                for item, item_cursor in zip(page.events, cursors)
            )
            cursor = page.next_cursor or cursor
            activity_cursor = cursor
            if len(page.events) < 500:
                break
        activities = compact_snapshot_activities(
            [
                {
                    "id": int(item.get("sequence", idx + 1)),
                    "event_id": item["event_id"],
                    "event_type": item["event_type"],
                    "sequence": int(item["sequence"]),
                    "summary": item["summary"],
                    "payload": item.get("payload") or {},
                    "created_at": item["occurred_at"],
                    "turn_id": item["turn_id"],
                    "execution_id": item["execution_id"],
                    "agent_id": item["agent_id"],
                    "parent_agent_id": item.get("parent_agent_id"),
                }
                for idx, item in enumerate(activities)
            ],
            turns,
        )

    if context_usage is None:
        context_usage = next(
            (
                dict(item.get("payload") or {})
                for item in reversed(activities)
                if item.get("event_type") in {
                    AgentActivityEventType.CONTEXT_UPDATED.value,
                    AgentActivityEventType.CONTEXT_COMPACTED.value,
                }
                and isinstance(item.get("payload"), Mapping)
                and "used_tokens" in item["payload"]
            ),
            None,
        )

    attachments_by_message = _serialize_attachments(attachment_rows)
    command_by_message = _serialize_commands(command_rows)

    raw_conv_mode = str(conv.get("mode") or "chat").strip().lower()
    conv_mode = raw_conv_mode if raw_conv_mode in {"chat", "code", "friend"} else "chat"
    conv_perm = str(conv.get("permission_mode") or ("accept_edits" if conv_mode == "code" else "ask_permissions"))

    serialized_messages = [
        {
            "message_id": m["message_id"],
            "sequence": int(m["sequence"]),
            "role": m["role"],
            "content": m["content"],
            "status": m["status"],
            "retryable": bool(m["retryable"]),
            "attachments": attachments_by_message.get(str(m["message_id"]), []),
            "command": command_by_message.get(str(m["message_id"])),
            "turn_id": str(m["turn_id"]) if m.get("turn_id") else turn_by_message.get(str(m["message_id"])),
        }
        for m in messages
    ]

    turns_for_payload: Sequence[Mapping[str, object]] = turns
    if bounded_msg_limit is not None and has_older_messages and len(turns) > 80:
        keep_turn_ids = {
            str(m["turn_id"])
            for m in serialized_messages
            if m.get("turn_id")
        }
        keep_turn_ids.update(
            str(a["turn_id"])
            for a in activities
            if a.get("turn_id")
        )
        for t in turns[-60:]:
            keep_turn_ids.add(str(t["turn_id"]))
        for t in turns:
            if str(t.get("state") or "") not in _SETTLED_TURN_STATES or t.get("code_mode") == "code":
                keep_turn_ids.add(str(t["turn_id"]))
        turns_for_payload = [t for t in turns if str(t["turn_id"]) in keep_turn_ids]

    return {
        "conversation_id": conv["conversation_id"],
        "title": conv["title"],
        "state": conv["state"],
        "mode": conv_mode,
        "permission_mode": conv_perm,
        "provider": conv["provider"],
        "model_id": conv["model_id"],
        "project_id": str(project_id) if project_id else None,
        "project_name": str(project_name) if project_name else None,
        "messages": serialized_messages,
        "has_older_messages": has_older_messages,
        "oldest_message_sequence": oldest_message_sequence,
        "turns": _serialize_turns(turns_for_payload),
        "activities": activities,
        "activity_cursor": activity_cursor,
        "has_older_activities": has_older_activities,
        "oldest_activity_id": oldest_activity_id,
        "context_usage": context_usage,
    }


def query_older_messages(
    engine: Engine,
    conversation_id: str,
    user_id: str,
    *,
    before_sequence: int,
    limit: int = 50,
) -> dict[str, object]:
    bounded_limit = min(max(int(limit), 1), 200)
    with engine.connect() as c:
        conv = c.execute(
            select(conversations.c.conversation_id).where(
                conversations.c.conversation_id == conversation_id,
                conversations.c.user_id == user_id,
            )
        ).first()
        if conv is None:
            raise ApplicationNotFoundError(conversation_id)
        desc_messages = c.execute(
            select(conversation_messages)
            .where(
                conversation_messages.c.conversation_id == conversation_id,
                conversation_messages.c.sequence < int(before_sequence),
            )
            .order_by(conversation_messages.c.sequence.desc())
            .limit(bounded_limit + 1)
        ).mappings().all()
        has_older = len(desc_messages) > bounded_limit
        messages = list(reversed(desc_messages[:bounded_limit]))
        oldest_seq: int | None = int(messages[0]["sequence"]) if (has_older and messages) else None
        msg_ids = [str(m["message_id"]) for m in messages]
        direct_turn_ids = [str(m["turn_id"]) for m in messages if m.get("turn_id")]
        if msg_ids:
            attachment_rows = c.execute(
                select(conversation_message_attachments)
                .where(
                    conversation_message_attachments.c.conversation_id == conversation_id,
                    conversation_message_attachments.c.message_id.in_(msg_ids),
                )
                .order_by(conversation_message_attachments.c.id)
            ).mappings().all()
            command_rows = c.execute(
                select(conversation_message_commands).where(
                    conversation_message_commands.c.conversation_id == conversation_id,
                    conversation_message_commands.c.message_id.in_(msg_ids),
                )
            ).mappings().all()
            turn_cond = (
                conversation_turns.c.user_message_id.in_(msg_ids)
                | conversation_turns.c.assistant_message_id.in_(msg_ids)
            )
            if direct_turn_ids:
                turn_cond = turn_cond | conversation_turns.c.turn_id.in_(direct_turn_ids)
            matching_turns = c.execute(
                select(conversation_turns)
                .where(
                    conversation_turns.c.conversation_id == conversation_id,
                    turn_cond,
                )
                .order_by(conversation_turns.c.created_at)
            ).mappings().all()
        else:
            attachment_rows = []
            command_rows = []
            matching_turns = []

    attachments_by_message = _serialize_attachments(attachment_rows)
    command_by_message = _serialize_commands(command_rows)
    turn_by_message: dict[str, str] = {}
    for t in matching_turns:
        t_id_str = str(t["turn_id"])
        if t.get("user_message_id"):
            turn_by_message[str(t["user_message_id"])] = t_id_str
        if t.get("assistant_message_id"):
            turn_by_message[str(t["assistant_message_id"])] = t_id_str

    return {
        "messages": [
            {
                "message_id": m["message_id"],
                "sequence": int(m["sequence"]),
                "role": m["role"],
                "content": m["content"],
                "status": m["status"],
                "retryable": bool(m["retryable"]),
                "attachments": attachments_by_message.get(str(m["message_id"]), []),
                "command": command_by_message.get(str(m["message_id"])),
                "turn_id": str(m["turn_id"]) if m.get("turn_id") else turn_by_message.get(str(m["message_id"])),
            }
            for m in messages
        ],
        "turns": _serialize_turns(matching_turns),
        "has_older_messages": has_older,
        "oldest_message_sequence": oldest_seq,
    }


def query_older_activities(
    engine: Engine,
    activity_store: Any,
    conversation_id: str,
    user_id: str,
    *,
    before_id: int,
    limit: int = 150,
) -> dict[str, object]:
    bounded_limit = min(max(int(limit), 1), 300)
    with engine.connect() as c:
        conv = c.execute(
            select(conversations.c.conversation_id).where(
                conversations.c.conversation_id == conversation_id,
                conversations.c.user_id == user_id,
            )
        ).first()
        if conv is None:
            raise ApplicationNotFoundError(conversation_id)
        turns = c.execute(
            select(conversation_turns)
            .where(conversation_turns.c.conversation_id == conversation_id)
            .order_by(conversation_turns.c.created_at)
        ).mappings().all()
        encode_fn = (
            (lambda pos: activity_store._encode_cursor(user_id, conversation_id, int(pos)))
            if activity_store is not None and hasattr(activity_store, "_encode_cursor")
            else None
        )
        activities, has_older, oldest_id = query_activity_window(
            c,
            user_id=user_id,
            conversation_id=conversation_id,
            turns=turns,
            limit=bounded_limit,
            before_id=int(before_id),
            encode_cursor=encode_fn,
        )
    return {
        "activities": activities,
        "has_older_activities": has_older,
        "oldest_activity_id": oldest_id,
    }

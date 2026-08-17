"""Durable public conversation application service and worker-facing store."""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from agentos.api.contracts import ApplicationNotFoundError
from agentos.api.events import CursorError
from agentos.agentic.events import AgentActivityEvent, AgentActivityEventType
from agentos.persistence.postgres.agentic_activity import ActivityCursorError
from agentos.persistence.postgres.schema import (conversation_activity_events, conversation_agent_usage, conversation_agents, conversation_dispatches, conversation_events, conversation_hook_context, conversation_message_attachments, conversation_message_commands, conversation_messages, conversation_tool_records, conversation_turns, conversations, projects, runtime_heartbeats, workspace_roots)


_LOGGER = logging.getLogger("agentos.conversations.chat")


def _id(prefix: str) -> str: return f"{prefix}_{uuid4().hex}"
def _title(message: str) -> str: return " ".join(message.split())[:80] or "Nova conversa"


def _attachment_label(record: Mapping[str, object]) -> str:
    kinds = {"image": "imagem", "pdf": "PDF", "office": "documento", "text": "texto"}
    kind = kinds.get(str(record.get("kind") or ""), "arquivo")
    size = int(record.get("bytes") or 0)
    return f"{record.get('path')} ({kind}, {max(1, round(size / 1024))} KB)"


def _attachment_marker(records: Sequence[Mapping[str, object]]) -> str:
    """The line appended to a user message so the model knows the files exist."""
    listed = ", ".join(_attachment_label(record) for record in records)
    return (
        f"\n\n[anexos enviados pela pessoa: {listed}]\n"
        "Use view_file(path=\"…\") para ler o conteúdo de um anexo visual, "
        "ou read_file para texto."
    )


def _chunks(text: str, size: int) -> list[str]:
    """Split a delta so each chunk fits the bounded activity payload limit."""
    return [text[index:index + size] for index in range(0, len(text), size)] or [text]


def _plain(value: object) -> object:
    """Convert the frozen activity payload into JSON-serializable containers."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


_CONTEXT_USAGE_COMPONENTS = (
    "history_tokens",
    "input_tokens",
    "tools_tokens",
    "skills_tokens",
    "mcps_tokens",
)


def _repair_context_usage(payload: Mapping[str, object]) -> dict[str, object]:
    """Recover the prompt count from events written before telemetry redaction was fixed."""
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
    if inferred >= 0:
        repaired["system_prompt_tokens"] = inferred
    return repaired


MAX_EXPANDED_COMMAND_BODY = 200_000


def _expand_command(library, user_id: str, message: str) -> tuple[object, str, str] | None:
    """Resolve a leading ``/token`` to a command, or return None.

    Deliberately conservative: only a first token of the exact shape ``/slug``
    or ``/plugin-id:slug`` is considered, so an ordinary message that happens
    to start with a path or a regex is never hijacked.
    """
    if library is None or not message.startswith("/"):
        return None
    token, _, remainder = message.partition(" ")
    name = token[1:]
    if not name or "/" in name:
        return None
    resolved = library.resolve(user_id, name)
    if resolved is None:
        return None
    try:
        body = resolved.path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    arguments = remainder.strip()
    if "$ARGUMENTS" in body:
        body = body.replace("$ARGUMENTS", arguments)
    elif arguments:
        body = f"{body.rstrip()}\n\nArgumentos: {arguments}"
    return resolved, arguments, body.strip()[:MAX_EXPANDED_COMMAND_BODY]


def _unavailable_token_usage() -> dict[str, object]:
    return {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "usage_reported": False,
    }


def _token_usage(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
        "total_tokens": row["total_tokens"],
        "usage_reported": bool(row["usage_reported"]),
    }


def _total_token_usage(usages) -> dict[str, object]:
    items = list(usages)
    if not any(bool(item["usage_reported"]) for item in items):
        return _unavailable_token_usage()

    def total(field: str) -> int | None:
        values = [int(item[field]) for item in items if item[field] is not None]
        return sum(values) if values else None

    return {
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "total_tokens": total("total_tokens"),
        "usage_reported": True,
    }


@dataclass(frozen=True, slots=True)
class ChatReceipt:
    conversation_id: str
    title: str
    turn_id: str
    message_id: str
    state: str


class PostgresChatStore:
    def __init__(self, engine: Engine, activity_store=None, command_library=None) -> None:
        self._engine = engine
        self.activity_store = activity_store
        self._command_library = command_library

    def _activity(
        self,
        turn: dict[str, object] | None,
        event_type: AgentActivityEventType,
        summary: str,
        payload: dict[str, object] | None = None,
        *,
        agent_id: str | None = None,
        parent_agent_id: str | None = None,
    ) -> None:
        if self.activity_store is None or turn is None:
            return
        # ``sequence`` is unique per turn, so it is derived from the activity log
        # itself rather than from another table's identity. Concurrent writers
        # (the runtime emits several events back to back) lose the race on the
        # unique constraint and simply take the next free slot.
        for _ in range(6):
            sequence = self._next_activity_sequence(str(turn["turn_id"]))
            try:
                event = AgentActivityEvent(
                    event_id=f"activity:{turn['turn_id']}:{sequence}",
                    conversation_id=str(turn["conversation_id"]), turn_id=str(turn["turn_id"]),
                    execution_id=str(turn["execution_id"]), user_id=str(turn["user_id"]),
                    agent_id=agent_id or self.main_agent_id(turn), parent_agent_id=parent_agent_id,
                    event_type=event_type, sequence=sequence,
                    summary=summary[:512] or "Atividade", payload=payload or {}, created_at=datetime.now(UTC),
                )
            except ValueError:
                # The event's own content (payload/summary) failed validation —
                # retrying with the same content fails identically every time.
                # This is a different failure than the sequence race below, and
                # must not burn the retry budget meant for that race.
                return
            try:
                self.activity_store.record_event(event)
                return
            except ValueError:
                continue
            except Exception:
                # Activity is an audit projection; a projection failure must not
                # roll back the authoritative conversation transaction.
                return

    def _next_activity_sequence(self, turn_id: str) -> int:
        with self._engine.connect() as connection:
            current = connection.execute(
                select(func.max(conversation_activity_events.c.sequence)).where(conversation_activity_events.c.turn_id == turn_id)
            ).scalar()
        return int(current or 0) + 1

    def record_tool_call(self, turn: Mapping[str, object], *, tool_name: str, arguments: Mapping[str, object], status: str, summary: str) -> None:
        """Append one line to the conversation's durable tool ledger.

        This is a projection, exactly like the activity log: it must never be
        able to roll back or fail the turn it is describing.
        """
        try:
            rendered = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)[:1000]
        except Exception:
            try:
                rendered = str(arguments)[:1000]
            except Exception:
                rendered = "<unserializable>"
        now = datetime.now(UTC)
        for _ in range(6):
            try:
                with self._engine.connect() as connection:
                    current = connection.execute(
                        select(func.max(conversation_tool_records.c.sequence)).where(conversation_tool_records.c.conversation_id == turn["conversation_id"])
                    ).scalar()
                sequence = int(current or 0) + 1
                with self._engine.begin() as connection:
                    connection.execute(insert(conversation_tool_records).values(
                        record_id=f"tool:{turn['conversation_id']}:{sequence}",
                        conversation_id=str(turn["conversation_id"]), turn_id=str(turn["turn_id"]),
                        user_id=str(turn["user_id"]), sequence=sequence, tool_name=str(tool_name)[:64],
                        arguments=rendered, status=str(status)[:16], summary=str(summary)[:512] or str(tool_name)[:512],
                        created_at=now,
                    ))
                return
            except IntegrityError:
                continue
            except Exception:
                return

    def tool_ledger(self, turn: Mapping[str, object], *, limit: int = 20) -> list[dict[str, str]]:
        """The most recent tool steps of this conversation, oldest first."""
        bounded = max(1, min(int(limit), 50))
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(
                    conversation_tool_records.c.tool_name, conversation_tool_records.c.arguments,
                    conversation_tool_records.c.status, conversation_tool_records.c.summary,
                )
                .where(conversation_tool_records.c.conversation_id == turn["conversation_id"])
                .order_by(conversation_tool_records.c.sequence.desc())
                .limit(bounded)
            ).mappings().all()
        return [
            {"tool_name": str(row["tool_name"]), "arguments": str(row["arguments"]), "status": str(row["status"]), "summary": str(row["summary"])}
            for row in reversed(rows)
        ]

    @staticmethod
    def main_agent_id(turn: Mapping[str, object]) -> str:
        # Stable across turns so the conversation-level graph keeps one root node.
        return f"agent:{turn['conversation_id']}:main"

    def create(self, *, user_id: str, message: str, provider: str, model_id: str, idempotency_key: str, conversation_id: str | None = None, project_id: str | None = None, attachments: Sequence[Mapping[str, object]] = (), new_conversation_id: str | None = None, scheduled_by_schedule_id: str | None = None) -> ChatReceipt:
        message = message.strip()
        attachments = list(attachments)
        expansion = _expand_command(self._command_library, user_id, message)
        if len(message) > 16000: raise ValueError("message must be a bounded non-blank string")
        if not message and not attachments: raise ValueError("message must be a bounded non-blank string")
        title_source = message or str(attachments[0].get("original_name") or "Arquivo enviado")
        now = datetime.now(UTC)
        with self._engine.begin() as c:
            previous = c.execute(select(conversation_turns).where(conversation_turns.c.user_id == user_id, conversation_turns.c.idempotency_key == idempotency_key)).mappings().first()
            if previous:
                row = c.execute(select(conversations).where(conversations.c.conversation_id == previous["conversation_id"])).mappings().one()
                return ChatReceipt(previous["conversation_id"], row["title"], previous["turn_id"], previous["assistant_message_id"], previous["state"])
            if conversation_id is None:
                conversation_id = new_conversation_id or _id("chat")
                if project_id is not None:
                    from agentos.persistence.postgres.schema import projects
                    project = c.execute(select(projects.c.project_id).where(projects.c.project_id == project_id, projects.c.user_id == user_id, projects.c.archived_at.is_(None))).scalar()
                    if project is None: raise ApplicationNotFoundError(project_id)
                c.execute(insert(conversations).values(conversation_id=conversation_id, user_id=user_id, title=_title(title_source), provider=provider, model_id=model_id, project_id=project_id, state="queued", created_at=now, updated_at=now))
                sequence = 1
            else:
                row = c.execute(select(conversations).where(conversations.c.conversation_id == conversation_id, conversations.c.user_id == user_id)).mappings().first()
                if row is None: raise ApplicationNotFoundError(conversation_id)
                requested_provider, requested_model_id = provider.strip(), model_id.strip()
                if bool(requested_provider) != bool(requested_model_id):
                    raise ValueError("provider and model_id must be provided together")
                if requested_provider:
                    provider, model_id = requested_provider, requested_model_id
                    c.execute(update(conversations).where(
                        conversations.c.conversation_id == conversation_id,
                        conversations.c.user_id == user_id,
                    ).values(provider=provider, model_id=model_id))
                else:
                    provider, model_id = row["provider"], row["model_id"]
                sequence = int(c.execute(select(func.max(conversation_messages.c.sequence)).where(conversation_messages.c.conversation_id == conversation_id)).scalar() or 0) + 1
                # A normal follow-up message is also a valid answer to the
                # last structured prompt. Close it before queuing the next
                # turn so reloading the chat never presents stale forms again.
                waiting = c.execute(select(conversation_turns.c.assistant_message_id).where(
                    conversation_turns.c.conversation_id == conversation_id,
                    conversation_turns.c.user_id == user_id,
                    conversation_turns.c.state == "waiting_user",
                )).scalars().all()
                if waiting:
                    c.execute(update(conversation_turns).where(
                        conversation_turns.c.conversation_id == conversation_id,
                        conversation_turns.c.user_id == user_id,
                        conversation_turns.c.state == "waiting_user",
                    ).values(state="completed", finished_at=now, updated_at=now))
                    c.execute(update(conversation_messages).where(
                        conversation_messages.c.message_id.in_(waiting)
                    ).values(status="completed", updated_at=now))
            turn_id, user_message_id, assistant_message_id = _id("turn"), _id("msg"), _id("msg")
            execution_id = self.execution_id_for(turn_id)
            c.execute(insert(conversation_messages), [
                {"message_id": user_message_id, "conversation_id": conversation_id, "turn_id": turn_id, "user_id": user_id, "role": "user", "content": message, "sequence": sequence, "status": "completed", "retryable": False, "created_at": now, "updated_at": now},
                {"message_id": assistant_message_id, "conversation_id": conversation_id, "turn_id": turn_id, "user_id": user_id, "role": "assistant", "content": "", "sequence": sequence + 1, "status": "queued", "retryable": False, "created_at": now, "updated_at": now},
            ])
            if expansion is not None:
                resolved, arguments, expanded_body = expansion
                c.execute(insert(conversation_message_commands).values(
                    message_id=user_message_id, conversation_id=conversation_id, user_id=user_id,
                    plugin_id=resolved.plugin_id, command_id=resolved.command_id,
                    arguments=arguments, expanded_body=expanded_body, created_at=now,
                ))
            if attachments:
                c.execute(insert(conversation_message_attachments), [{
                    "attachment_id": _id("att"), "message_id": user_message_id,
                    "conversation_id": conversation_id, "user_id": user_id,
                    "path": str(item["path"]), "original_name": str(item["original_name"]),
                    "media_type": str(item["media_type"]), "kind": str(item["kind"]),
                    "bytes": int(item["bytes"]), "created_at": now,
                } for item in attachments])
            c.execute(insert(conversation_turns).values(turn_id=turn_id, conversation_id=conversation_id, user_id=user_id, execution_id=execution_id, user_message_id=user_message_id, assistant_message_id=assistant_message_id, provider=provider, model_id=model_id, state="queued", idempotency_key=idempotency_key, scheduled_by_schedule_id=scheduled_by_schedule_id, created_at=now, updated_at=now))
            c.execute(insert(conversation_dispatches).values(turn_id=turn_id, state="pending", attempts=0, queued_at=now, updated_at=now))
            c.execute(insert(conversation_events).values(conversation_id=conversation_id, user_id=user_id, event_type="turn.queued", message_id=assistant_message_id, payload={"state": "queued"}, created_at=now))
            c.execute(update(conversations).where(conversations.c.conversation_id == conversation_id).values(state="queued", updated_at=now))
        receipt = ChatReceipt(conversation_id, _title(title_source), turn_id, assistant_message_id, "queued")
        self._activity({"conversation_id": conversation_id, "turn_id": turn_id, "execution_id": execution_id, "user_id": user_id}, AgentActivityEventType.TURN_STARTED, "Execução agendada na fila" if scheduled_by_schedule_id else "Turn queued", {"scheduled_by_schedule_id": scheduled_by_schedule_id} if scheduled_by_schedule_id else None)
        return receipt

    def list(self, user_id: str) -> dict[str, object]:
        with self._engine.connect() as c: rows = c.execute(select(conversations).where(conversations.c.user_id == user_id, conversations.c.project_id.is_(None)).order_by(conversations.c.updated_at.desc())).mappings().all()
        return {"items": [{"conversation_id": r["conversation_id"], "title": r["title"], "state": r["state"], "updated_at": r["updated_at"].isoformat()} for r in rows]}

    def get(self, conversation_id: str, user_id: str) -> dict[str, object]:
        with self._engine.connect() as c:
            conv = c.execute(select(conversations).where(conversations.c.conversation_id == conversation_id, conversations.c.user_id == user_id)).mappings().first()
            if conv is None: raise ApplicationNotFoundError(conversation_id)
            messages = c.execute(select(conversation_messages).where(conversation_messages.c.conversation_id == conversation_id).order_by(conversation_messages.c.sequence)).mappings().all()
            turns = c.execute(select(conversation_turns).where(conversation_turns.c.conversation_id == conversation_id).order_by(conversation_turns.c.created_at)).mappings().all()
            attachment_rows = c.execute(select(conversation_message_attachments).where(conversation_message_attachments.c.conversation_id == conversation_id).order_by(conversation_message_attachments.c.id)).mappings().all()
            command_rows = c.execute(select(conversation_message_commands).where(conversation_message_commands.c.conversation_id == conversation_id)).mappings().all()
        attachments_by_message: dict[str, list[dict[str, object]]] = {}
        for row in attachment_rows:
            attachments_by_message.setdefault(str(row["message_id"]), []).append({
                "path": str(row["path"]), "original_name": str(row["original_name"]),
                "media_type": str(row["media_type"]), "kind": str(row["kind"]), "bytes": int(row["bytes"]),
            })
        command_by_message: dict[str, dict[str, object]] = {
            str(row["message_id"]): {
                "command_id": str(row["command_id"]), "slug": str(row["command_id"]).split(":", 1)[-1],
                "arguments": str(row["arguments"]),
            }
            for row in command_rows
        }
        activities: list[dict[str, object]] = []
        activity_cursor = "0"
        if self.activity_store is not None:
            # A snapshot replays the whole conversation, including assistant
            # deltas. Their position between tool and agent events is required
            # to reconstruct the same interleaved timeline as the live stream.
            cursor: str = "0"
            while True:
                page = self.activity_store.replay(user_id, conversation_id, cursor=cursor, limit=500)
                if not page.events:
                    break
                cursors = page.cursors or tuple(str(item.sequence) for item in page.events)
                activities.extend(
                    self._public_activity(item, item_cursor)
                    for item, item_cursor in zip(page.events, cursors)
                )
                cursor = page.next_cursor or cursor
                activity_cursor = cursor
                if len(page.events) < 500:
                    break
        context_usage = next(
            (dict(item.get("payload") or {}) for item in reversed(activities) if item.get("event_type") in {AgentActivityEventType.CONTEXT_UPDATED.value, AgentActivityEventType.CONTEXT_COMPACTED.value} and isinstance(item.get("payload"), Mapping) and "used_tokens" in item["payload"]),
            None,
        )
        return {"conversation_id": conv["conversation_id"], "title": conv["title"], "state": conv["state"], "provider": conv["provider"], "model_id": conv["model_id"], "project_id": conv["project_id"], "messages": [{"message_id": m["message_id"], "role": m["role"], "content": m["content"], "status": m["status"], "retryable": bool(m["retryable"]), "attachments": attachments_by_message.get(str(m["message_id"]), []), "command": command_by_message.get(str(m["message_id"]))} for m in messages], "turns": [{"turn_id": t["turn_id"], "state": t["state"], "created_at": t["created_at"].isoformat(), "started_at": t["started_at"].isoformat() if t["started_at"] else None, "finished_at": t["finished_at"].isoformat() if t["finished_at"] else None, "scheduled_by_schedule_id": t["scheduled_by_schedule_id"]} for t in turns], "activities": activities, "activity_cursor": activity_cursor, "context_usage": context_usage}

    @staticmethod
    def _public_activity(event: AgentActivityEvent, cursor: str | None = None) -> dict[str, object]:
        # The whole payload is already bounded and secret-redacted by
        # AgentActivityEvent, so it can be published as-is rather than through a
        # hand-maintained key allowlist that silently drops new event fields.
        payload = _plain(event.payload)
        if event.event_type in {AgentActivityEventType.CONTEXT_UPDATED, AgentActivityEventType.CONTEXT_COMPACTED} and isinstance(payload, Mapping):
            payload = _repair_context_usage(payload)
        return {
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "sequence": event.sequence,
            "summary": event.summary,
            "payload": payload,
            "occurred_at": event.created_at.isoformat(),
            "turn_id": event.turn_id,
            "execution_id": event.execution_id,
            "agent_id": event.agent_id,
            "parent_agent_id": event.parent_agent_id,
            "cursor": cursor or str(event.sequence),
        }

    def events(self, conversation_id: str, user_id: str, after: int | str) -> tuple[list[dict[str, object]], int | str]:
        if self.activity_store is not None:
            try:
                page = self.activity_store.replay(user_id, conversation_id, cursor=str(after), limit=200)
            except ActivityCursorError as error:
                # Translate the storage-level cursor failure into the public
                # resync signal the HTTP boundary and the client already share.
                raise CursorError("cursor_invalid") from error
            cursors = page.cursors or tuple(str(item.sequence) for item in page.events)
            events = [self._public_activity(item, item_cursor) for item, item_cursor in zip(page.events, cursors)]
            return events, page.next_cursor or str(after)
        with self._engine.connect() as c:
            rows = c.execute(select(conversation_events).where(conversation_events.c.conversation_id == conversation_id, conversation_events.c.user_id == user_id, conversation_events.c.id > after).order_by(conversation_events.c.id).limit(100)).mappings().all()
        safe_events = []
        for row in rows:
            event_type = str(row["event_type"])
            payload = dict(row["payload"] or {})
            if event_type == "message.delta":
                payload = {"summary": "Assistant updated the response"}
            else:
                payload = {key: value for key, value in payload.items() if key in {"state", "retryable", "summary", "code"}}
            safe_events.append({"id": row["id"], "event_type": event_type, "message_id": row["message_id"], "payload": payload})
        return (safe_events, int(rows[-1]["id"]) if rows else after)

    def pending(self) -> tuple[str, ...]:
        with self._engine.connect() as c: return tuple(c.execute(select(conversation_dispatches.c.turn_id).where(conversation_dispatches.c.state == "pending")).scalars())

    def mark_enqueued(self, turn_id: str) -> bool:
        now = datetime.now(UTC)
        with self._engine.begin() as c:
            return bool(c.execute(update(conversation_dispatches).where(conversation_dispatches.c.turn_id == turn_id, conversation_dispatches.c.state == "pending").values(state="enqueued", updated_at=now)).rowcount)

    def claim(self, turn_id: str) -> dict[str, object] | None:
        now = datetime.now(UTC)
        with self._engine.begin() as c:
            result = c.execute(update(conversation_dispatches).where(conversation_dispatches.c.turn_id == turn_id, conversation_dispatches.c.state.in_(("pending", "enqueued"))).values(state="active", attempts=conversation_dispatches.c.attempts + 1, acquired_at=now, updated_at=now))
            if result.rowcount != 1: return None
            effective_workspace_id = func.coalesce(projects.c.workspace_id, conversation_turns.c.conversation_id)
            turn = c.execute(
                select(conversation_turns, conversations.c.project_id, projects.c.workspace_id.label("project_workspace_id"), workspace_roots.c.root_path.label("workspace_root_path"))
                .join(conversations, conversations.c.conversation_id == conversation_turns.c.conversation_id)
                .outerjoin(projects, projects.c.project_id == conversations.c.project_id)
                .outerjoin(workspace_roots, workspace_roots.c.workspace_id == effective_workspace_id)
                .where(conversation_turns.c.turn_id == turn_id)
            ).mappings().one()
            c.execute(update(conversation_turns).where(conversation_turns.c.turn_id == turn_id).values(state="starting", started_at=now, updated_at=now))
            c.execute(update(conversation_messages).where(conversation_messages.c.message_id == turn["assistant_message_id"]).values(status="streaming", updated_at=now))
            # The conversation carries the state the UI labels. Leaving it at
            # "queued" for the whole run made a working agent read as waiting.
            c.execute(update(conversations).where(conversations.c.conversation_id == turn["conversation_id"]).values(state="running", updated_at=now))
            c.execute(insert(conversation_events).values(conversation_id=turn["conversation_id"], user_id=turn["user_id"], event_type="turn.starting", message_id=turn["assistant_message_id"], payload={"state": "starting"}, created_at=now))
        self._activity(turn, AgentActivityEventType.TURN_STARTED, "Turn started")
        return dict(turn)

    def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, str]]:
        with self._engine.connect() as c:
            rows = c.execute(select(conversation_messages.c.message_id, conversation_messages.c.role, conversation_messages.c.content).where(conversation_messages.c.conversation_id == turn["conversation_id"], conversation_messages.c.sequence <= select(conversation_messages.c.sequence).where(conversation_messages.c.message_id == turn["user_message_id"]).scalar_subquery()).order_by(conversation_messages.c.sequence)).mappings().all()
            attachment_rows = c.execute(select(conversation_message_attachments).where(conversation_message_attachments.c.conversation_id == turn["conversation_id"]).order_by(conversation_message_attachments.c.id)).mappings().all()
            command_rows = c.execute(select(
                conversation_message_commands.c.message_id, conversation_message_commands.c.expanded_body
            ).where(conversation_message_commands.c.conversation_id == turn["conversation_id"])).mappings().all()
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in attachment_rows:
            grouped.setdefault(str(row["message_id"]), []).append(dict(row))
        expansions = {str(row["message_id"]): str(row["expanded_body"]) for row in command_rows}
        history: list[dict[str, str]] = []
        for row in rows:
            content = expansions.get(str(row["message_id"]), str(row["content"]))
            records = grouped.get(str(row["message_id"]), [])
            if records:
                content = f"{content}{_attachment_marker(records)}"
            history.append({"role": str(row["role"]), "content": content})
        return history

    def hook_context(self, conversation_id: str) -> str | None:
        with self._engine.connect() as c:
            rows = c.execute(select(conversation_hook_context.c.body).where(
                conversation_hook_context.c.conversation_id == conversation_id
            ).order_by(conversation_hook_context.c.id)).scalars().all()
        return "\n\n".join(str(row) for row in rows) if rows else None

    def record_hook_context(self, conversation_id: str, body: str, *, user_id: str = "", plugin_id: str = "", hook_id: str = "session-start") -> None:
        with self._engine.begin() as c:
            c.execute(insert(conversation_hook_context).values(
                conversation_id=conversation_id, user_id=user_id, plugin_id=plugin_id,
                hook_id=hook_id, body=body, created_at=datetime.now(UTC),
            ))

    def attachments_for_turn(self, turn: Mapping[str, object]) -> list[dict[str, object]]:
        """The attachment records of this turn's own user message, insertion order.

        This is the only place ``TurnSession`` needs to read attachments back:
        a model that cannot call tools has no other way to learn what is in
        the file the person just attached (see ``pre_read_attachments``).
        """
        with self._engine.connect() as c:
            rows = c.execute(
                select(conversation_message_attachments)
                .where(conversation_message_attachments.c.message_id == turn["user_message_id"])
                .order_by(conversation_message_attachments.c.id)
            ).mappings().all()
        return [{
            "path": str(row["path"]), "original_name": str(row["original_name"]),
            "media_type": str(row["media_type"]), "kind": str(row["kind"]), "bytes": int(row["bytes"]),
        } for row in rows]

    def delta(self, turn: dict[str, object], text: str) -> None:
        if not text: return
        now = datetime.now(UTC)
        with self._engine.begin() as c:
            c.execute(update(conversation_messages).where(conversation_messages.c.message_id == turn["assistant_message_id"]).values(content=conversation_messages.c.content + text, status="streaming", updated_at=now))
            c.execute(insert(conversation_events).values(conversation_id=turn["conversation_id"], user_id=turn["user_id"], event_type="message.delta", message_id=turn["assistant_message_id"], payload={"content": text}, created_at=now))
        # The assistant message stays the durable content projection; the delta
        # event carries the same chunk so an attached client can render the text
        # as it arrives instead of re-fetching the whole conversation.
        for chunk in _chunks(text, 480):
            self._activity(turn, AgentActivityEventType.ASSISTANT_DELTA, "Resposta em andamento", {"content": chunk, "message_id": str(turn["assistant_message_id"])})

    def record(self, turn: dict[str, object], event_type: AgentActivityEventType, summary: str, payload: dict[str, object] | None = None, *, agent_id: str | None = None, parent_agent_id: str | None = None) -> None:
        """Public activity seam used by the agentic runtime and its tools."""
        self._activity(turn, event_type, summary, payload, agent_id=agent_id, parent_agent_id=parent_agent_id)

    def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
        now = datetime.now(UTC)
        # A cancelled turn is a distinct terminal state, not a failure: the UI
        # must not offer "retry" for something the user chose to stop.
        state = "cancelled" if code == "TURN_CANCELLED" else ("waiting_user" if code == "WAITING_USER" else ("failed" if failed else "completed"))
        retryable = failed and state != "cancelled"
        with self._engine.begin() as c:
            # A turn only finishes once. Without this guard a late projection
            # error in the worker could rewrite an answered turn as failed.
            current = c.execute(select(conversation_turns.c.state).where(conversation_turns.c.turn_id == turn["turn_id"])).scalar()
            if str(current) in ("completed", "failed", "cancelled", "waiting_user"):
                return
            c.execute(update(conversation_dispatches).where(conversation_dispatches.c.turn_id == turn["turn_id"]).values(state=state, last_error=code, updated_at=now))
            c.execute(update(conversation_turns).where(conversation_turns.c.turn_id == turn["turn_id"]).values(state=state, finished_at=now, updated_at=now))
            c.execute(update(conversation_messages).where(conversation_messages.c.message_id == turn["assistant_message_id"]).values(status=state, retryable=retryable, updated_at=now))
            c.execute(update(conversations).where(conversations.c.conversation_id == turn["conversation_id"]).values(state=state, updated_at=now))
            c.execute(insert(conversation_events).values(conversation_id=turn["conversation_id"], user_id=turn["user_id"], event_type=f"turn.{state}", message_id=turn["assistant_message_id"], payload={"state": state, "retryable": retryable}, created_at=now))
        summaries = {"cancelled": "Execução cancelada", "failed": "Não foi possível concluir", "completed": "Resposta concluída"}
        summaries["waiting_user"] = "Aguardando sua resposta"
        self._activity(
            turn,
            AgentActivityEventType.TURN_FAILED if failed else (AgentActivityEventType.TURN_WAITING_USER if state == "waiting_user" else AgentActivityEventType.TURN_COMPLETED),
            summaries[state],
            {"state": state, "retryable": retryable, "error_code": code},
        )

    def overview(self, conversation_id: str, user_id: str) -> dict[str, object]:
        """Aggregate one conversation into the shape the overview scene draws."""
        with self._engine.connect() as c:
            conv = c.execute(select(conversations).where(conversations.c.conversation_id == conversation_id, conversations.c.user_id == user_id)).mappings().first()
            if conv is None: raise ApplicationNotFoundError(conversation_id)
            turns = c.execute(select(conversation_turns).where(conversation_turns.c.conversation_id == conversation_id).order_by(conversation_turns.c.created_at)).mappings().all()
            agent_rows = c.execute(select(conversation_agents).where(conversation_agents.c.conversation_id == conversation_id).order_by(conversation_agents.c.created_at)).mappings().all()
            usage_rows = c.execute(select(conversation_agent_usage).where(
                conversation_agent_usage.c.conversation_id == conversation_id,
                conversation_agent_usage.c.user_id == user_id,
            )).mappings().all()
            activity = c.execute(
                select(conversation_activity_events)
                .where(conversation_activity_events.c.conversation_id == conversation_id, conversation_activity_events.c.user_id == user_id)
                .order_by(conversation_activity_events.c.id)
            ).mappings().all()
        main_id = f"agent:{conversation_id}:main"
        usage_by_agent = {
            str(row["agent_id"]): _token_usage(row)
            for row in usage_rows
        }
        unavailable_usage = _unavailable_token_usage()
        agents: list[dict[str, object]] = [{
            "agent_id": main_id, "name": "Main", "role": "Agente principal desta conversa",
            "parent_agent_id": None, "provider": conv["provider"], "model_id": conv["model_id"],
            "token_usage": usage_by_agent.get(main_id, unavailable_usage),
            "state": "completed" if conv["state"] in ("completed", "failed", "cancelled") else "working",
        }]
        agents += [{
            "agent_id": r["agent_id"], "name": r["name"], "role": r["role"],
            "parent_agent_id": r["parent_agent_id"] or main_id,
            "provider": r["provider"] or conv["provider"], "model_id": r["model_id"] or conv["model_id"],
            "token_usage": usage_by_agent.get(str(r["agent_id"]), unavailable_usage), "state": r["state"],
        } for r in agent_rows]
        tools: dict[str, dict[str, object]] = {}
        messages: list[dict[str, object]] = []
        errors: list[dict[str, object]] = []
        for row in activity:
            payload = dict(row["payload"] or {})
            if row["event_type"] == AgentActivityEventType.TOOL_FINISHED.value:
                name = str(payload.get("tool_name") or "tool")
                entry = tools.setdefault(name, {"tool_name": name, "kind": payload.get("tool_kind") or "tool", "count": 0, "failures": 0, "agent_id": row["agent_id"]})
                entry["count"] = int(entry["count"]) + 1
                if payload.get("status") != "succeeded":
                    entry["failures"] = int(entry["failures"]) + 1
            elif row["event_type"] in (AgentActivityEventType.AGENT_MESSAGE_SENT.value, AgentActivityEventType.AGENT_MESSAGE_RECEIVED.value):
                sent = row["event_type"] == AgentActivityEventType.AGENT_MESSAGE_SENT.value
                messages.append({
                    "event_id": row["event_id"],
                    "from_agent_id": main_id if sent else str(payload.get("from_agent_id") or row["agent_id"]),
                    "to_agent_id": str(payload.get("to_agent_id") or row["agent_id"]) if sent else main_id,
                    "preview": str(payload.get("content") or "")[:400],
                    "occurred_at": row["created_at"].isoformat(),
                })
            elif row["event_type"] == AgentActivityEventType.TURN_FAILED.value and payload.get("error_code"):
                errors.append({"event_id": row["event_id"], "code": str(payload["error_code"]), "summary": row["summary"], "occurred_at": row["created_at"].isoformat()})
        started = next((t["started_at"] for t in turns if t["started_at"]), None)
        finished = turns[-1]["finished_at"] if turns and turns[-1]["finished_at"] else None
        duration = (finished - started).total_seconds() if started and finished else None
        return {
            "conversation_id": conversation_id, "title": conv["title"], "state": conv["state"],
            "provider": conv["provider"], "model_id": conv["model_id"],
            "agents": agents, "tools": list(tools.values()), "messages": messages, "errors": errors,
            "turns": [{"turn_id": t["turn_id"], "state": t["state"], "created_at": t["created_at"].isoformat(), "started_at": t["started_at"].isoformat() if t["started_at"] else None, "finished_at": t["finished_at"].isoformat() if t["finished_at"] else None} for t in turns],
            "activity_count": len(activity),
            "duration_seconds": duration,
            "token_usage": _total_token_usage(usage_by_agent.values()),
        }

    def request_cancel(self, conversation_id: str, user_id: str) -> dict[str, object]:
        """Mark the conversation's live turns as cancelling.

        The worker observes this flag between provider events and between tool
        calls, so a stop actually reaches the running turn instead of only
        hiding the spinner. A turn that never got picked up is finished here
        directly, because no worker will ever observe the flag for it.
        """
        now = datetime.now(UTC)
        cancelled: list[str] = []
        with self._engine.begin() as c:
            rows = c.execute(select(conversation_turns).where(
                conversation_turns.c.conversation_id == conversation_id,
                conversation_turns.c.user_id == user_id,
                conversation_turns.c.state.in_(("queued", "starting", "running", "cancelling")),
            )).mappings().all()
            for turn in rows:
                c.execute(update(conversation_turns).where(conversation_turns.c.turn_id == turn["turn_id"]).values(state="cancelling", updated_at=now))
                cancelled.append(str(turn["turn_id"]))
        for turn in rows:
            if turn["started_at"] is None:
                self.finish(dict(turn), failed=True, code="TURN_CANCELLED")
        return {"conversation_id": conversation_id, "cancelling": cancelled}

    def cancel_requested(self, turn_id: str) -> bool:
        with self._engine.connect() as c:
            state = c.execute(select(conversation_turns.c.state).where(conversation_turns.c.turn_id == turn_id)).scalar()
        return str(state) == "cancelling"

    def recover_stale(self, *, maximum_age: timedelta = timedelta(seconds=120)) -> tuple[str, ...]:
        """Requeue active dispatches whose worker heartbeat was lost."""
        cutoff = datetime.now(UTC) - maximum_age
        requeued_at = datetime.now(UTC)
        recovered: list[str] = []
        with self._engine.begin() as c:
            rows = c.execute(
                select(conversation_dispatches.c.turn_id).where(
                    conversation_dispatches.c.state == "active",
                    conversation_dispatches.c.acquired_at.is_not(None),
                    conversation_dispatches.c.acquired_at < cutoff,
                )
            ).scalars().all()
            for turn_id in rows:
                changed = c.execute(
                    update(conversation_dispatches).where(
                        conversation_dispatches.c.turn_id == turn_id,
                        conversation_dispatches.c.state == "active",
                    ).values(state="pending", last_error="worker_recovered", queued_at=requeued_at, acquired_at=None, updated_at=requeued_at)
                )
                if changed.rowcount:
                    turn = c.execute(select(conversation_turns).where(conversation_turns.c.turn_id == turn_id)).mappings().one()
                    c.execute(update(conversation_turns).where(conversation_turns.c.turn_id == turn_id).values(state="queued", updated_at=datetime.now(UTC)))
                    c.execute(update(conversation_messages).where(conversation_messages.c.message_id == turn["assistant_message_id"]).values(status="queued", updated_at=datetime.now(UTC)))
                    c.execute(update(conversations).where(conversations.c.conversation_id == turn["conversation_id"]).values(state="queued", updated_at=datetime.now(UTC)))
                    c.execute(insert(conversation_events).values(conversation_id=turn["conversation_id"], user_id=turn["user_id"], event_type="turn.recovered", message_id=turn["assistant_message_id"], payload={"state": "queued", "reason": "worker_recovered"}, created_at=datetime.now(UTC)))
                    recovered.append(str(turn_id))
        return tuple(recovered)

    def heartbeat(self, component: str) -> None:
        now = datetime.now(UTC)
        with self._engine.begin() as c:
            exists = c.execute(select(runtime_heartbeats.c.component).where(runtime_heartbeats.c.component == component)).scalar()
            if exists: c.execute(update(runtime_heartbeats).where(runtime_heartbeats.c.component == component).values(updated_at=now))
            else: c.execute(insert(runtime_heartbeats).values(component=component, updated_at=now))

    @staticmethod
    def execution_id_for(turn_id: str) -> str:
        return f"exe_{sha256(turn_id.encode()).hexdigest()}"


class ChatApplication:
    """Gateway port. Execution persistence remains the source of technical state."""
    def __init__(self, store: PostgresChatStore, executions) -> None: self.store, self._executions = store, executions
    def allocate_conversation_id(self) -> str:
        return _id("chat")
    def create(self, context, *, message: str, provider: str, model_id: str, workspace_id: str | None, idempotency_key: str, project_id: str | None = None, attachments=(), new_conversation_id: str | None = None):
        receipt = self.store.create(user_id=context.user_id, message=message, provider=provider, model_id=model_id, idempotency_key=idempotency_key, project_id=project_id, attachments=attachments, new_conversation_id=new_conversation_id)
        self._project_execution(receipt, context.user_id, workspace_id, idempotency_key)
        return receipt
    def send(self, user_id: str, conversation_id: str, message: str, idempotency_key: str, attachments=(), provider: str = "", model_id: str = ""):
        receipt = self.store.create(user_id=user_id, message=message, provider=provider, model_id=model_id, idempotency_key=idempotency_key, conversation_id=conversation_id, attachments=attachments)
        self._project_execution(receipt, user_id, None, idempotency_key)
        return receipt
    def list(self, user_id: str): return self.store.list(user_id)
    def get(self, conversation_id: str, user_id: str): return self.store.get(conversation_id, user_id)
    def events(self, conversation_id: str, user_id: str, after: int): return self.store.events(conversation_id, user_id, after)
    def cancel(self, conversation_id: str, user_id: str): return self.store.request_cancel(conversation_id, user_id)
    def overview(self, conversation_id: str, user_id: str): return self.store.overview(conversation_id, user_id)
    def _project_execution(self, receipt, user_id, workspace_id, key) -> None:
        """Best-effort execution write; see README "Execution records".

        The turn itself is already durably committed by ``self.store.create``
        -- conversation, messages, attachment rows, turn and the ``pending``
        dispatch row, all in one transaction -- and that dispatch row can be
        claimed by a worker within milliseconds. A failure writing the
        technical execution projection must never propagate: raising here
        would reach the gateway route, whose ``except Exception:`` handler
        deletes the just-promoted attachment files, orphaning a turn that is
        already live and already eligible to run.
        """
        try:
            self._ensure_execution(receipt, user_id, workspace_id, key)
        except Exception:
            _LOGGER.warning("execution projection for turn %s was skipped", receipt.turn_id, exc_info=True)
    def _ensure_execution(self, receipt, user_id, workspace_id, key):
        digest = sha256(f"{user_id}|{receipt.turn_id}".encode()).hexdigest()
        self._executions.create({"operation_id": f"op_{digest}", "context": {"user_id": user_id, "workspace_id": workspace_id, "agent_id": f"chat-agent-{digest[:16]}", "execution_id": PostgresChatStore.execution_id_for(receipt.turn_id), "correlation_id": "", "purpose": "conversation.turn"}, "task_ref": f"conversation-turn:{receipt.turn_id}", "limits": {"max_duration_seconds": 120}, "expected_agent_version": 1, "idempotency_key": f"chat:{key}", "requested_at": datetime.now(UTC)})

__all__ = ["ChatApplication", "ChatReceipt", "PostgresChatStore"]

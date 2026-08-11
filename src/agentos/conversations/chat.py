"""Durable public conversation application service and worker-facing store."""
from __future__ import annotations

import json
from collections.abc import Mapping
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
from agentos.persistence.postgres.schema import (conversation_activity_events, conversation_agent_usage, conversation_agents, conversation_dispatches, conversation_events, conversation_messages, conversation_tool_records, conversation_turns, conversations, projects, runtime_heartbeats)


def _id(prefix: str) -> str: return f"{prefix}_{uuid4().hex}"
def _title(message: str) -> str: return " ".join(message.split())[:80] or "Nova conversa"


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
    def __init__(self, engine: Engine, activity_store=None) -> None:
        self._engine = engine
        self.activity_store = activity_store

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
            try:
                sequence = self._next_activity_sequence(str(turn["turn_id"]))
                self.activity_store.record_event(AgentActivityEvent(
                    event_id=f"activity:{turn['turn_id']}:{sequence}",
                    conversation_id=str(turn["conversation_id"]), turn_id=str(turn["turn_id"]),
                    execution_id=str(turn["execution_id"]), user_id=str(turn["user_id"]),
                    agent_id=agent_id or self.main_agent_id(turn), parent_agent_id=parent_agent_id,
                    event_type=event_type, sequence=sequence,
                    summary=summary[:512] or "Atividade", payload=payload or {}, created_at=datetime.now(UTC),
                ))
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

    @staticmethod
    def main_agent_id(turn: Mapping[str, object]) -> str:
        # Stable across turns so the conversation-level graph keeps one root node.
        return f"agent:{turn['conversation_id']}:main"

    def create(self, *, user_id: str, message: str, provider: str, model_id: str, idempotency_key: str, conversation_id: str | None = None, project_id: str | None = None) -> ChatReceipt:
        message = message.strip()
        if not message or len(message) > 16000: raise ValueError("message must be a bounded non-blank string")
        now = datetime.now(UTC)
        with self._engine.begin() as c:
            previous = c.execute(select(conversation_turns).where(conversation_turns.c.user_id == user_id, conversation_turns.c.idempotency_key == idempotency_key)).mappings().first()
            if previous:
                row = c.execute(select(conversations).where(conversations.c.conversation_id == previous["conversation_id"])).mappings().one()
                return ChatReceipt(previous["conversation_id"], row["title"], previous["turn_id"], previous["assistant_message_id"], previous["state"])
            if conversation_id is None:
                conversation_id = _id("chat")
                if project_id is not None:
                    from agentos.persistence.postgres.schema import projects
                    project = c.execute(select(projects.c.project_id).where(projects.c.project_id == project_id, projects.c.user_id == user_id, projects.c.archived_at.is_(None))).scalar()
                    if project is None: raise ApplicationNotFoundError(project_id)
                c.execute(insert(conversations).values(conversation_id=conversation_id, user_id=user_id, title=_title(message), provider=provider, model_id=model_id, project_id=project_id, state="queued", created_at=now, updated_at=now))
                sequence = 1
            else:
                row = c.execute(select(conversations).where(conversations.c.conversation_id == conversation_id, conversations.c.user_id == user_id)).mappings().first()
                if row is None: raise ApplicationNotFoundError(conversation_id)
                provider, model_id = row["provider"], row["model_id"]
                sequence = int(c.execute(select(func.max(conversation_messages.c.sequence)).where(conversation_messages.c.conversation_id == conversation_id)).scalar() or 0) + 1
            turn_id, user_message_id, assistant_message_id = _id("turn"), _id("msg"), _id("msg")
            execution_id = self.execution_id_for(turn_id)
            c.execute(insert(conversation_messages), [
                {"message_id": user_message_id, "conversation_id": conversation_id, "turn_id": turn_id, "user_id": user_id, "role": "user", "content": message, "sequence": sequence, "status": "completed", "retryable": False, "created_at": now, "updated_at": now},
                {"message_id": assistant_message_id, "conversation_id": conversation_id, "turn_id": turn_id, "user_id": user_id, "role": "assistant", "content": "", "sequence": sequence + 1, "status": "queued", "retryable": False, "created_at": now, "updated_at": now},
            ])
            c.execute(insert(conversation_turns).values(turn_id=turn_id, conversation_id=conversation_id, user_id=user_id, execution_id=execution_id, user_message_id=user_message_id, assistant_message_id=assistant_message_id, provider=provider, model_id=model_id, state="queued", idempotency_key=idempotency_key, created_at=now, updated_at=now))
            c.execute(insert(conversation_dispatches).values(turn_id=turn_id, state="pending", attempts=0, queued_at=now, updated_at=now))
            c.execute(insert(conversation_events).values(conversation_id=conversation_id, user_id=user_id, event_type="turn.queued", message_id=assistant_message_id, payload={"state": "queued"}, created_at=now))
            c.execute(update(conversations).where(conversations.c.conversation_id == conversation_id).values(state="queued", updated_at=now))
        receipt = ChatReceipt(conversation_id, _title(message), turn_id, assistant_message_id, "queued")
        self._activity({"conversation_id": conversation_id, "turn_id": turn_id, "execution_id": execution_id, "user_id": user_id}, AgentActivityEventType.TURN_STARTED, "Turn queued")
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
        return {"conversation_id": conv["conversation_id"], "title": conv["title"], "state": conv["state"], "provider": conv["provider"], "model_id": conv["model_id"], "project_id": conv["project_id"], "messages": [{"message_id": m["message_id"], "role": m["role"], "content": m["content"], "status": m["status"], "retryable": bool(m["retryable"])} for m in messages], "turns": [{"turn_id": t["turn_id"], "state": t["state"], "created_at": t["created_at"].isoformat(), "started_at": t["started_at"].isoformat() if t["started_at"] else None, "finished_at": t["finished_at"].isoformat() if t["finished_at"] else None} for t in turns], "activities": activities, "activity_cursor": activity_cursor}

    @staticmethod
    def _public_activity(event: AgentActivityEvent, cursor: str | None = None) -> dict[str, object]:
        # The whole payload is already bounded and secret-redacted by
        # AgentActivityEvent, so it can be published as-is rather than through a
        # hand-maintained key allowlist that silently drops new event fields.
        return {
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "sequence": event.sequence,
            "summary": event.summary,
            "payload": _plain(event.payload),
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
            turn = c.execute(
                select(conversation_turns, conversations.c.project_id, projects.c.workspace_id.label("project_workspace_id"))
                .join(conversations, conversations.c.conversation_id == conversation_turns.c.conversation_id)
                .outerjoin(projects, projects.c.project_id == conversations.c.project_id)
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
        with self._engine.connect() as c: rows = c.execute(select(conversation_messages.c.role, conversation_messages.c.content).where(conversation_messages.c.conversation_id == turn["conversation_id"], conversation_messages.c.sequence <= select(conversation_messages.c.sequence).where(conversation_messages.c.message_id == turn["user_message_id"]).scalar_subquery()).order_by(conversation_messages.c.sequence)).mappings().all()
        return [{"role": str(r["role"]), "content": str(r["content"])} for r in rows]

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
        state = "cancelled" if code == "TURN_CANCELLED" else ("failed" if failed else "completed")
        retryable = failed and state != "cancelled"
        with self._engine.begin() as c:
            # A turn only finishes once. Without this guard a late projection
            # error in the worker could rewrite an answered turn as failed.
            current = c.execute(select(conversation_turns.c.state).where(conversation_turns.c.turn_id == turn["turn_id"])).scalar()
            if str(current) in ("completed", "failed", "cancelled"):
                return
            c.execute(update(conversation_dispatches).where(conversation_dispatches.c.turn_id == turn["turn_id"]).values(state=state, last_error=code, updated_at=now))
            c.execute(update(conversation_turns).where(conversation_turns.c.turn_id == turn["turn_id"]).values(state=state, finished_at=now, updated_at=now))
            c.execute(update(conversation_messages).where(conversation_messages.c.message_id == turn["assistant_message_id"]).values(status=state, retryable=retryable, updated_at=now))
            c.execute(update(conversations).where(conversations.c.conversation_id == turn["conversation_id"]).values(state=state, updated_at=now))
            c.execute(insert(conversation_events).values(conversation_id=turn["conversation_id"], user_id=turn["user_id"], event_type=f"turn.{state}", message_id=turn["assistant_message_id"], payload={"state": state, "retryable": retryable}, created_at=now))
        summaries = {"cancelled": "Execução cancelada", "failed": "Não foi possível concluir", "completed": "Resposta concluída"}
        self._activity(
            turn,
            AgentActivityEventType.TURN_FAILED if failed else AgentActivityEventType.TURN_COMPLETED,
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
    def create(self, context, *, message: str, provider: str, model_id: str, workspace_id: str | None, idempotency_key: str, project_id: str | None = None):
        receipt = self.store.create(user_id=context.user_id, message=message, provider=provider, model_id=model_id, idempotency_key=idempotency_key, project_id=project_id)
        self._ensure_execution(receipt, context.user_id, workspace_id, idempotency_key)
        return receipt
    def send(self, user_id: str, conversation_id: str, message: str, idempotency_key: str):
        receipt = self.store.create(user_id=user_id, message=message, provider="", model_id="", idempotency_key=idempotency_key, conversation_id=conversation_id)
        self._ensure_execution(receipt, user_id, None, idempotency_key)
        return receipt
    def list(self, user_id: str): return self.store.list(user_id)
    def get(self, conversation_id: str, user_id: str): return self.store.get(conversation_id, user_id)
    def events(self, conversation_id: str, user_id: str, after: int): return self.store.events(conversation_id, user_id, after)
    def cancel(self, conversation_id: str, user_id: str): return self.store.request_cancel(conversation_id, user_id)
    def overview(self, conversation_id: str, user_id: str): return self.store.overview(conversation_id, user_id)
    def _ensure_execution(self, receipt, user_id, workspace_id, key):
        digest = sha256(f"{user_id}|{receipt.turn_id}".encode()).hexdigest()
        self._executions.create({"operation_id": f"op_{digest}", "context": {"user_id": user_id, "workspace_id": workspace_id, "agent_id": f"chat-agent-{digest[:16]}", "execution_id": PostgresChatStore.execution_id_for(receipt.turn_id), "correlation_id": "", "purpose": "conversation.turn"}, "task_ref": f"conversation-turn:{receipt.turn_id}", "limits": {"max_duration_seconds": 120}, "expected_agent_version": 1, "idempotency_key": f"chat:{key}", "requested_at": datetime.now(UTC)})

__all__ = ["ChatApplication", "ChatReceipt", "PostgresChatStore"]

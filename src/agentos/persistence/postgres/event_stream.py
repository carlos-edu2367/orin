"""Durable production ``ClientEventStream`` backed by the real outbox (frontend Fase 0/B).

Same public shape and cursor/epoch semantics as
``agentos.api.events.InMemoryClientEventStream`` (open/read/delivery_permitted,
HMAC-signed opaque cursors, per-stream revocation epoch), but reads events
from three durable sources, unioned and ordered chronologically:

- ``persistence_outbox`` — every execution transition
  (``agentos.execution.control.ExecutionControlService``);
- ``multi_agent_events`` — delegation/message/wait facts, written through
  ``agentos.persistence.postgres.multi_agent_events.PostgresMultiAgentEventRecorder``
  (frontend Fase B.1);
- ``tool_activity_events`` — tool invocation facts, written through
  ``agentos.persistence.postgres.tool_activity.PostgresToolActivitySink``
  (frontend Fase B.2).

Stream bindings persist in ``event_stream_bindings`` so a binding survives a
restart or a different process handling a later request.

There is deliberately no ``append`` here: production events arrive only
through the three durable writers above, never from direct writes by the
HTTP layer.

Payload translation (Fase B.3 "Decisões locais", see docs/frontend/
PROJECT_CLOSEOUT_ROADMAP.md and IMPLEMENTATION_PLAN.md): the multi_agent
domain fact for ``DelegationCreated`` stores ``child_execution_id``/
``child_agent_id`` only implicitly, as the envelope's own
``execution_id``/``agent_id`` (see ``MultiAgentCoordinatorService.delegate``
in ``agentos.multi_agent.service``), not inside its ``payload`` dict. The
frontend's already-written ``agentGraphProjection.ts`` reads
``payload.child_execution_id``/``payload.child_agent_id`` (Fase 4 "Decisões
locais" of IMPLEMENTATION_PLAN.md). This projector translates the envelope
fields into those payload keys so the existing frontend contract is
satisfied without changing the frontend or the domain event shape.
``AgentMessageCreated`` is translated the same way for
``recipient_agent_id`` (from the envelope's ``agent_id``, which
``_record_message_fact`` sets to ``command.recipient_agent_id``) — but
``sender_agent_id`` is a genuine domain gap, not a naming mismatch: the
persisted fact never captures the sender at all (see
``MultiAgentCoordinatorService.send``/``_record_message_fact``), so it is
never present in the projected payload. ``DelegationResultReturned``,
``AgentWaitRegistered``/``AgentWaitSatisfied`` already store the field names
the frontend expects (``delegation_id``/``wait_id``) and need no
translation. Tool facts get ``payload.invocation_id`` (top-level field on
``ToolOutboxEntry``, not originally in its payload) and
``payload.tool_kind`` (``ToolOutboxEntry.tool_ref.tool_id``) injected, to
match what ``ToolActivityView``/``activityNormalizer.ts`` (Fase 3) expects.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from agentos.api.events import ClientEvent, CursorError, StreamBinding
from agentos.api.security import AuthenticatedPrincipal

from .schema import event_stream_bindings, multi_agent_events, persistence_outbox, tool_activity_events


_SOURCES = ("outbox", "multi_agent", "tool_activity")


class PostgresClientEventStream:
    """Production adapter for the client-facing event stream (frontend Fase 0/B)."""

    def __init__(self, engine: Engine, signing_key: bytes = b"agentos-production-stream-cursor") -> None:
        self._engine = engine
        self._signing_key = signing_key

    def open(self, principal: AuthenticatedPrincipal, execution_ids: Iterable[str], epoch: int) -> tuple[StreamBinding, str]:
        requested = tuple(execution_ids)
        normalized = tuple(sorted(set(requested)))
        if not normalized or len(normalized) != len(requested):
            raise ValueError("stream selection must be non-empty and contain no duplicates")
        digest = hashlib.sha256((principal.user_id + "|" + "|".join(normalized)).encode()).hexdigest()
        binding = StreamBinding(f"stream_{uuid4().hex}", principal.user_id, principal.credential_ref, normalized, digest, epoch)
        with self._engine.begin() as connection:
            connection.execute(insert(event_stream_bindings).values(
                stream_id=binding.stream_id,
                user_id=binding.user_id,
                credential_ref=binding.credential_ref,
                execution_ids=list(binding.execution_ids),
                digest=binding.digest,
                epoch=binding.epoch,
                created_at=datetime.now(UTC),
            ))
        return binding, self._cursor(principal, binding, {source: 0 for source in _SOURCES})

    def delivery_permitted(self, principal: AuthenticatedPrincipal, stream_id: str, epoch: int) -> bool:
        binding = self._load_binding(stream_id)
        return bool(binding and binding.user_id == principal.user_id and binding.credential_ref == principal.credential_ref and binding.epoch == epoch)

    def read(self, principal: AuthenticatedPrincipal, stream_id: str, cursor: str, epoch: int, maximum_events: int = 100) -> tuple[list[ClientEvent], str]:
        binding = self._load_binding(stream_id)
        if binding is None or binding.user_id != principal.user_id or binding.credential_ref != principal.credential_ref:
            raise PermissionError("stream is not available")
        if binding.epoch != epoch:
            raise PermissionError("stream authorization has been revoked")
        positions = self._parse_cursor(principal, binding, cursor)
        with self._engine.connect() as connection:
            outbox_rows = connection.execute(
                select(persistence_outbox).where(
                    persistence_outbox.c.id > positions["outbox"],
                    persistence_outbox.c.user_id == principal.user_id,
                    persistence_outbox.c.execution_id.in_(binding.execution_ids),
                ).order_by(persistence_outbox.c.id).limit(maximum_events)
            ).mappings().all()
            multi_agent_rows = connection.execute(
                select(multi_agent_events).where(
                    multi_agent_events.c.id > positions["multi_agent"],
                    multi_agent_events.c.user_id == principal.user_id,
                    multi_agent_events.c.execution_id.in_(binding.execution_ids),
                ).order_by(multi_agent_events.c.id).limit(maximum_events)
            ).mappings().all()
            tool_rows = connection.execute(
                select(tool_activity_events).where(
                    tool_activity_events.c.id > positions["tool_activity"],
                    tool_activity_events.c.user_id == principal.user_id,
                    tool_activity_events.c.execution_id.in_(binding.execution_ids),
                ).order_by(tool_activity_events.c.id).limit(maximum_events)
            ).mappings().all()
        combined: list[tuple[datetime, int, str, int, ClientEvent]] = []
        for row in outbox_rows:
            combined.append((row["created_at"], row["id"], "outbox", int(row["id"]), self._outbox_client_event(row)))
        for row in multi_agent_rows:
            combined.append((row["created_at"], row["id"], "multi_agent", int(row["id"]), self._multi_agent_client_event(row)))
        for row in tool_rows:
            combined.append((row["created_at"], row["id"], "tool_activity", int(row["id"]), self._tool_activity_client_event(row)))
        combined.sort(key=lambda item: (item[0], _SOURCES.index(item[2]), item[1]))
        selected = combined[:maximum_events]
        next_positions = dict(positions)
        for _created_at, row_id, source, _sequence, _event in selected:
            next_positions[source] = max(next_positions[source], row_id)
        return [item[4] for item in selected], self._cursor(principal, binding, next_positions)

    def _load_binding(self, stream_id: str) -> StreamBinding | None:
        with self._engine.connect() as connection:
            row = connection.execute(select(event_stream_bindings).where(event_stream_bindings.c.stream_id == stream_id)).mappings().first()
        if row is None:
            return None
        return StreamBinding(row["stream_id"], row["user_id"], row["credential_ref"], tuple(row["execution_ids"]), row["digest"], row["epoch"])

    @staticmethod
    def _outbox_client_event(row: Any) -> ClientEvent:
        event = row["event"]
        return ClientEvent(
            event_id=str(event["event_id"]),
            execution_id=str(row["execution_id"]),
            event_type=str(event["event_type"]),
            payload=dict(event.get("payload") or {}),
            sequence=int(event["sequence"]),
            occurred_at=datetime.fromisoformat(str(event["occurred_at"])),
        )

    @staticmethod
    def _multi_agent_client_event(row: Any) -> ClientEvent:
        event = row["event"]
        payload = dict(event.get("payload") or {})
        event_type = str(event["event_type"])
        if event_type == "DelegationCreated":
            if row["execution_id"] is not None:
                payload.setdefault("child_execution_id", str(row["execution_id"]))
            if row["agent_id"] is not None:
                payload.setdefault("child_agent_id", str(row["agent_id"]))
        elif event_type in ("AgentMessageCreated", "AgentMessageExpired") and row["agent_id"] is not None:
            payload.setdefault("recipient_agent_id", str(row["agent_id"]))
        return ClientEvent(
            event_id=str(event["event_id"]),
            execution_id=str(row["execution_id"]),
            event_type=event_type,
            payload=payload,
            sequence=int(row["id"]),
            occurred_at=datetime.fromisoformat(str(event["occurred_at"])),
        )

    @staticmethod
    def _tool_activity_client_event(row: Any) -> ClientEvent:
        event = row["event"]
        payload = dict(event.get("payload") or {})
        return ClientEvent(
            event_id=str(row["event_id"]),
            execution_id=str(row["execution_id"]),
            event_type=str(event["event_type"]),
            payload=payload,
            sequence=int(row["id"]),
            occurred_at=datetime.fromisoformat(str(event["occurred_at"])),
        )

    def _cursor(self, principal: AuthenticatedPrincipal, binding: StreamBinding, positions: dict[str, int]) -> str:
        payload = json.dumps(
            {"u": principal.user_id, "d": binding.digest, "p": {source: positions[source] for source in _SOURCES}},
            separators=(",", ":"), sort_keys=True,
        ).encode()
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        signature = hmac.new(self._signing_key, encoded.encode(), hashlib.sha256).hexdigest()
        return f"c.{encoded}.{signature}"

    def _parse_cursor(self, principal: AuthenticatedPrincipal, binding: StreamBinding, cursor: str) -> dict[str, int]:
        try:
            prefix, encoded, signature = cursor.split(".", 2)
            expected = hmac.new(self._signing_key, encoded.encode(), hashlib.sha256).hexdigest()
            if prefix != "c" or not hmac.compare_digest(expected, signature):
                raise CursorError("cursor is invalid")
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            positions = payload["p"]
            if (
                payload["u"] != principal.user_id
                or payload["d"] != binding.digest
                or not isinstance(positions, dict)
                or set(positions) != set(_SOURCES)
                or any(not isinstance(value, int) or value < 0 for value in positions.values())
            ):
                raise CursorError("cursor is invalid")
            return {source: positions[source] for source in _SOURCES}
        except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise CursorError("cursor is invalid") from error


__all__ = ["PostgresClientEventStream"]

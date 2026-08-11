from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable
from uuid import uuid4

from .security import AuthenticatedPrincipal


class CursorError(ValueError):
    ...


@dataclass(frozen=True, slots=True)
class ClientEvent:
    event_id: str
    execution_id: str
    event_type: str
    payload: dict[str, Any]
    sequence: int
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class StreamBinding:
    stream_id: str
    user_id: str
    credential_ref: str
    execution_ids: tuple[str, ...]
    digest: str
    epoch: int


class InMemoryClientEventStream:
    """Port implementation for ASGI tests; it has no infrastructure dependency."""

    def __init__(self, signing_key: bytes = b"agentos-local-stream-cursor") -> None:
        self._signing_key = signing_key
        self._events: list[ClientEvent] = []
        self._bindings: dict[str, StreamBinding] = {}

    def append(self, execution_id: str, event_type: str, payload: dict[str, Any]) -> ClientEvent:
        event = ClientEvent(f"evt_{uuid4().hex}", execution_id, event_type, dict(payload), len(self._events) + 1, datetime.now(UTC))
        self._events.append(event)
        return event

    def open(self, principal: AuthenticatedPrincipal, execution_ids: Iterable[str], epoch: int) -> tuple[StreamBinding, str]:
        requested = tuple(execution_ids)
        normalized = tuple(sorted(set(requested)))
        if not normalized or len(normalized) != len(requested):
            raise ValueError("stream selection must be non-empty and contain no duplicates")
        digest = hashlib.sha256((principal.user_id + "|" + "|".join(normalized)).encode()).hexdigest()
        binding = StreamBinding(f"stream_{uuid4().hex}", principal.user_id, principal.credential_ref, normalized, digest, epoch)
        self._bindings[binding.stream_id] = binding
        return binding, self._cursor(principal, binding, 0)

    def delivery_permitted(self, principal: AuthenticatedPrincipal, stream_id: str, epoch: int) -> bool:
        binding = self._bindings.get(stream_id)
        return bool(binding and binding.user_id == principal.user_id and binding.credential_ref == principal.credential_ref and binding.epoch == epoch)

    def read(self, principal: AuthenticatedPrincipal, stream_id: str, cursor: str, epoch: int, maximum_events: int = 100) -> tuple[list[ClientEvent], str]:
        binding = self._bindings.get(stream_id)
        if binding is None or binding.user_id != principal.user_id or binding.credential_ref != principal.credential_ref:
            raise PermissionError("stream is not available")
        if binding.epoch != epoch:
            raise PermissionError("stream authorization has been revoked")
        position = self._parse_cursor(principal, binding, cursor)
        selected = [event for event in self._events if event.sequence > position and event.execution_id in binding.execution_ids][:maximum_events]
        next_position = selected[-1].sequence if selected else position
        return selected, self._cursor(principal, binding, next_position)

    def _cursor(self, principal: AuthenticatedPrincipal, binding: StreamBinding, position: int) -> str:
        payload = json.dumps({"u": principal.user_id, "d": binding.digest, "p": position}, separators=(",", ":"), sort_keys=True).encode()
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        signature = hmac.new(self._signing_key, encoded.encode(), hashlib.sha256).hexdigest()
        return f"c.{encoded}.{signature}"

    def _parse_cursor(self, principal: AuthenticatedPrincipal, binding: StreamBinding, cursor: str) -> int:
        try:
            prefix, encoded, signature = cursor.split(".", 2)
            expected = hmac.new(self._signing_key, encoded.encode(), hashlib.sha256).hexdigest()
            if prefix != "c" or not hmac.compare_digest(expected, signature):
                raise CursorError("cursor is invalid")
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if payload["u"] != principal.user_id or payload["d"] != binding.digest or not isinstance(payload["p"], int) or payload["p"] < 0:
                raise CursorError("cursor is invalid")
            return payload["p"]
        except (ValueError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise CursorError("cursor is invalid") from error

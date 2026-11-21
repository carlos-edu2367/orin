"""Delivery policy for messages sent by Orin Friend into live or idle sessions (WS-B8)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import select

import agentos.settings.keys_messages  # noqa: F401 - register friend.sessionDelivery key
from agentos.agentic.call_context import current_invocation_id
from agentos.api.contracts import ApplicationValidationError
from agentos.persistence.postgres.schema import conversation_turns, conversations
from agentos.settings.loader import EffectiveSettings, empty_settings, load_effective_settings
from agentos.settings.registry import resolve

_BLOCKED_PROMPT_PLACEHOLDER = "[mensagem bloqueada por hook]"

DeliveryKind = Literal["pending_in_turn", "queued_next_turn", "new_turn"]

FRIEND_MESSAGE_PREFIX = "Mensagem enviada pelo Orin Friend em nome do usuário:\n"

_LIVE_INJECTABLE_STATES: frozenset[str] = frozenset({"queued", "starting", "running"})
_ALLOWED_DELIVERY_MODES: frozenset[str] = frozenset({"now", "queue"})


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    delivery: DeliveryKind
    queue_id: str | None
    turn_id: str | None
    delivery_fallback: str | None = None
    idempotent: bool = True
    state: str | None = None


def format_friend_injected_text(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    if clean.startswith(FRIEND_MESSAGE_PREFIX):
        return clean
    return f"{FRIEND_MESSAGE_PREFIX}{clean}"


def resolve_friend_session_delivery(
    settings: EffectiveSettings | None = None,
    *,
    workspace_root: Path | None = None,
) -> str:
    effective = settings
    if effective is None:
        try:
            effective = load_effective_settings(workspace_root)
        except Exception:  # noqa: BLE001 - settings load must never block delivery
            effective = empty_settings()
    try:
        raw = str(resolve(effective, "friend.sessionDelivery").value or "now").strip().lower()
    except Exception:  # noqa: BLE001
        raw = "now"
    return raw if raw in _ALLOWED_DELIVERY_MODES else "now"


def build_delivery_idempotency_key(
    tool_call_id: str | None = None,
    *,
    origin_conversation_id: str | None = None,
    origin_turn_id: str | None = None,
    fallback_prefix: str = "sess_msg",
) -> tuple[str, bool]:
    candidate = str(tool_call_id or "").strip() or str(current_invocation_id() or "").strip()
    if candidate:
        scope_parts = [
            part
            for part in (
                str(origin_conversation_id or "").strip(),
                str(origin_turn_id or "").strip(),
            )
            if part
        ]
        raw_key = f"friend:{':'.join(scope_parts)}:{candidate}" if scope_parts else f"friend:{candidate}"
        if len(raw_key) > 240:
            digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
            raw_key = f"friend:h:{digest}"
        return raw_key, True
    return f"{fallback_prefix}_{uuid4().hex}", False


def _find_live_turn(
    chat_store: Any,
    conversation_id: str,
    user_id: str,
    conv: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    engine = getattr(chat_store, "_engine", None)
    if engine is not None:
        try:
            with engine.connect() as conn:
                rows = (
                    conn.execute(
                        select(
                            conversation_turns.c.turn_id,
                            conversation_turns.c.state,
                            conversation_turns.c.provider,
                            conversation_turns.c.model_id,
                        )
                        .where(
                            conversation_turns.c.conversation_id == conversation_id,
                            conversation_turns.c.user_id == user_id,
                            conversation_turns.c.state.in_(("queued", "starting", "running", "cancelling")),
                        )
                        .order_by(conversation_turns.c.id.desc())
                    )
                    .mappings()
                    .all()
                )
                perm_mode: str | None = None
                if rows:
                    perm_row = conn.execute(
                        select(conversations.c.permission_mode).where(
                            conversations.c.conversation_id == conversation_id,
                            conversations.c.user_id == user_id,
                        )
                    ).first()
                    if perm_row is not None and perm_row[0]:
                        perm_mode = str(perm_row[0])
            if rows:
                for row in rows:
                    state = str(row.get("state") or "").strip().lower()
                    if state in _LIVE_INJECTABLE_STATES:
                        found = dict(row)
                        if perm_mode:
                            found["permission_mode"] = perm_mode
                        return found, state
                first = dict(rows[0])
                if perm_mode:
                    first["permission_mode"] = perm_mode
                return first, str(first.get("state") or "").strip().lower() or None
            return None, None
        except Exception:  # noqa: BLE001
            pass

    turns_list = [t for t in ((conv or {}).get("turns") or ()) if isinstance(t, Mapping)]
    for turn in reversed(turns_list):
        state = str(turn.get("state") or "").strip().lower()
        if state in _LIVE_INJECTABLE_STATES:
            return dict(turn), state
    for turn in reversed(turns_list):
        state = str(turn.get("state") or "").strip().lower()
        if state == "cancelling":
            return dict(turn), state
    return None, None


def _is_model_or_permission_change(
    *,
    requested_provider: str | None,
    requested_model_id: str | None,
    requested_permission_mode: str | None,
    effective_provider: str | None,
    effective_model_id: str | None,
    effective_permission_mode: str | None,
    live_turn: Mapping[str, Any] | None,
    conv: Mapping[str, Any] | None,
) -> bool:
    live_provider = str((live_turn or {}).get("provider") or (conv or {}).get("provider") or "").strip()
    live_model = str((live_turn or {}).get("model_id") or (conv or {}).get("model_id") or "").strip()
    live_perm = str((live_turn or {}).get("permission_mode") or (conv or {}).get("permission_mode") or "").strip()

    if requested_provider is not None:
        eff_p = str(effective_provider or requested_provider or "").strip()
        if eff_p and live_provider and eff_p != live_provider:
            return True
    if requested_model_id is not None:
        eff_m = str(effective_model_id or requested_model_id or "").strip()
        if eff_m and live_model and eff_m != live_model:
            return True
    if requested_permission_mode is not None:
        eff_perm = str(effective_permission_mode or requested_permission_mode or "").strip()
        if eff_perm and live_perm and eff_perm != live_perm:
            return True
    return False


def _create_turn(
    *,
    chat_store: Any,
    user_id: str,
    conversation_id: str,
    text: str,
    provider: str,
    model_id: str,
    idempotency_key: str,
    project_id: str | None,
    code_mode: str,
    permission_mode: str,
) -> Any:
    return chat_store.create(
        user_id=user_id,
        message=text,
        provider=provider,
        model_id=model_id,
        idempotency_key=idempotency_key,
        conversation_id=conversation_id,
        project_id=project_id,
        code_mode=code_mode,
        permission_mode=permission_mode,
    )


def deliver_to_session(
    *,
    chat_store: Any,
    user_id: str,
    conversation_id: str,
    text: str,
    delivery: str | None = None,
    origin: str = "friend",
    origin_conversation_id: str | None = None,
    origin_turn_id: str | None = None,
    idempotency_key: str | None = None,
    tool_call_id: str | None = None,
    requested_model_change: bool | None = None,
    requested_provider: str | None = None,
    requested_model_id: str | None = None,
    requested_permission_mode: str | None = None,
    provider: str = "",
    model_id: str = "",
    permission_mode: str = "accept_edits",
    project_id: str | None = None,
    code_mode: str = "chat",
    conv: Mapping[str, Any] | None = None,
    settings: EffectiveSettings | None = None,
    workspace_root: Path | None = None,
    idempotent: bool | None = None,
) -> DeliveryResult:
    """Route a Friend follow-up message into the running turn or a queued/new turn with idempotency."""
    clean_text = str(text or "").strip()
    if not clean_text:
        raise ValueError("text must be a non-blank string")

    if delivery is not None and str(delivery).strip():
        effective_delivery = str(delivery).strip().lower()
        if effective_delivery not in _ALLOWED_DELIVERY_MODES:
            raise ValueError("delivery must be 'now' or 'queue'.")
    else:
        effective_delivery = resolve_friend_session_delivery(settings, workspace_root=workspace_root)

    if idempotency_key is not None and str(idempotency_key).strip():
        key = str(idempotency_key).strip()
        is_idempotent = True if idempotent is None else bool(idempotent)
    else:
        key, is_idempotent = build_delivery_idempotency_key(
            tool_call_id,
            origin_conversation_id=origin_conversation_id,
            origin_turn_id=origin_turn_id,
        )

    live_turn, live_state = _find_live_turn(chat_store, conversation_id, user_id, conv)
    live_turn_id = str((live_turn or {}).get("turn_id") or "") or None

    # Idempotency check BEFORE deciding between queue vs turn creation:
    # repeating the exact same tool_call_id must never create a duplicate queue row or turn.
    if key:
        lookup_queue = getattr(chat_store, "queued_input_for_key", None)
        lookup_turn = getattr(chat_store, "turn_for_idempotency_key", None)
        earlier_t: dict[str, Any] | None = None
        if callable(lookup_turn):
            try:
                earlier_t = lookup_turn(user_id, key)
            except Exception:  # noqa: BLE001
                earlier_t = None

        if callable(lookup_queue):
            try:
                earlier_q = lookup_queue(conversation_id, user_id, key)
            except Exception:  # noqa: BLE001
                earlier_q = None
            if earlier_q is not None:
                earlier_q_state = str(earlier_q.get("state") or "pending")
                if earlier_q_state in {"promoted", "cancelled"} and earlier_t is not None:
                    pass
                else:
                    if str(earlier_q.get("content") or "").strip() != clean_text:
                        raise ApplicationValidationError(
                            "the Idempotency-Key was already used for a different message"
                        )
                    return DeliveryResult(
                        delivery="pending_in_turn",
                        queue_id=str(earlier_q["queue_id"]),
                        turn_id=str(earlier_q.get("turn_id") or live_turn_id or "") or None,
                        delivery_fallback=None,
                        idempotent=is_idempotent,
                        state=earlier_q_state,
                    )

        if earlier_t is not None:
            earlier_cid = str(earlier_t.get("conversation_id") or "")
            earlier_tid = str(earlier_t.get("turn_id") or "")
            earlier_content = str(earlier_t.get("content") or "").strip()
            if earlier_cid and earlier_cid != conversation_id:
                raise ApplicationValidationError(
                    "the Idempotency-Key was already used for a different conversation"
                )
            if earlier_content and earlier_content not in {clean_text, _BLOCKED_PROMPT_PLACEHOLDER}:
                raise ApplicationValidationError(
                    "the Idempotency-Key was already used for a different message"
                )
            earlier_state = str(earlier_t.get("state") or "queued")
            has_other_live = bool(live_turn_id and live_turn_id != earlier_tid)
            return DeliveryResult(
                delivery="queued_next_turn" if has_other_live else "new_turn",
                queue_id=None,
                turn_id=earlier_tid or None,
                delivery_fallback=None,
                idempotent=is_idempotent,
                state=earlier_state,
            )

    has_injectable_turn = live_state in _LIVE_INJECTABLE_STATES
    is_cancelling = live_state == "cancelling"

    if requested_model_change is not None:
        model_change = bool(requested_model_change)
    else:
        model_change = _is_model_or_permission_change(
            requested_provider=requested_provider,
            requested_model_id=requested_model_id,
            requested_permission_mode=requested_permission_mode,
            effective_provider=provider,
            effective_model_id=model_id,
            effective_permission_mode=permission_mode,
            live_turn=live_turn,
            conv=conv,
        )

    if effective_delivery == "now":
        if has_injectable_turn:
            if model_change:
                created = _create_turn(
                    chat_store=chat_store,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    text=clean_text,
                    provider=provider,
                    model_id=model_id,
                    idempotency_key=key,
                    project_id=project_id,
                    code_mode=code_mode,
                    permission_mode=permission_mode,
                )
                return DeliveryResult(
                    delivery="queued_next_turn",
                    queue_id=None,
                    turn_id=created.turn_id,
                    delivery_fallback="model_change",
                    idempotent=is_idempotent,
                    state=created.state,
                )

            enqueue_fn = getattr(chat_store, "enqueue_input", None)
            if callable(enqueue_fn):
                try:
                    try:
                        queued_item = enqueue_fn(
                            user_id=user_id,
                            conversation_id=conversation_id,
                            content=clean_text,
                            idempotency_key=key,
                            origin=origin,
                            origin_conversation_id=origin_conversation_id,
                        )
                    except TypeError:
                        queued_item = enqueue_fn(
                            user_id=user_id,
                            conversation_id=conversation_id,
                            content=clean_text,
                            idempotency_key=key,
                        )
                except ApplicationValidationError as error:
                    if "queue is full" in str(error).lower():
                        created = _create_turn(
                            chat_store=chat_store,
                            user_id=user_id,
                            conversation_id=conversation_id,
                            text=clean_text,
                            provider=provider,
                            model_id=model_id,
                            idempotency_key=key,
                            project_id=project_id,
                            code_mode=code_mode,
                            permission_mode=permission_mode,
                        )
                        return DeliveryResult(
                            delivery="queued_next_turn",
                            queue_id=None,
                            turn_id=created.turn_id,
                            delivery_fallback="queue_full",
                            idempotent=is_idempotent,
                            state=created.state,
                        )
                    raise
                # TOCTOU race guard: if the live turn finished while enqueue_input ran,
                # nothing will drain the queue row; promote immediately into a follow-up turn.
                post_conv = None if getattr(chat_store, "_engine", None) is not None else conv
                _post_turn, post_state = _find_live_turn(chat_store, conversation_id, user_id, post_conv)
                if post_state not in _LIVE_INJECTABLE_STATES:
                    take_fn = getattr(chat_store, "take_queued_inputs", None)
                    if callable(take_fn):
                        taken = take_fn(
                            {
                                "conversation_id": conversation_id,
                                "user_id": user_id,
                                "turn_id": live_turn_id,
                            }
                        )
                        if taken is not None:
                            promoted_text, promoted_ids = taken
                            try:
                                created = _create_turn(
                                    chat_store=chat_store,
                                    user_id=user_id,
                                    conversation_id=conversation_id,
                                    text=promoted_text,
                                    provider=provider,
                                    model_id=model_id,
                                    idempotency_key=key,
                                    project_id=project_id,
                                    code_mode=code_mode,
                                    permission_mode=permission_mode,
                                )
                            except Exception:
                                restore_fn = getattr(chat_store, "restore_queued_inputs", None)
                                if callable(restore_fn):
                                    try:
                                        restore_fn(promoted_ids)
                                    except Exception:  # noqa: BLE001
                                        pass
                                raise
                            return DeliveryResult(
                                delivery="queued_next_turn" if post_state == "cancelling" else "new_turn",
                                queue_id=None,
                                turn_id=created.turn_id,
                                delivery_fallback="turn_cancelling" if post_state == "cancelling" else None,
                                idempotent=is_idempotent,
                                state=created.state,
                            )
                return DeliveryResult(
                    delivery="pending_in_turn",
                    queue_id=str(queued_item["queue_id"]),
                    turn_id=live_turn_id,
                    delivery_fallback=None,
                    idempotent=is_idempotent,
                    state=str(queued_item.get("state") or "pending"),
                )

        if is_cancelling:
            created = _create_turn(
                chat_store=chat_store,
                user_id=user_id,
                conversation_id=conversation_id,
                text=clean_text,
                provider=provider,
                model_id=model_id,
                idempotency_key=key,
                project_id=project_id,
                code_mode=code_mode,
                permission_mode=permission_mode,
            )
            return DeliveryResult(
                delivery="queued_next_turn",
                queue_id=None,
                turn_id=created.turn_id,
                delivery_fallback="turn_cancelling",
                idempotent=is_idempotent,
                state=created.state,
            )

        created = _create_turn(
            chat_store=chat_store,
            user_id=user_id,
            conversation_id=conversation_id,
            text=clean_text,
            provider=provider,
            model_id=model_id,
            idempotency_key=key,
            project_id=project_id,
            code_mode=code_mode,
            permission_mode=permission_mode,
        )
        return DeliveryResult(
            delivery="new_turn",
            queue_id=None,
            turn_id=created.turn_id,
            delivery_fallback=None,
            idempotent=is_idempotent,
            state=created.state,
        )

    # effective_delivery == "queue"
    created = _create_turn(
        chat_store=chat_store,
        user_id=user_id,
        conversation_id=conversation_id,
        text=clean_text,
        provider=provider,
        model_id=model_id,
        idempotency_key=key,
        project_id=project_id,
        code_mode=code_mode,
        permission_mode=permission_mode,
    )
    return DeliveryResult(
        delivery="queued_next_turn" if (has_injectable_turn or is_cancelling) else "new_turn",
        queue_id=None,
        turn_id=created.turn_id,
        delivery_fallback=None,
        idempotent=is_idempotent,
        state=created.state,
    )


__all__ = [
    "DeliveryKind",
    "DeliveryResult",
    "FRIEND_MESSAGE_PREFIX",
    "build_delivery_idempotency_key",
    "deliver_to_session",
    "format_friend_injected_text",
    "resolve_friend_session_delivery",
]

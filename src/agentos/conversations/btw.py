"""Ephemeral `/btw` parallel side-question service.

Reads a conversation's durable state (including compaction boundaries, recent tool
trajectory steps and any currently running turn) without mutating any conversation
table, and streams a single tool-free answer using the active model at low effort.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
import json
import logging
import threading
import time
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from agentos.agentic.compaction import (
    COMPACT_RETAINED_USER_TOKENS,
    attach_retained_user_requests,
    compaction_message,
)
from agentos.agentic.effort_levels import EFFORT_RANK, levels_for
from agentos.agentic.provider_stream import (
    NormalizedStreamItem,
    ProviderKeyRejected,
    StreamKind,
    _redact_secrets,
)
from agentos.agentic.text_sanitize import new_assistant_text_stream
from agentos.agentic.transcript import (
    STEP_ASSISTANT_TOOL_CALL,
    STEP_QUEUED_INPUT,
    STEP_TOOL_RESULT,
)
from agentos.agentic.transport_factory import (
    build_provider_transport,
    resolve_ollama_num_ctx,
)
from agentos.conversations.chat import BLOCKED_BY_HOOK_STATUS, COMMAND_STATUS
from agentos.redaction import redact_text
from agentos.persistence.postgres.schema import (
    conversation_compact_boundaries,
    conversation_message_attachments,
    conversation_message_commands,
    conversation_messages,
    conversation_turn_steps,
    conversation_turns,
    conversations,
    provider_model_catalog,
)

_LOGGER = logging.getLogger("agentos.conversations.btw")

BTW_MAX_QUESTION_CHARS = 8_000
BTW_DEFAULT_CONTEXT_CHARS = 96_000
BTW_MIN_CONTEXT_CHARS = 12_000
BTW_MIN_OLLAMA_CONTEXT_CHARS = 3_000
BTW_MAX_STEP_PREVIEW_CHARS = 1_200
BTW_RECENT_STEP_PREVIEW_CHARS = 3_200
BTW_MAX_STEPS_PER_TURN = 16
BTW_MAX_RECENT_MESSAGES = 200
BTW_MAX_RETAINED_USER_MESSAGES = 40
BTW_MAX_RECENT_TURNS = 100
BTW_MAX_STEP_TURNS = 12
BTW_MAX_TOTAL_STEPS = 256
BTW_IDLE_TIMEOUT_SECONDS = 60.0
BTW_WALL_TIMEOUT_SECONDS = 180.0
BTW_SLOT_WAIT_TIMEOUT_SECONDS = 20.0
BTW_HEARTBEAT_SECONDS = 15.0
BTW_BASE_MAX_OUTPUT_TOKENS = 2_048
BTW_THINKING_MAX_OUTPUT_TOKENS = 4_096
BTW_MAX_CONCURRENT_PER_CONVERSATION = 2
BTW_MAX_CONCURRENT_PER_USER = 4

ACTIVE_TURN_STATES: frozenset[str] = frozenset({"queued", "starting", "running", "streaming"})

_STREAM_END = object()


class BtwConcurrencyGate:
    """Bounds concurrent `/btw` executions per `(user_id, conversation_id)` and per user."""

    def __init__(
        self,
        *,
        max_per_conversation: int = BTW_MAX_CONCURRENT_PER_CONVERSATION,
        max_per_user: int = BTW_MAX_CONCURRENT_PER_USER,
    ) -> None:
        self._max_per_conversation = max(1, int(max_per_conversation))
        self._max_per_user = max(1, int(max_per_user))
        self._lock = threading.Lock()
        self._per_conv: dict[tuple[str, str], int] = {}
        self._per_user: dict[str, int] = {}

    def try_acquire(self, user_id: str, conversation_id: str) -> bool:
        key = (str(user_id), str(conversation_id))
        uid = str(user_id)
        with self._lock:
            if self._per_conv.get(key, 0) >= self._max_per_conversation:
                return False
            if self._per_user.get(uid, 0) >= self._max_per_user:
                return False
            self._per_conv[key] = self._per_conv.get(key, 0) + 1
            self._per_user[uid] = self._per_user.get(uid, 0) + 1
            return True

    def release(self, user_id: str, conversation_id: str) -> None:
        key = (str(user_id), str(conversation_id))
        uid = str(user_id)
        with self._lock:
            conv_count = self._per_conv.get(key, 0) - 1
            if conv_count <= 0:
                self._per_conv.pop(key, None)
            else:
                self._per_conv[key] = conv_count
            user_count = self._per_user.get(uid, 0) - 1
            if user_count <= 0:
                self._per_user.pop(uid, None)
            else:
                self._per_user[uid] = user_count


_BTW_GATE_LOCK = threading.Lock()
_BTW_CONCURRENCY_GATE: BtwConcurrencyGate | None = None


def btw_concurrency_gate() -> BtwConcurrencyGate:
    global _BTW_CONCURRENCY_GATE
    with _BTW_GATE_LOCK:
        if _BTW_CONCURRENCY_GATE is None:
            _BTW_CONCURRENCY_GATE = BtwConcurrencyGate()
        return _BTW_CONCURRENCY_GATE


def _redact_btw_text(value: str) -> str:
    if not value:
        return ""
    return redact_text(_redact_secrets(value))


def _compute_budget_chars(
    *,
    provider: str,
    context_window: int | None,
    question_chars: int,
) -> int:
    budget_chars = BTW_DEFAULT_CONTEXT_CHARS
    if context_window is not None and context_window > 0:
        budget_chars = min(
            BTW_DEFAULT_CONTEXT_CHARS,
            max(BTW_MIN_CONTEXT_CHARS, int(context_window * 0.55) * 4),
        )
    if provider == "ollama":
        effective_num_ctx = resolve_ollama_num_ctx(context_window)
        question_tokens = max(64, (question_chars + 2) // 3)
        prompt_token_budget = max(
            1_000,
            effective_num_ctx - BTW_THINKING_MAX_OUTPUT_TOKENS - question_tokens - 1_200,
        )
        ollama_char_cap = max(BTW_MIN_OLLAMA_CONTEXT_CHARS, prompt_token_budget * 3)
        budget_chars = min(budget_chars, ollama_char_cap)
    return budget_chars


@dataclass(frozen=True, slots=True)
class BtwContextSnapshot:
    conversation_id: str
    title: str
    mode: str
    provider: str
    model_id: str
    context_window: int | None
    capabilities: tuple[str, ...]
    workspace_path: str | None
    messages: list[dict[str, Any]]
    context_messages_count: int
    active_turn_included: bool
    active_turn_state: str | None = None


@dataclass(frozen=True, slots=True)
class BtwEvent:
    event: str
    data: dict[str, Any] = field(default_factory=dict)


def resolve_btw_effort(
    provider: str,
    model_id: str,
    capabilities: Collection[str] = (),
) -> str | None:
    """Choose the lightest supported reasoning effort for a quick `/btw` answer."""
    info = levels_for(provider, model_id, capabilities)
    if not info.levels:
        return None
    if "low" in info.levels:
        return "low"
    ordered = sorted(
        (lvl for lvl in info.levels if lvl in EFFORT_RANK),
        key=lambda lvl: EFFORT_RANK[lvl],
    )
    return ordered[0] if ordered else None


def _truncate(text: str, limit: int) -> str:
    cleaned = str(text or "")
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: max(0, limit - 24)]}\n...[trecho omitido]"


def _compact_args(raw_args: object, limit: int = 240) -> str:
    text = str(raw_args or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, Mapping):
            parts: list[str] = []
            for key, value in list(parsed.items())[:6]:
                rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
                parts.append(f"{key}={_truncate(str(rendered), 80)}")
            return _truncate(", ".join(parts), limit)
    except (TypeError, ValueError):
        pass
    return _truncate(text, limit)


def _format_turn_steps_note(
    steps: Sequence[Mapping[str, Any]],
    *,
    is_active_turn: bool,
    preview_limit: int,
) -> str:
    """Project recorded tool trajectory steps into tool-free plain text.

    Sending raw `tool_use` / `tool_result` blocks when `tools=[]` triggers HTTP 400
    on Anthropic and fails on half-finished tool calls from an in-flight turn.
    """
    if not steps:
        return ""

    results_by_id: dict[str, Mapping[str, Any]] = {}
    parsed_steps: list[tuple[str, dict[str, Any]]] = []
    for row in steps:
        kind = str(row.get("kind") or "")
        raw_payload = row.get("payload")
        if isinstance(raw_payload, Mapping):
            payload = dict(raw_payload)
        elif isinstance(raw_payload, str):
            try:
                loaded = json.loads(raw_payload)
                payload = dict(loaded) if isinstance(loaded, Mapping) else {}
            except ValueError:
                payload = {}
        else:
            payload = {}
        parsed_steps.append((kind, payload))
        if kind == STEP_TOOL_RESULT:
            call_id = str(payload.get("id") or row.get("tool_call_id") or "").strip()
            if call_id:
                results_by_id[call_id] = payload

    lines: list[str] = []
    used_result_ids: set[str] = set()
    for kind, payload in parsed_steps:
        if kind == STEP_ASSISTANT_TOOL_CALL:
            step_text = str(payload.get("text") or "").strip()
            if step_text:
                lines.append(f"- Nota do agente: {_truncate(step_text, preview_limit)}")
            calls = payload.get("calls")
            if isinstance(calls, Sequence):
                for call in calls:
                    if not isinstance(call, Mapping):
                        continue
                    call_id = str(call.get("id") or "").strip()
                    name = str(call.get("name") or "tool").strip()
                    args_str = _compact_args(call.get("arguments"))
                    signature = f"{name}({args_str})" if args_str else f"{name}()"
                    result = results_by_id.get(call_id) if call_id else None
                    if result is not None:
                        used_result_ids.add(call_id)
                        status = str(result.get("status") or "ok")
                        content = _truncate(str(result.get("content") or "").strip(), preview_limit)
                        summary = f" -> {status}: {content}" if content else f" -> {status}"
                        lines.append(f"- Ferramenta {signature}{summary}")
                    elif is_active_turn:
                        lines.append(f"- Ferramenta {signature} -> [em execução]")
                    else:
                        lines.append(f"- Ferramenta {signature}")
        elif kind == STEP_TOOL_RESULT:
            call_id = str(payload.get("id") or "").strip()
            if call_id and call_id in used_result_ids:
                continue
            name = str(payload.get("name") or "tool").strip()
            status = str(payload.get("status") or "ok")
            content = _truncate(str(payload.get("content") or "").strip(), preview_limit)
            lines.append(f"- Resultado de {name} ({status}): {content}" if content else f"- Resultado de {name} ({status})")
        elif kind == STEP_QUEUED_INPUT:
            queued_text = str(payload.get("content") or payload.get("text") or "").strip()
            if queued_text:
                lines.append(f"- Mensagem enfileirada pelo usuário: {_truncate(queued_text, 400)}")

    if not lines:
        return ""
    bounded = lines[-BTW_MAX_STEPS_PER_TURN:]
    omitted = len(lines) - len(bounded)
    header = (
        "[Atividade do turno atual até este momento"
        if is_active_turn
        else "[Atividade de ferramentas neste turno"
    )
    if omitted > 0:
        header += f" ({omitted} passos anteriores omitidos)"
    header += "]"
    return f"{header}\n" + "\n".join(bounded)


def _build_btw_system_prompt(
    *,
    mode: str,
    title: str,
    workspace_path: str | None,
    active_turn_state: str | None,
) -> str:
    mode_label = "Orin Code" if mode == "code" else ("Friend" if mode == "friend" else "Chat")
    lines = [
        "Você está respondendo uma pergunta paralela efêmera (`/btw`) sobre a conversa fornecida.",
        "Esta resposta NÃO entra no histórico da conversa principal e NÃO interrompe o turno principal.",
        "Regras importantes:",
        "- Você NÃO tem ferramentas disponíveis nesta chamada: não pode ler novos arquivos, buscar na web, rodar comandos nem editar código.",
        "- Responda de forma direta, concisa e factual usando apenas o contexto da conversa acima e seu conhecimento geral.",
        "- Se a pergunta exigir inspecionar arquivos, rodar comandos ou consultar algo que não está no contexto, diga isso claramente em 1 frase e sugira perguntar na conversa principal ou usar /fork.",
        "- Responda no mesmo idioma da pergunta do usuário.",
        f"- Sessão atual: modo {mode_label}" + (f" · título: {title}" if title else ""),
    ]
    if workspace_path:
        lines.append(f"- Pasta de trabalho da sessão: {workspace_path}")
    if active_turn_state:
        lines.append(
            f"- Há um turno principal em andamento agora (estado: {active_turn_state}); o contexto inclui os passos já registrados até este instante."
        )
    return "\n".join(lines)


def _normalize_alternation(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Ensure valid user/assistant alternation (required by Anthropic and strict chat APIs)."""
    normalized: list[dict[str, str]] = []
    for item in messages:
        role = str(item.get("role") or "user")
        if role not in ("user", "assistant"):
            role = "user"
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        if normalized and normalized[-1]["role"] == role:
            normalized[-1]["content"] = f"{normalized[-1]['content']}\n\n{content}"
        else:
            normalized.append({"role": role, "content": content})
    if normalized and normalized[0]["role"] == "assistant":
        normalized.insert(0, {"role": "user", "content": "[Contexto anterior da conversa]"})
    return normalized


def _trim_history_to_budget(
    entries: list[dict[str, str]],
    *,
    budget_chars: int,
    pinned_prefix: dict[str, str] | None = None,
    prior_omitted: int = 0,
) -> list[dict[str, str]]:
    """Keep the most recent messages within `budget_chars`, preserving optional compaction prefix."""
    remaining = max(BTW_MIN_OLLAMA_CONTEXT_CHARS, budget_chars)
    prefix_list: list[dict[str, str]] = []
    if pinned_prefix is not None:
        prefix_cap = min(max(1_000, remaining // 3), remaining)
        prefix_content = _truncate(pinned_prefix["content"], prefix_cap)
        prefix_list.append({"role": "user", "content": prefix_content})
        remaining = max(1_000, remaining - len(prefix_content))

    kept_reversed: list[dict[str, str]] = []
    used = 0
    for item in reversed(entries):
        text = item["content"]
        cost = len(text) + 32
        if kept_reversed and used + cost > remaining:
            break
        if not kept_reversed and cost > remaining:
            kept_reversed.append({"role": item["role"], "content": _truncate(text, max(1_000, remaining - 32))})
            used = remaining
            break
        kept_reversed.append(item)
        used += cost

    kept = list(reversed(kept_reversed))
    omitted = max(0, int(prior_omitted)) + (len(entries) - len(kept))
    result: list[dict[str, str]] = list(prefix_list)
    if omitted > 0:
        result.append(
            {
                "role": "user",
                "content": f"[{omitted} mensagem(ns) mais antiga(s) omitida(s) para manter o /btw dentro do limite de contexto]",
            }
        )
    result.extend(kept)
    return result


def build_btw_context(
    engine: Engine,
    *,
    user_id: str,
    conversation_id: str,
    question: str,
    provider: str | None = None,
    model_id: str | None = None,
    workspace_path: str | None = None,
) -> BtwContextSnapshot:
    """Build a read-only prompt snapshot for `/btw` from the conversation's durable state."""
    clean_question = str(question or "").strip()
    if not clean_question:
        raise ValueError("question must not be blank")
    if len(clean_question) > BTW_MAX_QUESTION_CHARS:
        raise ValueError(f"question exceeds {BTW_MAX_QUESTION_CHARS} characters")

    with engine.connect() as conn:
        conv = conn.execute(
            select(
                conversations.c.conversation_id,
                conversations.c.user_id,
                conversations.c.title,
                conversations.c.provider,
                conversations.c.model_id,
                conversations.c.mode,
                conversations.c.state,
            ).where(
                conversations.c.conversation_id == conversation_id,
                conversations.c.user_id == user_id,
            )
        ).mappings().first()
        if conv is None:
            raise LookupError("conversation not found")

        boundary = conn.execute(
            select(
                conversation_compact_boundaries.c.summary,
                conversation_compact_boundaries.c.through_sequence,
            )
            .where(
                conversation_compact_boundaries.c.conversation_id == conversation_id,
                conversation_compact_boundaries.c.kind == "compact",
                conversation_compact_boundaries.c.through_sequence.is_not(None),
                conversation_compact_boundaries.c.summary.is_not(None),
            )
            .order_by(conversation_compact_boundaries.c.id.desc())
            .limit(1)
        ).mappings().first()

        max_seq_val = conn.execute(
            select(func.max(conversation_messages.c.sequence)).where(
                conversation_messages.c.conversation_id == conversation_id,
            )
        ).scalar()
        max_seq = int(max_seq_val or 0)
        through = int(boundary["through_sequence"]) if boundary is not None else None
        compacted = (
            through is not None
            and through <= max_seq
            and bool(str(boundary["summary"] or "").strip())
        )

        msg_stmt = select(
            conversation_messages.c.message_id,
            conversation_messages.c.role,
            conversation_messages.c.content,
            conversation_messages.c.sequence,
            conversation_messages.c.status,
        ).where(conversation_messages.c.conversation_id == conversation_id)
        if compacted and through is not None:
            msg_stmt = msg_stmt.where(conversation_messages.c.sequence > through)
        msg_rows = list(
            reversed(
                conn.execute(
                    msg_stmt.order_by(conversation_messages.c.sequence.desc()).limit(
                        BTW_MAX_RECENT_MESSAGES
                    )
                )
                .mappings()
                .all()
            )
        )

        compacted_user_messages: list[str] = []
        if compacted and through is not None:
            compacted_user_rows = list(
                reversed(
                    conn.execute(
                        select(
                            conversation_messages.c.message_id,
                            conversation_messages.c.content,
                            conversation_messages.c.status,
                        )
                        .where(
                            conversation_messages.c.conversation_id == conversation_id,
                            conversation_messages.c.sequence <= through,
                            conversation_messages.c.role == "user",
                        )
                        .order_by(conversation_messages.c.sequence.desc())
                        .limit(BTW_MAX_RETAINED_USER_MESSAGES)
                    )
                    .mappings()
                    .all()
                )
            )
            compacted_user_ids = [str(r["message_id"]) for r in compacted_user_rows]
            compacted_cmd_ids: set[str] = set()
            if compacted_user_ids:
                compacted_cmd_ids = {
                    str(r["message_id"])
                    for r in conn.execute(
                        select(conversation_message_commands.c.message_id).where(
                            conversation_message_commands.c.conversation_id == conversation_id,
                            conversation_message_commands.c.message_id.in_(compacted_user_ids),
                        )
                    )
                    .mappings()
                    .all()
                }
            compacted_user_messages = [
                str(r["content"])
                for r in compacted_user_rows
                if str(r.get("status") or "") not in (BLOCKED_BY_HOOK_STATUS, COMMAND_STATUS)
                and str(r["message_id"]) not in compacted_cmd_ids
                and str(r.get("content") or "").strip()
            ]

        turn_rows = list(
            reversed(
                conn.execute(
                    select(
                        conversation_turns.c.turn_id,
                        conversation_turns.c.user_message_id,
                        conversation_turns.c.assistant_message_id,
                        conversation_turns.c.provider,
                        conversation_turns.c.model_id,
                        conversation_turns.c.state,
                    )
                    .where(conversation_turns.c.conversation_id == conversation_id)
                    .order_by(conversation_turns.c.id.desc())
                    .limit(BTW_MAX_RECENT_TURNS)
                )
                .mappings()
                .all()
            )
        )

        msg_ids = [str(row["message_id"]) for row in msg_rows]
        command_rows: list[Mapping[str, Any]] = []
        attachment_rows: list[Mapping[str, Any]] = []
        if msg_ids:
            command_rows = list(
                conn.execute(
                    select(
                        conversation_message_commands.c.message_id,
                        conversation_message_commands.c.expanded_body,
                    ).where(
                        conversation_message_commands.c.conversation_id == conversation_id,
                        conversation_message_commands.c.message_id.in_(msg_ids),
                    )
                )
                .mappings()
                .all()
            )
            attachment_rows = list(
                conn.execute(
                    select(
                        conversation_message_attachments.c.message_id,
                        conversation_message_attachments.c.original_name,
                        conversation_message_attachments.c.media_type,
                    )
                    .where(
                        conversation_message_attachments.c.conversation_id == conversation_id,
                        conversation_message_attachments.c.message_id.in_(msg_ids),
                    )
                    .order_by(conversation_message_attachments.c.id.asc())
                )
                .mappings()
                .all()
            )

        latest_turn = turn_rows[-1] if turn_rows else None
        resolved_provider = (
            str(provider or "").strip().lower()
            or (str(latest_turn["provider"]).strip().lower() if latest_turn and latest_turn.get("provider") else "")
            or str(conv["provider"] or "").strip().lower()
        )
        resolved_model_id = (
            str(model_id or "").strip()
            or (str(latest_turn["model_id"]).strip() if latest_turn and latest_turn.get("model_id") else "")
            or str(conv["model_id"] or "").strip()
        )

        catalog_row = None
        if resolved_provider and resolved_model_id:
            catalog_row = conn.execute(
                select(
                    provider_model_catalog.c.context_window,
                    provider_model_catalog.c.capabilities,
                ).where(
                    provider_model_catalog.c.user_id == user_id,
                    provider_model_catalog.c.provider == resolved_provider,
                    provider_model_catalog.c.model_id == resolved_model_id,
                )
            ).mappings().first()

        context_window: int | None = None
        capabilities: tuple[str, ...] = ()
        if catalog_row is not None:
            raw_ctx = catalog_row.get("context_window")
            if isinstance(raw_ctx, int) and raw_ctx > 0:
                context_window = raw_ctx
            raw_caps = catalog_row.get("capabilities")
            if isinstance(raw_caps, str) and raw_caps.strip():
                try:
                    decoded = json.loads(raw_caps)
                    if isinstance(decoded, list):
                        capabilities = tuple(str(item) for item in decoded if item)
                except ValueError:
                    pass
            elif isinstance(raw_caps, Sequence) and not isinstance(raw_caps, (str, bytes)):
                capabilities = tuple(str(item) for item in raw_caps if item)

        budget_chars = _compute_budget_chars(
            provider=resolved_provider,
            context_window=context_window,
            question_chars=len(clean_question),
        )

        active_turn = None
        for t in reversed(turn_rows):
            if str(t.get("state") or "").strip().lower() in ACTIVE_TURN_STATES:
                active_turn = t
                break

        blocked_user_ids = {
            str(row["message_id"])
            for row in msg_rows
            if str(row.get("status") or "") in (BLOCKED_BY_HOOK_STATUS, COMMAND_STATUS)
        }
        skipped_ids = set(blocked_user_ids)
        for t in turn_rows:
            if str(t["user_message_id"]) in blocked_user_ids:
                skipped_ids.add(str(t["assistant_message_id"]))

        visible_msgs = [row for row in msg_rows if str(row["message_id"]) not in skipped_ids]
        expansions = {str(row["message_id"]): str(row["expanded_body"]) for row in command_rows}
        attachments_by_msg: dict[str, list[str]] = {}
        for att in attachment_rows:
            mid = str(att["message_id"])
            fname = str(att.get("original_name") or "").strip()
            if fname:
                attachments_by_msg.setdefault(mid, []).append(fname)

        turn_by_assistant_msg: dict[str, Mapping[str, Any]] = {
            str(t["assistant_message_id"]): t for t in turn_rows
        }

        pinned_prefix: dict[str, str] | None = None
        if compacted and through is not None and boundary is not None:
            summary_body = attach_retained_user_requests(
                str(boundary["summary"]),
                compacted_user_messages,
                max_tokens=COMPACT_RETAINED_USER_TOKENS,
            )
            comp_msg = compaction_message(summary_body)
            pinned_prefix = {"role": "user", "content": str(comp_msg["content"])}

        active_turn_id = str(active_turn["turn_id"]) if active_turn is not None else None
        active_assistant_msg_id = str(active_turn["assistant_message_id"]) if active_turn is not None else None
        active_turn_state = str(active_turn["state"]) if active_turn is not None else None

        # Pre-select the tail of messages that can fit within the character budget
        # before querying or parsing JSON from `conversation_turn_steps`.
        selected_reversed: list[Mapping[str, Any]] = []
        pre_used = 0
        pre_limit = max(BTW_MIN_OLLAMA_CONTEXT_CHARS, budget_chars)
        for row in reversed(visible_msgs):
            mid = str(row["message_id"])
            raw = expansions.get(mid, str(row.get("content") or ""))
            cost = len(raw) + 64
            if selected_reversed and pre_used + cost > pre_limit:
                break
            selected_reversed.append(row)
            pre_used += cost
        selected_msgs = list(reversed(selected_reversed))
        prior_omitted = len(visible_msgs) - len(selected_msgs)

        candidate_turn_ids_ordered: list[str] = []
        seen_turn_ids: set[str] = set()
        if active_turn_id:
            candidate_turn_ids_ordered.append(active_turn_id)
            seen_turn_ids.add(active_turn_id)
        for row in reversed(selected_msgs):
            if str(row["role"]) != "assistant":
                continue
            t = turn_by_assistant_msg.get(str(row["message_id"]))
            if t is None:
                continue
            tid = str(t["turn_id"])
            if tid not in seen_turn_ids:
                seen_turn_ids.add(tid)
                candidate_turn_ids_ordered.append(tid)
                if len(candidate_turn_ids_ordered) >= BTW_MAX_STEP_TURNS:
                    break

        step_rows: list[Mapping[str, Any]] = []
        if candidate_turn_ids_ordered:
            step_rows = list(
                reversed(
                    conn.execute(
                        select(
                            conversation_turn_steps.c.turn_id,
                            conversation_turn_steps.c.sequence,
                            conversation_turn_steps.c.kind,
                            conversation_turn_steps.c.tool_name,
                            conversation_turn_steps.c.tool_call_id,
                            conversation_turn_steps.c.payload,
                        )
                        .where(
                            conversation_turn_steps.c.conversation_id == conversation_id,
                            conversation_turn_steps.c.agent_id == "main",
                            conversation_turn_steps.c.turn_id.in_(candidate_turn_ids_ordered),
                        )
                        .order_by(conversation_turn_steps.c.id.desc())
                        .limit(BTW_MAX_TOTAL_STEPS)
                    )
                    .mappings()
                    .all()
                )
            )

    steps_by_turn: dict[str, list[Mapping[str, Any]]] = {}
    for step in step_rows:
        steps_by_turn.setdefault(str(step["turn_id"]), []).append(step)

    recent_turn_ids = {str(t["turn_id"]) for t in turn_rows[-3:]}
    history_entries: list[dict[str, str]] = []
    for row in selected_msgs:
        msg_id = str(row["message_id"])
        role = str(row["role"])
        raw_content = expansions.get(msg_id, str(row.get("content") or ""))
        attached_names = attachments_by_msg.get(msg_id, ())
        if attached_names:
            marker = f"\n[Anexos: {', '.join(attached_names)}]"
            raw_content = f"{raw_content}{marker}" if raw_content.strip() else marker.strip()

        turn_for_msg = turn_by_assistant_msg.get(msg_id) if role == "assistant" else None
        if turn_for_msg is not None:
            tid = str(turn_for_msg["turn_id"])
            is_active = tid == active_turn_id
            preview_limit = (
                BTW_RECENT_STEP_PREVIEW_CHARS
                if (is_active or tid in recent_turn_ids)
                else BTW_MAX_STEP_PREVIEW_CHARS
            )
            step_note = _format_turn_steps_note(
                steps_by_turn.get(tid, ()),
                is_active_turn=is_active,
                preview_limit=preview_limit,
            )
            parts: list[str] = []
            if is_active:
                parts.append(f"[Turno principal ainda em andamento — estado: {active_turn_state}]")
            if step_note:
                parts.append(step_note)
            if raw_content.strip():
                parts.append(raw_content.strip())
            combined = "\n\n".join(parts).strip()
            if combined:
                history_entries.append({"role": "assistant", "content": combined})
        else:
            if raw_content.strip():
                history_entries.append({"role": role, "content": raw_content.strip()})

    # If the active turn had steps but no assistant message row rendered yet, attach them explicitly.
    if active_turn is not None and active_turn_id is not None:
        has_active_assistant_row = any(str(row["message_id"]) == active_assistant_msg_id for row in selected_msgs)
        if not has_active_assistant_row:
            step_note = _format_turn_steps_note(
                steps_by_turn.get(active_turn_id, ()),
                is_active_turn=True,
                preview_limit=BTW_RECENT_STEP_PREVIEW_CHARS,
            )
            body = f"[Turno principal ainda em andamento — estado: {active_turn_state}]"
            if step_note:
                body = f"{body}\n\n{step_note}"
            history_entries.append({"role": "assistant", "content": body})

    trimmed = _trim_history_to_budget(
        history_entries,
        budget_chars=budget_chars,
        pinned_prefix=pinned_prefix,
        prior_omitted=prior_omitted,
    )
    context_count = len(trimmed)

    btw_user_prompt = (
        "[Pergunta paralela /btw — responda diretamente a esta pergunta usando apenas o contexto acima, "
        "sem ferramentas e sem alterar a conversa principal:]\n"
        f"{clean_question}"
    )
    trimmed.append({"role": "user", "content": btw_user_prompt})

    conversation_messages_payload = _normalize_alternation(trimmed)
    system_prompt = _build_btw_system_prompt(
        mode=str(conv.get("mode") or "chat"),
        title=str(conv.get("title") or ""),
        workspace_path=workspace_path,
        active_turn_state=active_turn_state,
    )
    final_messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *conversation_messages_payload,
    ]

    return BtwContextSnapshot(
        conversation_id=conversation_id,
        title=str(conv.get("title") or ""),
        mode=str(conv.get("mode") or "chat"),
        provider=resolved_provider,
        model_id=resolved_model_id,
        context_window=context_window,
        capabilities=capabilities,
        workspace_path=workspace_path,
        messages=final_messages,
        context_messages_count=context_count,
        active_turn_included=active_turn is not None,
        active_turn_state=active_turn_state,
    )


def _friendly_error(exc: Exception) -> tuple[str, str, bool]:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return ("btw_timeout", "A pergunta paralela excedeu o tempo limite.", True)
    if isinstance(exc, ProviderKeyRejected):
        if exc.exhausted:
            return ("provider_auth_or_quota", "A chave ou cota do provedor foi recusada.", False)
        return ("provider_rate_limited", "O provedor pediu para aguardar antes de tentar novamente.", True)
    msg = _redact_btw_text(str(exc or "")).strip()
    lowered = msg.lower()
    if "provider unavailable" in lowered or "credential is missing" in lowered:
        return ("provider_unavailable", "O provedor selecionado não está disponível para esta conta.", False)
    if "not found" in lowered and "cli" in lowered:
        return ("cli_unavailable", "A CLI configurada não foi encontrada nesta máquina.", False)
    return ("btw_failed", "Não foi possível responder à pergunta paralela no momento.", True)


def _close_transport_safely(transport: object) -> None:
    closer = getattr(transport, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:  # noqa: BLE001
            pass


def iter_btw_events(
    snapshot: BtwContextSnapshot,
    *,
    transport: object,
    cancel_event: threading.Event | None = None,
    idle_timeout_seconds: float = BTW_IDLE_TIMEOUT_SECONDS,
    wall_timeout_seconds: float = BTW_WALL_TIMEOUT_SECONDS,
    slot_wait_timeout_seconds: float = BTW_SLOT_WAIT_TIMEOUT_SECONDS,
) -> Iterator[BtwEvent]:
    """Run a single tool-free streaming completion over `snapshot` and yield structured events."""
    btw_id = f"btw_{uuid4().hex[:12]}"
    effort = resolve_btw_effort(snapshot.provider, snapshot.model_id, snapshot.capabilities)
    started = time.monotonic()

    yield BtwEvent(
        "start",
        {
            "btw_id": btw_id,
            "provider": snapshot.provider,
            "model_id": snapshot.model_id,
            "effort": effort,
            "active_turn_included": snapshot.active_turn_included,
            "context_messages": snapshot.context_messages_count,
        },
    )

    def is_cancelled() -> bool:
        return bool(cancel_event is not None and cancel_event.is_set())

    if is_cancelled():
        return

    max_output_tokens = (
        BTW_THINKING_MAX_OUTPUT_TOKENS if effort is not None else BTW_BASE_MAX_OUTPUT_TOKENS
    )
    request_payload: dict[str, Any] = {
        "model": snapshot.model_id,
        "messages": snapshot.messages,
        "tools": [],
        "tool_choice": "none",
        "effort": effort,
        "max_output_tokens": max_output_tokens,
        "conversation_id": f"btw:{snapshot.conversation_id}",
        "idle_timeout_seconds": idle_timeout_seconds,
        "max_wall_clock_seconds": wall_timeout_seconds,
        "slot_wait_timeout_seconds": slot_wait_timeout_seconds,
        "cancel_check": is_cancelled,
    }

    sanitizer = new_assistant_text_stream()
    collected_chunks: list[str] = []
    usage_data: dict[str, int | None] = {
        "input_tokens": None,
        "output_tokens": None,
        "cached_input_tokens": None,
    }

    try:
        stream_fn = getattr(transport, "stream")
        try:
            items: Iterator[NormalizedStreamItem] = stream_fn(request_payload, cancel_check=is_cancelled)
        except TypeError:
            items = stream_fn(request_payload)

        for item in items:
            if is_cancelled():
                return
            if time.monotonic() - started > wall_timeout_seconds:
                yield BtwEvent(
                    "error",
                    {
                        "code": "btw_timeout",
                        "message": "A pergunta paralela excedeu o tempo limite.",
                        "retryable": True,
                    },
                )
                return

            kind = item.kind
            if kind is StreamKind.WAITING:
                yield BtwEvent(
                    "waiting",
                    {
                        "position": item.queue_position,
                        "limit": item.queue_limit,
                        "reason": item.wait_reason,
                    },
                )
            elif kind is StreamKind.THINKING:
                thinking_delta = item.thinking if item.thinking is not None else item.text
                if thinking_delta:
                    yield BtwEvent("thinking", {"delta": thinking_delta})
            elif kind is StreamKind.TEXT and item.text:
                clean = sanitizer.feed(item.text)
                if clean:
                    collected_chunks.append(clean)
                    yield BtwEvent("delta", {"delta": clean})
            elif kind is StreamKind.USAGE and item.usage is not None:
                if item.usage.input_tokens is not None:
                    usage_data["input_tokens"] = int(item.usage.input_tokens)
                if item.usage.output_tokens is not None:
                    usage_data["output_tokens"] = int(item.usage.output_tokens)
                cached = getattr(item.usage, "cached_input_tokens", None)
                if cached is None:
                    cached = getattr(item.usage, "cache_read_input_tokens", None)
                if cached is not None:
                    usage_data["cached_input_tokens"] = int(cached)
            elif kind is StreamKind.ERROR:
                err_msg = (
                    _redact_btw_text(str(getattr(item.error, "message", "") or item.text or "")).strip()
                    or "O provedor retornou um erro durante o /btw."
                )
                err_code = str(getattr(item.error, "code", "") or "provider_error")
                retryability = getattr(item.error, "retryability", None)
                if retryability is not None:
                    retryable = str(retryability).upper() != "NEVER"
                else:
                    retryable = bool(getattr(item.error, "retryable", True))
                yield BtwEvent(
                    "error",
                    {"code": err_code, "message": err_msg[:240], "retryable": retryable},
                )
                return
            elif kind is StreamKind.FINISH:
                break

        if is_cancelled():
            return

        tail = sanitizer.finish()
        if tail:
            collected_chunks.append(tail)
            yield BtwEvent("delta", {"delta": tail})

        duration_ms = int((time.monotonic() - started) * 1000)
        yield BtwEvent(
            "done",
            {
                "btw_id": btw_id,
                "text": "".join(collected_chunks),
                "duration_ms": duration_ms,
                "provider": snapshot.provider,
                "model_id": snapshot.model_id,
                "effort": effort,
                "usage": usage_data,
            },
        )
    except Exception as exc:  # noqa: BLE001
        if is_cancelled():
            return
        _LOGGER.warning("btw stream failed for conversation=%s: %s", snapshot.conversation_id, _redact_btw_text(str(exc)))
        code, message, retryable = _friendly_error(exc)
        yield BtwEvent("error", {"code": code, "message": message, "retryable": retryable})
    finally:
        _close_transport_safely(transport)


async def async_stream_btw_events(
    snapshot: BtwContextSnapshot,
    *,
    transport: object,
    is_disconnected: Callable[[], Any],
    heartbeat_seconds: float = BTW_HEARTBEAT_SECONDS,
    idle_timeout_seconds: float = BTW_IDLE_TIMEOUT_SECONDS,
    wall_timeout_seconds: float = BTW_WALL_TIMEOUT_SECONDS,
    slot_wait_timeout_seconds: float = BTW_SLOT_WAIT_TIMEOUT_SECONDS,
) -> AsyncIterator[BtwEvent]:
    """Bridge synchronous provider streaming into an async SSE event generator with disconnect cancellation."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[object] = asyncio.Queue()
    cancel_event = threading.Event()

    def _put(value: object) -> None:
        if loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(queue.put_nowait, value)
        except RuntimeError:
            pass

    def _producer() -> None:
        try:
            for event in iter_btw_events(
                snapshot,
                transport=transport,
                cancel_event=cancel_event,
                idle_timeout_seconds=idle_timeout_seconds,
                wall_timeout_seconds=wall_timeout_seconds,
                slot_wait_timeout_seconds=slot_wait_timeout_seconds,
            ):
                if cancel_event.is_set():
                    break
                _put(event)
        except Exception as exc:  # noqa: BLE001
            if not cancel_event.is_set():
                _LOGGER.warning("btw producer crashed for conversation=%s: %s", snapshot.conversation_id, _redact_btw_text(str(exc)))
                code, message, retryable = _friendly_error(exc)
                _put(BtwEvent("error", {"code": code, "message": message, "retryable": retryable}))
        finally:
            _put(_STREAM_END)

    worker = threading.Thread(
        target=_producer,
        name=f"orin-btw-{snapshot.conversation_id[:16]}",
        daemon=True,
    )
    worker.start()

    started = time.monotonic()
    last_emit = started
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=0.25)
            except TimeoutError:
                if await is_disconnected():
                    cancel_event.set()
                    _close_transport_safely(transport)
                    return
                now = time.monotonic()
                if now - started >= wall_timeout_seconds:
                    cancel_event.set()
                    _close_transport_safely(transport)
                    yield BtwEvent(
                        "error",
                        {
                            "code": "btw_timeout",
                            "message": "A pergunta paralela excedeu o tempo limite.",
                            "retryable": True,
                        },
                    )
                    return
                if now - last_emit >= heartbeat_seconds:
                    last_emit = now
                    yield BtwEvent("heartbeat", {})
                continue
            if item is _STREAM_END:
                return
            if isinstance(item, BtwEvent):
                last_emit = time.monotonic()
                yield item
    finally:
        cancel_event.set()
        _close_transport_safely(transport)


def resolve_btw_transport(
    engine: Engine,
    *,
    user_id: str,
    snapshot: BtwContextSnapshot,
) -> object:
    """Resolve the streaming transport for a `/btw` snapshot."""
    num_ctx = (
        resolve_ollama_num_ctx(snapshot.context_window)
        if snapshot.provider == "ollama"
        else None
    )
    return build_provider_transport(
        engine,
        user_id=user_id,
        provider=snapshot.provider,
        model_id=snapshot.model_id,
        num_ctx=num_ctx,
        cipher_required=True,
    )


def format_sse_event(event: BtwEvent) -> str:
    payload = json.dumps(event.data, ensure_ascii=False, default=str)
    return f"event: {event.event}\ndata: {payload}\n\n"


__all__ = [
    "ACTIVE_TURN_STATES",
    "BTW_MAX_QUESTION_CHARS",
    "BtwConcurrencyGate",
    "BtwContextSnapshot",
    "BtwEvent",
    "async_stream_btw_events",
    "btw_concurrency_gate",
    "build_btw_context",
    "format_sse_event",
    "iter_btw_events",
    "resolve_btw_effort",
    "resolve_btw_transport",
]

"""Discord Markdown chunking, `<speak>` sanitization, Live Progress Embeds, and Decision Cards."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any

DISCORD_MESSAGE_MAX_CHARS = 2000
DEFAULT_SPLIT_LIMIT = 1950

EMBED_MAX_TITLE_CHARS = 256
EMBED_MAX_DESCRIPTION_CHARS = 4096
EMBED_MAX_FIELD_NAME_CHARS = 256
EMBED_MAX_FIELD_VALUE_CHARS = 1024
EMBED_MAX_FIELDS = 25
EMBED_MAX_FOOTER_CHARS = 2048

BUTTON_MAX_LABEL_CHARS = 80
CUSTOM_ID_MAX_CHARS = 100
SELECT_OPTION_MAX_CHARS = 100
SELECT_MAX_OPTIONS = 25

# Embed colors by state
COLOR_RUNNING = 0x3B82F6       # Blue
COLOR_WAITING_USER = 0xF59E0B  # Amber
COLOR_COMPLETED = 0x10B981     # Green
COLOR_FAILED = 0xEF4444        # Red
COLOR_CANCELLED = 0x6B7280     # Gray

_SPEAK_TAG_RE = re.compile(r"</?speak(?:\s[^>]*)?>", re.IGNORECASE)
_EXCESS_NEWLINES_RE = re.compile(r"\n{3,}")
_FENCE_LINE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})([^\n`~]*)$")

_STATUS_BADGES: dict[str, tuple[str, int]] = {
    "watching": ("🔄 Em execução", COLOR_RUNNING),
    "running": ("🔄 Em execução", COLOR_RUNNING),
    "starting": ("🔄 Iniciando", COLOR_RUNNING),
    "queued": ("⏳ Na fila", COLOR_RUNNING),
    "waiting_user": ("⏸ Aguardando sua decisão", COLOR_WAITING_USER),
    "completed": ("✅ Concluído", COLOR_COMPLETED),
    "succeeded": ("✅ Concluído", COLOR_COMPLETED),
    "failed": ("❌ Falhou", COLOR_FAILED),
    "error": ("❌ Falhou", COLOR_FAILED),
    "cancelled": ("⏹ Cancelado", COLOR_CANCELLED),
    "cancelling": ("⏹ Cancelando", COLOR_CANCELLED),
}


def strip_residual_speak_tags(text: str) -> str:
    """Remove any residual `<speak>` and `</speak>` tags emitted by voice-tuned prompts."""
    if not isinstance(text, str) or not text:
        return ""
    cleaned = _SPEAK_TAG_RE.sub("", text)
    cleaned = _EXCESS_NEWLINES_RE.sub("\n\n", cleaned)
    return cleaned.strip()


strip_Residual_speak_tags = strip_residual_speak_tags


def _truncate_str(value: object, max_chars: int, *, suffix: str = "…") -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    if max_chars <= len(suffix):
        return text[:max_chars]
    return text[: max_chars - len(suffix)].rstrip() + suffix


def _next_fence_state(
    open_fence: tuple[str, str] | None,
    line: str,
) -> tuple[str, str] | None:
    match = _FENCE_LINE_RE.match(line)
    if match is None:
        return open_fence
    marker = match.group(2)
    rest = match.group(3).strip()
    if open_fence is None:
        fence_char = marker[0]
        fence_token = fence_char * max(3, min(len(marker), 4))
        lang = rest.split()[0][:32] if rest else ""
        return (fence_token, lang)
    open_marker, _ = open_fence
    if marker[0] == open_marker[0] and len(marker) >= len(open_marker) and not rest:
        return None
    return open_fence


def split_discord_markdown(text: str, limit: int = DEFAULT_SPLIT_LIMIT) -> list[str]:
    """Split Markdown text into Discord-safe chunks (`<= 2000` chars) preserving code fences.

    Every returned chunk is guaranteed to be `<= min(limit, 2000)` characters even after
    appending a closing code fence (`\\n``` `) and prepending a reopened fence (` ```lang\\n`).
    """
    cleaned = strip_residual_speak_tags(text)
    if not cleaned:
        return []

    effective_limit = min(max(int(limit), 120), DISCORD_MESSAGE_MAX_CHARS)
    if len(cleaned) <= effective_limit:
        return [cleaned]

    lines = cleaned.split("\n")
    chunks: list[str] = []
    current_lines: list[str] = []
    current_len = 0
    open_fence: tuple[str, str] | None = None

    def _reopen_header(fence: tuple[str, str] | None) -> str:
        if fence is None:
            return ""
        marker, lang = fence
        return f"{marker}{lang}"

    def _close_footer(fence: tuple[str, str] | None) -> str:
        if fence is None:
            return ""
        marker, _ = fence
        return f"\n{marker}"

    def _flush_current(fence_at_end: tuple[str, str] | None) -> None:
        nonlocal current_lines, current_len
        if not current_lines:
            return
        body = "\n".join(current_lines)
        footer = _close_footer(fence_at_end)
        chunk = body + footer
        if chunk.strip():
            chunks.append(chunk)
        current_lines = []
        current_len = 0
        if fence_at_end is not None:
            header = _reopen_header(fence_at_end)
            current_lines = [header]
            current_len = len(header)

    for line in lines:
        next_fence = _next_fence_state(open_fence, line)
        footer_if_added = _close_footer(next_fence)
        sep_len = 1 if current_lines else 0
        projected_len = current_len + sep_len + len(line) + len(footer_if_added)

        if projected_len <= effective_limit:
            current_lines.append(line)
            current_len += sep_len + len(line)
            open_fence = next_fence
            continue

        # Flushing existing lines first if we have content beyond just an auto-reopened fence header
        has_real_content = bool(current_lines) and not (
            open_fence is not None
            and len(current_lines) == 1
            and current_lines[0] == _reopen_header(open_fence)
        )
        if has_real_content:
            _flush_current(open_fence)
            sep_len = 1 if current_lines else 0
            projected_len = current_len + sep_len + len(line) + len(footer_if_added)
            if projected_len <= effective_limit:
                current_lines.append(line)
                current_len += sep_len + len(line)
                open_fence = next_fence
                continue

        # The single `line` itself exceeds the available space; split `line` into pieces
        remaining = line
        while remaining:
            header_prefix_len = (current_len + 1) if current_lines else 0
            active_footer = _close_footer(open_fence)
            max_piece_len = effective_limit - header_prefix_len - len(active_footer)
            if max_piece_len < 20:
                _flush_current(open_fence)
                header_prefix_len = (current_len + 1) if current_lines else 0
                max_piece_len = max(20, effective_limit - header_prefix_len - len(active_footer))

            if len(remaining) <= max_piece_len:
                sep = 1 if current_lines else 0
                current_lines.append(remaining)
                current_len += sep + len(remaining)
                remaining = ""
                break

            split_at = remaining.rfind(" ", 0, max_piece_len + 1)
            if split_at <= max_piece_len // 3:
                split_at = max_piece_len

            piece = remaining[:split_at]
            remaining = remaining[split_at:].lstrip(" ") if split_at < len(remaining) and remaining[split_at] == " " else remaining[split_at:]
            sep = 1 if current_lines else 0
            current_lines.append(piece)
            current_len += sep + len(piece)
            _flush_current(open_fence)

        open_fence = next_fence

    if current_lines:
        has_only_reopen_header = (
            open_fence is not None
            and len(current_lines) == 1
            and current_lines[0] == _reopen_header(open_fence)
        )
        if not has_only_reopen_header:
            body = "\n".join(current_lines)
            footer = _close_footer(open_fence)
            final_chunk = body + footer
            if final_chunk.strip():
                chunks.append(final_chunk)

    return chunks


def _format_todos_field(todos: Sequence[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for item in todos[:15]:
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "pending").strip().lower()
        if status == "completed":
            icon = "✅"
        elif status == "in_progress":
            icon = "🔄"
        else:
            icon = "⬜"
        label = str(
            (item.get("activeForm") if status == "in_progress" and item.get("activeForm") else item.get("content"))
            or item.get("title")
            or "Tarefa"
        ).strip()
        lines.append(f"{icon} {_truncate_str(label, 90)}")
    if len(todos) > 15:
        lines.append(f"… e mais {len(todos) - 15} item(ns)")
    return _truncate_str("\n".join(lines), EMBED_MAX_FIELD_VALUE_CHARS)


def _format_files_field(files: Sequence[str]) -> str:
    clean_files = [str(f).strip() for f in files if str(f).strip()]
    if not clean_files:
        return ""
    shown = [f"`{_truncate_str(f, 80)}`" for f in clean_files[:12]]
    if len(clean_files) > 12:
        shown.append(f"… (+{len(clean_files) - 12} arquivo(s))")
    return _truncate_str("\n".join(shown), EMBED_MAX_FIELD_VALUE_CHARS)


def compute_progress_digest_hash(snapshot: Mapping[str, Any]) -> str:
    """Compute a deterministic SHA-256 digest of the visible progress fields for debounce/dedup."""
    embed = build_live_progress_embed(snapshot)
    canonical = json.dumps(embed, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_live_progress_embed(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Build a Discord Embed representing the Live Progress Card of a watched session."""
    raw_state = str(snapshot.get("state") or snapshot.get("status") or "running").strip().lower()
    badge_text, color = _STATUS_BADGES.get(raw_state, ("🔄 Em execução", COLOR_RUNNING))

    raw_mode = str(snapshot.get("mode") or "code").strip().lower()
    mode_label = "Orin Code" if raw_mode == "code" else "Orin Chat"
    raw_title = str(snapshot.get("title") or snapshot.get("session_title") or "Sessão").strip() or "Sessão"
    title = _truncate_str(f"{badge_text} · {raw_title}", EMBED_MAX_TITLE_CHARS)

    # Determine current step / description
    current_step = str(snapshot.get("current_step") or snapshot.get("step_summary") or "").strip()
    if not current_step:
        recent = snapshot.get("recent_activities")
        if isinstance(recent, Sequence) and not isinstance(recent, (str, bytes)) and recent:
            last_act = recent[-1]
            if isinstance(last_act, Mapping):
                current_step = str(last_act.get("summary") or last_act.get("event_type") or "").strip()

    pending_block = snapshot.get("pending_block")
    if raw_state == "waiting_user" and isinstance(pending_block, Mapping):
        block_kind = str(pending_block.get("kind") or "")
        if block_kind == "tool_permission":
            req = pending_block.get("request") if isinstance(pending_block.get("request"), Mapping) else {}
            tool_n = str(req.get("tool_name") or "ferramenta")
            current_step = f"Aguardando permissão para executar `{tool_n}`."
        elif block_kind in {"plan_approval", "plan_decision"}:
            current_step = "Plano pronto aguardando sua aprovação."
        elif block_kind == "ask_user":
            current_step = "A sessão fez uma pergunta e aguarda sua resposta."

    error_text = str(snapshot.get("error") or snapshot.get("last_error") or "").strip()
    if raw_state in {"failed", "error"} and error_text:
        description = _truncate_str(f"**Erro:** {error_text}", EMBED_MAX_DESCRIPTION_CHARS)
    elif current_step:
        description = _truncate_str(f"**Etapa atual:** {current_step}", EMBED_MAX_DESCRIPTION_CHARS)
    else:
        description = _truncate_str(f"Acompanhando sessão `{mode_label}`.", EMBED_MAX_DESCRIPTION_CHARS)

    fields: list[dict[str, Any]] = []

    project_label = str(
        snapshot.get("project_name")
        or snapshot.get("project_id")
        or snapshot.get("workspace_path")
        or "Sem projeto vinculado"
    ).strip()
    fields.append({
        "name": "Projeto / Modo",
        "value": _truncate_str(f"**{project_label}** (`{mode_label}`)", EMBED_MAX_FIELD_VALUE_CHARS),
        "inline": True,
    })

    provider = str(snapshot.get("provider") or "").strip()
    model_id = str(snapshot.get("model_id") or "").strip()
    perm_mode = str(snapshot.get("public_permission_mode") or snapshot.get("permission_mode") or "").strip()
    model_parts = [p for p in (provider, model_id) if p]
    model_str = " / ".join(model_parts) if model_parts else "Padrão"
    if perm_mode:
        model_str = f"{model_str} · `{perm_mode}`"
    fields.append({
        "name": "Modelo",
        "value": _truncate_str(model_str, EMBED_MAX_FIELD_VALUE_CHARS),
        "inline": True,
    })

    raw_todos = snapshot.get("todos")
    if isinstance(raw_todos, Sequence) and not isinstance(raw_todos, (str, bytes)) and raw_todos:
        todos_val = _format_todos_field([t for t in raw_todos if isinstance(t, Mapping)])
        if todos_val:
            done_cnt = sum(1 for t in raw_todos if isinstance(t, Mapping) and str(t.get("status") or "") == "completed")
            fields.append({
                "name": _truncate_str(f"Checklist ({done_cnt}/{len(raw_todos)})", EMBED_MAX_FIELD_NAME_CHARS),
                "value": todos_val,
                "inline": False,
            })

    raw_files = snapshot.get("files_modified")
    if isinstance(raw_files, Sequence) and not isinstance(raw_files, (str, bytes)) and raw_files:
        files_val = _format_files_field([str(f) for f in raw_files])
        if files_val:
            fields.append({
                "name": _truncate_str(f"Arquivos Alterados ({len(raw_files)})", EMBED_MAX_FIELD_NAME_CHARS),
                "value": files_val,
                "inline": False,
            })

    conv_id = str(snapshot.get("conversation_id") or snapshot.get("target_conversation_id") or "").strip()
    embed: dict[str, Any] = {
        "title": title,
        "description": description,
        "color": color,
        "fields": fields[:EMBED_MAX_FIELDS],
    }
    if conv_id:
        embed["footer"] = {"text": _truncate_str(f"Sessão: {conv_id}", EMBED_MAX_FOOTER_CHARS)}
    return embed


def sanitize_custom_id(
    binding_id: str,
    *,
    prefix: str = "orin:v1",
) -> str:
    """Build a Discord component `custom_id` (<= 100 chars) without control characters or extra colons in `prefix`."""
    raw_id = re.sub(r"[\x00-\x1f\x7f]+", "", str(binding_id or "")).strip()
    if not raw_id:
        raise ValueError("binding_id must be a non-empty string")
    if raw_id.startswith("orin:v1:"):
        raw_id = raw_id[len("orin:v1:") :].strip()
    elif raw_id.startswith("orin:"):
        parts = raw_id.split(":", 2)
        raw_id = parts[-1].strip()
    raw_id = raw_id.replace(":", "_")
    if not raw_id:
        raise ValueError("binding_id must contain valid characters")

    clean_prefix = re.sub(r"[\x00-\x1f\x7f]+", "", str(prefix or "orin:v1")).strip().strip(":")
    if clean_prefix == "orin:v1" or not clean_prefix:
        effective_prefix = "orin:v1"
    else:
        # Ensure prefix never injects extra colons that could shift split(":", 2)
        safe_token = re.sub(r"[^a-zA-Z0-9_-]+", "", clean_prefix.replace(":", "_")).strip("_-") or "v1"
        effective_prefix = f"orin:{safe_token}"

    full_id = f"{effective_prefix}:{raw_id}"
    return full_id[:CUSTOM_ID_MAX_CHARS]


def build_progress_components(
    stop_binding_id: str | None,
    *,
    active: bool = True,
) -> list[dict[str, Any]]:
    """Build the ActionRow containing `[Parar sessão]` while the session is actively running."""
    if not active or not stop_binding_id or not str(stop_binding_id).strip():
        return []
    custom_id = sanitize_custom_id(str(stop_binding_id))
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 4,  # Danger (Red)
                    "label": "Parar sessão",
                    "custom_id": custom_id,
                }
            ],
        }
    ]


def _extract_binding_info(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        binding_id = str(item.get("binding_id") or item.get("id") or "").strip()
        action_kind = str(item.get("action_kind") or "").strip()
        payload = dict(item.get("payload") or {}) if isinstance(item.get("payload"), Mapping) else {}
        label = str(item.get("label") or payload.get("label") or "").strip()
        style = item.get("style") or payload.get("style")
        description = str(item.get("description") or payload.get("description") or "").strip()
    else:
        binding_id = str(getattr(item, "binding_id", "") or "").strip()
        action_kind = str(getattr(item, "action_kind", "") or "").strip()
        raw_payload = getattr(item, "payload", None)
        payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
        label = str(payload.get("label") or "").strip()
        style = payload.get("style")
        description = str(payload.get("description") or "").strip()

    custom_id = sanitize_custom_id(binding_id) if binding_id else ""
    return {
        "binding_id": binding_id,
        "custom_id": custom_id,
        "action_kind": action_kind,
        "payload": payload,
        "label": label,
        "style": int(style) if isinstance(style, int) and 1 <= style <= 4 else None,
        "description": description,
    }


def _default_button_label_and_style(info: Mapping[str, Any], index: int) -> tuple[str, int]:
    payload = info.get("payload") if isinstance(info.get("payload"), Mapping) else {}
    explicit_label = str(info.get("label") or payload.get("label") or payload.get("option_label") or "").strip()
    explicit_style = info.get("style")
    decision = str(payload.get("decision") or "").strip().lower()

    if decision == "approve":
        return (explicit_label or "Aprovar", explicit_style or 3)
    if decision == "revise":
        return (explicit_label or "Pedir ajustes", explicit_style or 2)
    if decision == "reject":
        return (explicit_label or "Rejeitar", explicit_style or 4)
    if decision == "allow_once":
        return (explicit_label or "Permitir 1x", explicit_style or 1)
    if decision == "allow_always":
        return (explicit_label or "Sempre permitir", explicit_style or 3)
    if decision == "deny":
        return (explicit_label or "Negar", explicit_style or 4)

    answer = str(payload.get("answer") or payload.get("value") or "").strip()
    label = explicit_label or answer or f"Opção {index + 1}"
    return (_truncate_str(label, BUTTON_MAX_LABEL_CHARS), explicit_style or 1)


def build_decision_card(
    prompt_data: Mapping[str, Any],
    bindings: Sequence[Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build a Discord `(embed, components)` pair for a session waiting on user input/permission.

    - Supports `tool_permission`, `plan_approval` / `plan_decision`, and `ask_user`.
    - Automatically switches from `Button` (`type=2`) to `StringSelect` (`type=3`) when a question
      has between 6 and 25 options.
    """
    kind = str(prompt_data.get("kind") or "ask_user").strip().lower()
    session_title = str(prompt_data.get("title") or prompt_data.get("session_title") or "Sessão").strip()
    conv_id = str(prompt_data.get("conversation_id") or "").strip()

    normalized_bindings = [_extract_binding_info(b) for b in bindings if _extract_binding_info(b)["binding_id"]]
    fields: list[dict[str, Any]] = []

    if kind == "tool_permission":
        req = prompt_data.get("request") if isinstance(prompt_data.get("request"), Mapping) else prompt_data
        tool_name = str(req.get("tool_name") or "ferramenta").strip()
        summary = str(req.get("summary") or req.get("description") or "").strip()
        suggested_rule = str(req.get("suggested_rule") or "").strip()
        args_obj = req.get("arguments")
        if not summary and isinstance(args_obj, Mapping):
            cmd = args_obj.get("command") or args_obj.get("file_path") or args_obj.get("path")
            if cmd:
                summary = f"`{_truncate_str(cmd, 300)}`"

        embed_title = _truncate_str(f"🔐 Permissão de Ferramenta · {session_title}", EMBED_MAX_TITLE_CHARS)
        desc_lines = [f"A sessão solicita permissão para executar **`{tool_name}`**."]
        if summary:
            desc_lines.append(f"\n{summary}")
        description = _truncate_str("\n".join(desc_lines), EMBED_MAX_DESCRIPTION_CHARS)
        if suggested_rule:
            fields.append({
                "name": "Regra sugerida",
                "value": _truncate_str(f"`{suggested_rule}`", EMBED_MAX_FIELD_VALUE_CHARS),
                "inline": False,
            })

    elif kind in {"plan_approval", "plan_decision"}:
        plan_path = str(prompt_data.get("plan_path") or "").strip()
        plan_summary = str(
            prompt_data.get("plan_summary")
            or prompt_data.get("plan_content")
            or prompt_data.get("summary")
            or "O plano técnico foi elaborado e aguarda sua revisão."
        ).strip()
        embed_title = _truncate_str(f"📋 Revisão de Plano · {session_title}", EMBED_MAX_TITLE_CHARS)
        description = _truncate_str(plan_summary, EMBED_MAX_DESCRIPTION_CHARS)
        if plan_path:
            fields.append({
                "name": "Arquivo do Plano",
                "value": _truncate_str(f"`{plan_path}`", EMBED_MAX_FIELD_VALUE_CHARS),
                "inline": False,
            })

    else:
        # ask_user / AskUserQuestion
        questions = prompt_data.get("questions")
        first_q: Mapping[str, Any] = {}
        if isinstance(questions, Sequence) and not isinstance(questions, (str, bytes)) and questions:
            if isinstance(questions[0], Mapping):
                first_q = questions[0]
        header = str(first_q.get("header") or prompt_data.get("header") or "Pergunta").strip()
        question_text = str(
            first_q.get("question")
            or prompt_data.get("question")
            or prompt_data.get("summary")
            or "Escolha uma opção ou responda por mensagem para continuar:"
        ).strip()
        embed_title = _truncate_str(f"❓ {header} · {session_title}", EMBED_MAX_TITLE_CHARS)

        desc_parts = [question_text]
        raw_options = first_q.get("options") if isinstance(first_q.get("options"), Sequence) else prompt_data.get("options")
        if isinstance(raw_options, Sequence) and not isinstance(raw_options, (str, bytes)) and raw_options:
            opt_lines: list[str] = []
            for idx, opt in enumerate(raw_options[:SELECT_MAX_OPTIONS]):
                if isinstance(opt, Mapping):
                    lbl = str(opt.get("label") or opt.get("value") or f"Opção {idx + 1}").strip()
                    opt_desc = str(opt.get("description") or "").strip()
                    opt_lines.append(f"• **{lbl}**" + (f" — {opt_desc}" if opt_desc else ""))
                else:
                    opt_lines.append(f"• **{str(opt).strip()}**")
            if opt_lines:
                desc_parts.append("\n".join(opt_lines))
        description = _truncate_str("\n\n".join(desc_parts), EMBED_MAX_DESCRIPTION_CHARS)

    embed: dict[str, Any] = {
        "title": embed_title,
        "description": description,
        "color": COLOR_WAITING_USER,
    }
    if fields:
        embed["fields"] = fields[:EMBED_MAX_FIELDS]
    if conv_id:
        embed["footer"] = {"text": _truncate_str(f"Sessão: {conv_id}", EMBED_MAX_FOOTER_CHARS)}

    # Build interactive components (Buttons for <= 5 options, StringSelect for 6..25 options)
    components: list[dict[str, Any]] = []
    if not normalized_bindings:
        return embed, components

    first_payload = normalized_bindings[0]["payload"]
    payload_options = first_payload.get("options") if isinstance(first_payload.get("options"), Sequence) else None

    if len(normalized_bindings) >= 6:
        # 6..25 bindings -> render a StringSelect (type=3) where each option's value is the binding_id
        select_options: list[dict[str, Any]] = []
        for idx, b_info in enumerate(normalized_bindings[:SELECT_MAX_OPTIONS]):
            lbl, _ = _default_button_label_and_style(b_info, idx)
            opt_entry: dict[str, Any] = {
                "label": _truncate_str(lbl, SELECT_OPTION_MAX_CHARS),
                "value": _truncate_str(b_info["binding_id"], SELECT_OPTION_MAX_CHARS),
            }
            desc = b_info.get("description") or str(b_info["payload"].get("description") or "").strip()
            if desc:
                opt_entry["description"] = _truncate_str(desc, SELECT_OPTION_MAX_CHARS)
            select_options.append(opt_entry)

        components.append(
            {
                "type": 1,
                "components": [
                    {
                        "type": 3,
                        "custom_id": normalized_bindings[0]["custom_id"],
                        "placeholder": "Selecione uma opção…",
                        "min_values": 1,
                        "max_values": 1,
                        "options": select_options,
                    }
                ],
            }
        )
    elif (
        len(normalized_bindings) == 1
        and isinstance(payload_options, Sequence)
        and not isinstance(payload_options, (str, bytes))
        and len(payload_options) >= 6
    ):
        # Single binding carrying 6..25 options in its payload
        select_options = []
        for idx, opt in enumerate(payload_options[:SELECT_MAX_OPTIONS]):
            if isinstance(opt, Mapping):
                lbl = str(opt.get("label") or opt.get("value") or f"Opção {idx + 1}").strip()
                val = str(opt.get("value") or lbl).strip()
                desc = str(opt.get("description") or "").strip()
            else:
                lbl = str(opt).strip() or f"Opção {idx + 1}"
                val = lbl
                desc = ""
            opt_entry = {
                "label": _truncate_str(lbl, SELECT_OPTION_MAX_CHARS),
                "value": _truncate_str(val, SELECT_OPTION_MAX_CHARS),
            }
            if desc:
                opt_entry["description"] = _truncate_str(desc, SELECT_OPTION_MAX_CHARS)
            select_options.append(opt_entry)

        components.append(
            {
                "type": 1,
                "components": [
                    {
                        "type": 3,
                        "custom_id": normalized_bindings[0]["custom_id"],
                        "placeholder": "Selecione uma opção…",
                        "min_values": 1,
                        "max_values": 1,
                        "options": select_options,
                    }
                ],
            }
        )
    else:
        # Up to 5 bindings -> ActionRow(s) of Buttons (type=2)
        buttons: list[dict[str, Any]] = []
        for idx, b_info in enumerate(normalized_bindings[:25]):
            lbl, style = _default_button_label_and_style(b_info, idx)
            buttons.append(
                {
                    "type": 2,
                    "style": style,
                    "label": _truncate_str(lbl, BUTTON_MAX_LABEL_CHARS),
                    "custom_id": b_info["custom_id"],
                }
            )
        for row_start in range(0, len(buttons), 5):
            components.append(
                {
                    "type": 1,
                    "components": buttons[row_start : row_start + 5],
                }
            )

    return embed, components


def build_decision_components(
    prompt_data: Mapping[str, Any],
    bindings: Sequence[Any],
) -> list[dict[str, Any]]:
    """Convenience wrapper returning only the ActionRow components from `build_decision_card`."""
    _, components = build_decision_card(prompt_data, bindings)
    return components


def build_resolved_decision_components(
    chosen_label: str = "Respondido",
) -> list[dict[str, Any]]:
    """Build a disabled ActionRow showing `✔ Respondido` after a user clicks a decision button/select."""
    clean_label = _truncate_str(f"✔ {chosen_label.lstrip('✔ ').strip() or 'Respondido'}", BUTTON_MAX_LABEL_CHARS)
    return [
        {
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 2,  # Secondary
                    "label": clean_label,
                    "custom_id": "orin:v1:resolved",
                    "disabled": True,
                }
            ],
        }
    ]


__all__ = [
    "BUTTON_MAX_LABEL_CHARS",
    "COLOR_CANCELLED",
    "COLOR_COMPLETED",
    "COLOR_FAILED",
    "COLOR_RUNNING",
    "COLOR_WAITING_USER",
    "CUSTOM_ID_MAX_CHARS",
    "DEFAULT_SPLIT_LIMIT",
    "DISCORD_MESSAGE_MAX_CHARS",
    "EMBED_MAX_DESCRIPTION_CHARS",
    "EMBED_MAX_FIELDS",
    "EMBED_MAX_FIELD_NAME_CHARS",
    "EMBED_MAX_FIELD_VALUE_CHARS",
    "EMBED_MAX_TITLE_CHARS",
    "SELECT_MAX_OPTIONS",
    "SELECT_OPTION_MAX_CHARS",
    "build_decision_card",
    "build_decision_components",
    "build_live_progress_embed",
    "build_progress_components",
    "build_resolved_decision_components",
    "compute_progress_digest_hash",
    "sanitize_custom_id",
    "split_discord_markdown",
    "strip_Residual_speak_tags",
    "strip_residual_speak_tags",
]

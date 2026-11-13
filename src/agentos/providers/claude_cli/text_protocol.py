from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Literal, Mapping, Sequence
from uuid import uuid4

from agentos.agentic.tools.public_names import PUBLIC_NAMES

TOOL_CALL_OPEN = "<orin_tool_call"
TOOL_CALL_CLOSE = "</orin_tool_call>"
_ALT_CALL_OPEN = "<tool_call"
_ALT_CALL_CLOSE = "</tool_call>"
_HALLUCINATED_RESULT_MARKER = "[Tool Result"
_HALLUCINATED_RESULT_MARKERS: tuple[str, ...] = (
    "[Tool Result",
    "&#91;Tool Result",
    "&#x5b;Tool Result",
    "[/Tool Result]",
    "&#91;/Tool Result]",
    "&#x5b;/Tool Result]",
)
_BRACKET_TOOL_USE_PREFIXES: tuple[str, ...] = (
    "[Tool Use",
    "&#91;Tool Use",
    "&#x5b;Tool Use",
)
_BRACKET_TOOL_USE_CLOSES: tuple[str, ...] = (
    "[/Tool Use]",
    "&#91;/Tool Use]",
    "&#x5b;/Tool Use]",
)

_OPEN_TAG_RE = re.compile(
    r"<(?P<tag>orin_tool_call|tool_call)(?:\s+id=(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+)))?\s*>",
    re.IGNORECASE,
)
_BRACKET_OPEN_TAG_RE = re.compile(
    r"(?:\[|&#91;|&#x5b;)\s*Tool Use(?:\b|(?=:))(?P<attrs>[^\]]*)\]",
    re.IGNORECASE,
)
_BRACKET_ATTR_ID_RE = re.compile(r"""\bid=(?:"([^"]*)"|'([^']*)'|([^\s\]]+))""", re.IGNORECASE)
_BRACKET_ATTR_NAME_RE = re.compile(r"""\bname=(?:"([^"]*)"|'([^']*)'|([^\s\]]+))""", re.IGNORECASE)
_ESCAPE_TAG_RE = re.compile(r"<(/?)(orin_tool_call|tool_call)", re.IGNORECASE)
_ESCAPE_BRACKET_RE = re.compile(
    r"\[(/?)(tool result|tool use|user|assistant|system)", re.IGNORECASE
)

_INTERNAL_BY_PUBLIC = {
    "Glob": "list_files",
    **{public: internal for internal, public in PUBLIC_NAMES.items()},
}
_INTERNAL_BY_LOWER_PUBLIC = {
    "glob": "list_files",
    **{public.lower(): internal for internal, public in PUBLIC_NAMES.items()},
}
_PUBLIC_BY_LOWER_INTERNAL = {
    "list_files": "Glob",
    **{internal.lower(): public for internal, public in PUBLIC_NAMES.items()},
}

_ARG_ALIASES: dict[str, dict[str, str]] = {
    "read_file": {"file_path": "path", "filePath": "path", "filepath": "path"},
    "write_file": {"file_path": "path", "filePath": "path", "filepath": "path"},
    "edit_file": {
        "file_path": "path",
        "filePath": "path",
        "filepath": "path",
        "old_string": "old_text",
        "oldString": "old_text",
        "new_string": "new_text",
        "newString": "new_text",
    },
    "run_command": {"cmd": "command", "command_line": "command"},
}

_PUBLIC_ARG_ALIASES: dict[str, dict[str, str]] = {
    "Read": {"path": "file_path", "filePath": "file_path", "filepath": "file_path"},
    "Write": {"path": "file_path", "filePath": "file_path", "filepath": "file_path"},
    "Edit": {
        "path": "file_path",
        "filePath": "file_path",
        "filepath": "file_path",
        "old_text": "old_string",
        "oldString": "old_string",
        "new_text": "new_string",
        "newString": "new_string",
    },
    "Bash": {"cmd": "command", "command_line": "command"},
}


@dataclass(frozen=True, slots=True)
class TextProtocolEvent:
    kind: Literal["text", "tool_call_started", "tool_call", "stop"]
    text: str = ""
    call_id: str = ""
    name: str = ""
    arguments: str = ""


def escape_tool_content(text: str) -> str:
    """Escape protocol delimiters (case-insensitively) inside tool results and untrusted content."""
    if not text:
        return ""
    out = _ESCAPE_TAG_RE.sub(r"&lt;\1\2", str(text))
    out = _ESCAPE_BRACKET_RE.sub(r"&#91;\1\2", out)
    return out


def extract_tool_declarations(
    tools: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Extract normalized tool declarations and the set of declared tool names."""
    declarations: list[dict[str, Any]] = []
    allowed: set[str] = set()
    for item in tools or ():
        if not isinstance(item, Mapping):
            continue
        fn = item.get("function") if isinstance(item.get("function"), Mapping) else item
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        description = str(fn.get("description") or "").strip()
        parameters = (
            dict(fn["parameters"])
            if isinstance(fn.get("parameters"), Mapping)
            else (
                dict(fn["input_schema"])
                if isinstance(fn.get("input_schema"), Mapping)
                else {"type": "object", "properties": {}}
            )
        )
        declarations.append(
            {
                "name": name,
                "description": description,
                "parameters": parameters,
            }
        )
        allowed.add(name)
    return declarations, allowed


def build_system_prompt(
    stable_system: str,
    declarations: Sequence[Mapping[str, Any]],
) -> str:
    """Build a cache-stable system prompt containing the base system prompt and union of turn tools."""
    base = (stable_system or "").strip() or "You are a helpful assistant."
    if not declarations:
        return base

    normalized_decls: list[dict[str, Any]] = []
    for decl in declarations:
        if not isinstance(decl, Mapping):
            continue
        fn = decl.get("function") if isinstance(decl.get("function"), Mapping) else decl
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        params = (
            fn.get("parameters")
            if isinstance(fn.get("parameters"), Mapping)
            else (
                fn.get("input_schema")
                if isinstance(fn.get("input_schema"), Mapping)
                else {"type": "object", "properties": {}}
            )
        )
        normalized_decls.append(
            {
                "name": name,
                "description": str(fn.get("description") or "").strip(),
                "parameters": dict(params),
            }
        )

    if not normalized_decls:
        return base

    tools_json = json.dumps(normalized_decls, ensure_ascii=False, indent=2)
    protocol_section = (
        "\n\n# Orin Tool Calling Protocol\n"
        "When you need to invoke a tool from the catalog below, emit an XML block in the exact format:\n"
        '<orin_tool_call>{"name": "<tool_name>", "arguments": {<json_arguments>}}</orin_tool_call>\n\n'
        "Rules:\n"
        "1. Use only tool names declared in the catalog below and currently available.\n"
        "2. `arguments` must be a valid JSON object matching the tool's `parameters` schema.\n"
        "3. Do not wrap `<orin_tool_call>` inside markdown code fences.\n"
        "4. Emit only the independent tool calls for this step, all together, and stop. Never write `[Tool Result`.\n\n"
        "## Tool Catalog\n"
        f"{tools_json}"
    )
    return f"{base}{protocol_section}"


def _extract_content_text_and_images(
    raw_content: Any,
) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(raw_content, str):
        return raw_content, []
    if not isinstance(raw_content, list):
        return (str(raw_content) if raw_content is not None else ""), []

    text_parts: list[str] = []
    images: list[dict[str, Any]] = []
    for part in raw_content:
        if isinstance(part, str):
            text_parts.append(part)
            continue
        if not isinstance(part, Mapping):
            continue
        p_type = str(part.get("type") or "").strip().lower()
        if p_type in {"text", "input_text"}:
            val = part.get("text") or part.get("content") or ""
            if isinstance(val, str) and val:
                text_parts.append(val)
        elif p_type == "image":
            source = part.get("source")
            if isinstance(source, Mapping) and source.get("data"):
                images.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": str(
                                source.get("media_type") or "image/png"
                            ),
                            "data": str(source["data"]),
                        },
                    }
                )
            elif part.get("data"):
                images.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": str(
                                part.get("media_type")
                                or part.get("mime_type")
                                or "image/png"
                            ),
                            "data": str(part["data"]),
                        },
                    }
                )
        elif p_type in {"image_url", "input_image"}:
            url_obj = part.get("image_url")
            url_str = (
                str(url_obj.get("url") or "")
                if isinstance(url_obj, Mapping)
                else str(url_obj or part.get("url") or "")
            )
            if url_str.startswith("data:") and ";base64," in url_str:
                header, b64_data = url_str.split(";base64,", 1)
                media_type = header[5:] or "image/png"
                images.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data.strip(),
                        },
                    }
                )
    return "\n".join(text_parts), images


def _parse_args_dict(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
            if isinstance(parsed, Mapping):
                return dict(parsed)
        except json.JSONDecodeError:
            return {}
        return {}
    if isinstance(raw_args, Mapping):
        return dict(raw_args)
    return {}


def _format_single_message_group(
    msg: Mapping[str, Any],
    *,
    native_history: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    role = str(msg.get("role") or "user").strip().lower()
    raw_content = msg.get("content")
    text, images = _extract_content_text_and_images(raw_content)

    if role == "system":
        rendered = f"[System]\n{escape_tool_content(text.strip())}"
    elif role == "assistant":
        parts: list[str] = ["[Assistant]"]
        if text.strip():
            parts.append(escape_tool_content(text.strip()))
        raw_calls = msg.get("tool_calls")
        if isinstance(raw_calls, list):
            for idx, tc in enumerate(raw_calls, start=1):
                if not isinstance(tc, Mapping):
                    continue
                fn = (
                    tc.get("function")
                    if isinstance(tc.get("function"), Mapping)
                    else tc
                )
                call_id = str(tc.get("id") or f"call_{idx}").strip()
                safe_call_id = _safe_call_id(call_id)
                t_name = str(fn.get("name") or tc.get("name") or "").strip()
                raw_args = fn.get("arguments", tc.get("arguments", {}))
                parsed_args = _parse_args_dict(raw_args)
                payload = json.dumps(
                    {"name": t_name, "arguments": parsed_args},
                    ensure_ascii=False,
                )
                safe_payload = escape_tool_content(payload).replace("</", "<\\/")
                if native_history:
                    parts.append(
                        f"[Tool Use id={safe_call_id} name={t_name}]\n"
                        f"{safe_payload}\n"
                        f"[/Tool Use]"
                    )
                else:
                    parts.append(
                        f'<orin_tool_call id="{safe_call_id}">{safe_payload}</orin_tool_call>'
                    )
        if isinstance(raw_content, list):
            for idx, item in enumerate(raw_content, start=1):
                if not isinstance(item, Mapping):
                    continue
                if str(item.get("type") or "").strip().lower() == "tool_use":
                    call_id = str(item.get("id") or f"call_{idx}").strip()
                    safe_call_id = _safe_call_id(call_id)
                    t_name = str(item.get("name") or "").strip()
                    parsed_args = _parse_args_dict(
                        item.get("input", item.get("arguments", {}))
                    )
                    payload = json.dumps(
                        {"name": t_name, "arguments": parsed_args},
                        ensure_ascii=False,
                    )
                    safe_payload = escape_tool_content(payload).replace("</", "<\\/")
                    if native_history:
                        parts.append(
                            f"[Tool Use id={safe_call_id} name={t_name}]\n"
                            f"{safe_payload}\n"
                            f"[/Tool Use]"
                        )
                    else:
                        parts.append(
                            f'<orin_tool_call id="{safe_call_id}">{safe_payload}</orin_tool_call>'
                        )
        rendered = "\n".join(parts)
    else:
        user_segments: list[str] = []
        if isinstance(raw_content, list):
            for idx, item in enumerate(raw_content, start=1):
                if not isinstance(item, Mapping):
                    continue
                if str(item.get("type") or "").strip().lower() == "tool_result":
                    call_id = str(
                        item.get("tool_use_id") or item.get("id") or f"call_{idx}"
                    ).strip()
                    t_name = str(item.get("name") or item.get("tool_name") or "tool").strip()
                    is_err = bool(item.get("is_error"))
                    status = "error" if is_err else str(item.get("status") or "ok").strip()
                    tr_text, tr_images = _extract_content_text_and_images(
                        item.get("content")
                    )
                    escaped_tr = escape_tool_content(tr_text)
                    user_segments.append(
                        f"[Tool Result id={call_id} name={t_name} status={status}]\n"
                        f"{escaped_tr}\n"
                        f"[/Tool Result]"
                    )
                    images.extend(tr_images)
        if text.strip() or not user_segments:
            user_segments.append(f"[User]\n{escape_tool_content(text.strip())}")
        rendered = "\n\n".join(user_segments)

    blocks: list[dict[str, Any]] = [{"type": "text", "text": rendered}]
    blocks.extend(images)
    digest = hashlib.sha256(
        json.dumps(blocks, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return digest, blocks


def _safe_call_id(call_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-:]", "_", call_id)


def _format_tool_batch_group(
    tool_msgs: Sequence[Mapping[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    text_entries: list[str] = []
    all_images: list[dict[str, Any]] = []
    for msg in tool_msgs:
        raw_call_id = str(msg.get("tool_call_id") or msg.get("id") or "call").strip()
        # Same sanitizing as the tool-use header, so the call and its result keep one id.
        call_id = _safe_call_id(raw_call_id)
        name = str(msg.get("name") or msg.get("tool_name") or "tool").strip()
        is_err = bool(msg.get("is_error"))
        status = "error" if is_err else str(msg.get("status") or "ok").strip()
        raw_text, images = _extract_content_text_and_images(msg.get("content"))
        escaped = escape_tool_content(raw_text)
        text_entries.append(
            f"[Tool Result id={call_id} name={name} status={status}]\n"
            f"{escaped}\n"
            f"[/Tool Result]"
        )
        all_images.extend(images)

    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": "\n\n".join(text_entries)}
    ]
    blocks.extend(all_images)
    digest = hashlib.sha256(
        json.dumps(blocks, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return digest, blocks


def _clone_block(block: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(block)
    if isinstance(out.get("source"), Mapping):
        out["source"] = dict(out["source"])
    return out


class TranscriptState:
    """Per-turn append-only multi-block transcript builder for `claude -p`."""

    def __init__(self) -> None:
        self.last_rebuild_reason: str | None = None
        self._group_digests: list[str] = []
        self._group_blocks: list[list[dict[str, Any]]] = []

    def blocks_for(
        self,
        window: Sequence[Mapping[str, Any]],
        *,
        allowed_now: set[str] | None = None,
        closing: bool = False,
        native_history: bool = False,
        strip_leading_system: bool = True,
    ) -> list[dict[str, Any]]:
        msgs = [m for m in window if isinstance(m, Mapping)]
        volatile_system_msgs: list[Mapping[str, Any]] = []
        if strip_leading_system:
            if msgs and str(msgs[0].get("role") or "").strip().lower() == "system":
                msgs = msgs[1:]
            while msgs and str(msgs[0].get("role") or "").strip().lower() == "system":
                volatile_system_msgs.append(msgs.pop(0))

        groups: list[tuple[str, list[dict[str, Any]]]] = []
        idx = 0
        while idx < len(msgs):
            role = str(msgs[idx].get("role") or "").strip().lower()
            if role == "tool":
                batch: list[Mapping[str, Any]] = []
                while (
                    idx < len(msgs)
                    and str(msgs[idx].get("role") or "").strip().lower() == "tool"
                ):
                    batch.append(msgs[idx])
                    idx += 1
                groups.append(_format_tool_batch_group(batch))
            else:
                groups.append(
                    _format_single_message_group(
                        msgs[idx], native_history=native_history
                    )
                )
                idx += 1

        new_digests = [d for d, _ in groups]
        old_len = len(self._group_digests)

        if (
            old_len > 0
            and len(new_digests) >= old_len
            and new_digests[:old_len] == self._group_digests
        ):
            self.last_rebuild_reason = None
            for d, b in groups[old_len:]:
                self._group_digests.append(d)
                self._group_blocks.append(b)
        else:
            self.last_rebuild_reason = (
                "history_rewritten" if old_len > 0 else None
            )
            self._group_digests = new_digests
            self._group_blocks = [b for _, b in groups]

        result_blocks: list[dict[str, Any]] = [
            _clone_block(block)
            for grp in self._group_blocks
            for block in grp
        ]

        for v_msg in volatile_system_msgs:
            _, v_blocks = _format_single_message_group(
                v_msg, native_history=native_history
            )
            for block in v_blocks:
                result_blocks.append(_clone_block(block))

        if closing:
            result_blocks.append(
                {
                    "type": "text",
                    "text": (
                        "[System]\n"
                        "Ferramentas disponíveis agora: nenhuma. "
                        "Responda diretamente sem emitir `<orin_tool_call>`."
                    ),
                }
            )
        elif allowed_now is not None:
            if allowed_now:
                sorted_names = ", ".join(sorted(allowed_now))
                result_blocks.append(
                    {
                        "type": "text",
                        "text": f"[System]\nFerramentas disponíveis agora: {sorted_names}",
                    }
                )
            else:
                result_blocks.append(
                    {
                        "type": "text",
                        "text": (
                            "[System]\n"
                            "Ferramentas disponíveis agora: nenhuma. "
                            "Responda diretamente sem emitir `<orin_tool_call>`."
                        ),
                    }
                )

        if not result_blocks:
            result_blocks.append({"type": "text", "text": "[User]\nContinue."})
        return result_blocks


def _longest_suffix_prefix(text: str, target: str) -> int:
    max_k = min(len(text), len(target) - 1)
    for k in range(max_k, 0, -1):
        if text[-k:].lower() == target[:k].lower():
            return k
    return 0


def _find_token_outside_json_strings(
    buf: str, token: str
) -> int:
    in_string = False
    escaped = False
    target_lower = token.lower()
    target_len = len(token)
    buf_lower = buf.lower()
    i = 0
    limit = len(buf) - target_len
    while i <= limit:
        ch = buf[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if buf_lower[i : i + target_len] == target_lower:
            return i
        i += 1
    return -1


def _resolve_allowed_tool_name(
    raw_name: str, allowed_names: set[str] | None
) -> str | None:
    candidate = (raw_name or "").strip()
    if not candidate:
        return None
    if candidate.startswith("mcp__"):
        parts = candidate.split("__", 2)
        if len(parts) == 3 and parts[2]:
            candidate = parts[2]

    if allowed_names is None:
        return _INTERNAL_BY_PUBLIC.get(
            candidate, _INTERNAL_BY_LOWER_PUBLIC.get(candidate.lower(), candidate)
        )

    if not allowed_names:
        return None

    if candidate in allowed_names:
        return candidate

    lower_map = {n.lower(): n for n in allowed_names}
    if candidate.lower() in lower_map:
        return lower_map[candidate.lower()]

    internal = _INTERNAL_BY_PUBLIC.get(
        candidate, _INTERNAL_BY_LOWER_PUBLIC.get(candidate.lower())
    )
    if internal and internal in allowed_names:
        return internal
    if internal and internal.lower() in lower_map:
        return lower_map[internal.lower()]

    public = PUBLIC_NAMES.get(
        candidate, _PUBLIC_BY_LOWER_INTERNAL.get(candidate.lower())
    )
    if public and public in allowed_names:
        return public
    if public and public.lower() in lower_map:
        return lower_map[public.lower()]

    secondary = {
        "list_files": ("Glob", "LS"),
        "glob": ("glob_files", "list_files"),
        "ls": ("list_files", "Glob"),
        "grep": ("search_files",),
        "search_files": ("Grep",),
    }.get(candidate.lower(), ())
    for alt in secondary:
        if alt in allowed_names:
            return alt
        if alt.lower() in lower_map:
            return lower_map[alt.lower()]

    return None


def _normalize_tool_arguments(
    resolved_name: str, raw_args: Mapping[str, Any]
) -> dict[str, Any]:
    args = dict(raw_args)
    canonical_internal = _INTERNAL_BY_PUBLIC.get(
        resolved_name,
        _INTERNAL_BY_LOWER_PUBLIC.get(resolved_name.lower(), resolved_name),
    )
    if resolved_name == canonical_internal:
        aliases = _ARG_ALIASES.get(canonical_internal, {})
        for src_key, dst_key in aliases.items():
            if src_key in args and dst_key not in args:
                args[dst_key] = args.pop(src_key)
    else:
        canonical_public = PUBLIC_NAMES.get(
            canonical_internal,
            _PUBLIC_BY_LOWER_INTERNAL.get(canonical_internal.lower(), resolved_name),
        )
        aliases = _PUBLIC_ARG_ALIASES.get(canonical_public, {})
        for src_key, dst_key in aliases.items():
            if src_key in args and dst_key not in args:
                args[dst_key] = args.pop(src_key)
    return args


def resolve_native_tool_call(
    raw_name: str,
    raw_input: Mapping[str, Any],
    allowed_names: set[str] | None,
) -> tuple[str, str] | None:
    """Resolve a native or MCP `tool_use` call against `allowed_names` and normalize arguments."""
    resolved = _resolve_allowed_tool_name(raw_name, allowed_names)
    if resolved is None:
        return None
    args_dict = dict(raw_input) if isinstance(raw_input, Mapping) else {}
    normalized = _normalize_tool_arguments(resolved, args_dict)
    return resolved, json.dumps(normalized, ensure_ascii=False)


def _parse_json_objects(inner: str) -> list[Mapping[str, Any]]:
    text = inner.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            if lines[-1].strip().startswith("```"):
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            text = "\n".join(lines).strip()

    decoder = json.JSONDecoder()
    results: list[Mapping[str, Any]] = []
    idx = 0
    length = len(text)
    while idx < length:
        while idx < length and text[idx] in " \t\r\n,":
            idx += 1
        if idx >= length or text[idx] != "{":
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        if isinstance(obj, Mapping):
            results.append(obj)
        idx = end
    return results


def _find_earliest_hallucinated_marker(buf: str, *, outside_json_strings: bool = False) -> int:
    best = -1
    buf_lower = buf.lower()
    for marker in _HALLUCINATED_RESULT_MARKERS:
        pos = (
            _find_token_outside_json_strings(buf, marker)
            if outside_json_strings
            else buf_lower.find(marker.lower())
        )
        if pos != -1 and (best == -1 or pos < best):
            best = pos
    return best


def _find_earliest_close_token(buf: str, close_tokens: tuple[str, ...]) -> tuple[int, int]:
    best_pos = -1
    best_len = 0
    for token in close_tokens:
        pos = _find_token_outside_json_strings(buf, token)
        if pos != -1 and (best_pos == -1 or pos < best_pos):
            best_pos = pos
            best_len = len(token)
    return best_pos, best_len


class ToolCallStreamParser:
    """Streaming parser for `<orin_tool_call>` and `[Tool Use ...]` blocks that stops on hallucinated `[Tool Result`."""

    def __init__(self, allowed_names: set[str] | None = None) -> None:
        self._allowed_names = (
            set(allowed_names) if allowed_names is not None else None
        )
        self.stopped: bool = False
        self.saw_unclosed_tool_call: bool = False
        self._buf: str = ""
        self._active_close_tokens: tuple[str, ...] | None = None
        self._active_call_id: str | None = None
        self._active_name_hint: str | None = None
        self._seen_call_ids: set[str] = set()

    def _allocate_call_id(self, preferred: str | None) -> str:
        candidate = (preferred or "").strip()
        if candidate and candidate not in self._seen_call_ids:
            self._seen_call_ids.add(candidate)
            return candidate
        while True:
            generated = f"call_{uuid4().hex[:12]}"
            if generated not in self._seen_call_ids:
                self._seen_call_ids.add(generated)
                return generated

    def _parse_inner_calls(
        self,
        inner_xml: str,
        preallocated_id: str | None,
        *,
        fallback_name: str | None = None,
    ) -> list[TextProtocolEvent]:
        objs = _parse_json_objects(inner_xml)
        if not objs and fallback_name:
            resolved_empty = _resolve_allowed_tool_name(fallback_name, self._allowed_names)
            if resolved_empty is not None and not inner_xml.strip():
                call_id = self._allocate_call_id(preallocated_id)
                return [
                    TextProtocolEvent(
                        kind="tool_call",
                        call_id=call_id,
                        name=resolved_empty,
                        arguments="{}",
                    )
                ]
        events: list[TextProtocolEvent] = []
        for idx, obj in enumerate(objs):
            raw_name = str(
                obj.get("name") or obj.get("tool") or obj.get("function") or fallback_name or ""
            ).strip()
            resolved = _resolve_allowed_tool_name(raw_name, self._allowed_names)
            if resolved is None:
                continue
            has_envelope = any(k in obj for k in ("name", "tool", "function", "arguments", "input", "parameters"))
            if has_envelope:
                raw_args = obj.get("arguments")
                if raw_args is None:
                    raw_args = obj.get("input") or obj.get("parameters") or {}
            else:
                raw_args = obj
            if isinstance(raw_args, str):
                try:
                    parsed = json.loads(raw_args)
                    args_map = dict(parsed) if isinstance(parsed, Mapping) else {}
                except json.JSONDecodeError:
                    args_map = {}
            elif isinstance(raw_args, Mapping):
                args_map = dict(raw_args)
            else:
                args_map = {}

            normalized_args = _normalize_tool_arguments(resolved, args_map)
            explicit_json_id = str(obj.get("id") or "").strip() if has_envelope else ""
            if idx == 0 and preallocated_id and not explicit_json_id:
                call_id = preallocated_id
            else:
                call_id = self._allocate_call_id(explicit_json_id or None)
            events.append(
                TextProtocolEvent(
                    kind="tool_call",
                    call_id=call_id,
                    name=resolved,
                    arguments=json.dumps(normalized_args, ensure_ascii=False),
                )
            )
        return events

    def feed(self, chunk: str) -> list[TextProtocolEvent]:
        if self.stopped or not chunk:
            return []
        self._buf += chunk
        events: list[TextProtocolEvent] = []

        while self._buf and not self.stopped:
            if self._active_close_tokens is None:
                hallucinated_pos = _find_earliest_hallucinated_marker(self._buf)
                xml_match = _OPEN_TAG_RE.search(self._buf)
                bracket_match = _BRACKET_OPEN_TAG_RE.search(self._buf)

                chosen_kind: Literal["xml", "bracket"] | None = None
                tag_match: re.Match[str] | None = None
                if xml_match is not None and bracket_match is not None:
                    if xml_match.start() <= bracket_match.start():
                        chosen_kind = "xml"
                        tag_match = xml_match
                    else:
                        chosen_kind = "bracket"
                        tag_match = bracket_match
                elif xml_match is not None:
                    chosen_kind = "xml"
                    tag_match = xml_match
                elif bracket_match is not None:
                    chosen_kind = "bracket"
                    tag_match = bracket_match

                tag_pos = tag_match.start() if tag_match is not None else -1

                if hallucinated_pos != -1 and (
                    tag_pos == -1 or hallucinated_pos < tag_pos
                ):
                    before = self._buf[:hallucinated_pos].rstrip()
                    if before:
                        events.append(TextProtocolEvent(kind="text", text=before))
                    events.append(TextProtocolEvent(kind="stop"))
                    self.stopped = True
                    self._buf = ""
                    break

                if tag_match is not None and chosen_kind is not None:
                    before = self._buf[:tag_pos]
                    if before:
                        events.append(TextProtocolEvent(kind="text", text=before))
                    if chosen_kind == "xml":
                        tag_name = tag_match.group("tag").lower()
                        self._active_close_tokens = (
                            (TOOL_CALL_CLOSE,)
                            if tag_name == "orin_tool_call"
                            else (_ALT_CALL_CLOSE,)
                        )
                        attr_id = (
                            tag_match.group(2)
                            or tag_match.group(3)
                            or tag_match.group(4)
                            or ""
                        ).strip() or None
                        self._active_name_hint = None
                    else:
                        attrs_raw = tag_match.group("attrs") or ""
                        id_m = _BRACKET_ATTR_ID_RE.search(attrs_raw)
                        name_m = _BRACKET_ATTR_NAME_RE.search(attrs_raw)
                        attr_id = (
                            (id_m.group(1) or id_m.group(2) or id_m.group(3) or "").strip()
                            if id_m is not None
                            else ""
                        ) or None
                        name_hint = (
                            (name_m.group(1) or name_m.group(2) or name_m.group(3) or "").strip()
                            if name_m is not None
                            else ""
                        )
                        if not name_hint and attrs_raw.strip().startswith(":"):
                            name_hint = attrs_raw.strip().lstrip(":").strip().split()[0] if attrs_raw.strip().lstrip(":").strip() else ""
                        self._active_name_hint = name_hint or None
                        self._active_close_tokens = _BRACKET_TOOL_USE_CLOSES

                    self._active_call_id = self._allocate_call_id(attr_id)
                    if self._allowed_names is None or len(self._allowed_names) > 0:
                        events.append(
                            TextProtocolEvent(
                                kind="tool_call_started",
                                call_id=self._active_call_id,
                            )
                        )
                    self._buf = self._buf[tag_match.end() :]
                    continue

                hold_candidates = [
                    _longest_suffix_prefix(self._buf, TOOL_CALL_OPEN),
                    _longest_suffix_prefix(self._buf, _ALT_CALL_OPEN),
                    *(_longest_suffix_prefix(self._buf, p) for p in _BRACKET_TOOL_USE_PREFIXES),
                    *(_longest_suffix_prefix(self._buf, m) for m in _HALLUCINATED_RESULT_MARKERS),
                ]
                hold_len = max(hold_candidates)
                lower_buf = self._buf.lower()
                for open_prefix in (TOOL_CALL_OPEN, _ALT_CALL_OPEN):
                    last_open = lower_buf.rfind(open_prefix)
                    if last_open != -1 and ">" not in self._buf[last_open:]:
                        hold_len = max(hold_len, len(self._buf) - last_open)
                for bracket_prefix in _BRACKET_TOOL_USE_PREFIXES:
                    last_b_open = lower_buf.rfind(bracket_prefix.lower())
                    if last_b_open != -1 and "]" not in self._buf[last_b_open:]:
                        hold_len = max(hold_len, len(self._buf) - last_b_open)

                if hold_len > 0:
                    emit_part = self._buf[:-hold_len]
                    self._buf = self._buf[-hold_len:]
                else:
                    emit_part = self._buf
                    self._buf = ""

                if emit_part:
                    events.append(TextProtocolEvent(kind="text", text=emit_part))
                break
            else:
                close_pos, close_len = _find_earliest_close_token(
                    self._buf, self._active_close_tokens
                )
                hallucinated_pos = _find_earliest_hallucinated_marker(
                    self._buf, outside_json_strings=True
                )
                if hallucinated_pos != -1 and (
                    close_pos == -1 or hallucinated_pos < close_pos
                ):
                    inner_before = self._buf[:hallucinated_pos]
                    pre_id = self._active_call_id
                    name_hint = self._active_name_hint
                    self._active_close_tokens = None
                    self._active_call_id = None
                    self._active_name_hint = None
                    events.extend(
                        self._parse_inner_calls(inner_before, pre_id, fallback_name=name_hint)
                    )
                    events.append(TextProtocolEvent(kind="stop"))
                    self.stopped = True
                    self._buf = ""
                    break

                if close_pos != -1:
                    inner_xml = self._buf[:close_pos]
                    after = self._buf[close_pos + close_len :]
                    pre_id = self._active_call_id
                    name_hint = self._active_name_hint
                    self._active_close_tokens = None
                    self._active_call_id = None
                    self._active_name_hint = None
                    events.extend(
                        self._parse_inner_calls(inner_xml, pre_id, fallback_name=name_hint)
                    )
                    self._buf = after
                    continue
                break

        return events

    def finish(self) -> list[TextProtocolEvent]:
        if self.stopped:
            self._buf = ""
            self._active_close_tokens = None
            self._active_call_id = None
            self._active_name_hint = None
            return []

        events: list[TextProtocolEvent] = []
        leftover = self._buf
        self._buf = ""

        if self._active_close_tokens is not None:
            pre_id = self._active_call_id
            name_hint = self._active_name_hint
            self._active_close_tokens = None
            self._active_call_id = None
            self._active_name_hint = None
            hallucinated_pos = _find_earliest_hallucinated_marker(
                leftover, outside_json_strings=True
            )
            if hallucinated_pos != -1:
                events.extend(
                    self._parse_inner_calls(
                        leftover[:hallucinated_pos], pre_id, fallback_name=name_hint
                    )
                )
                events.append(TextProtocolEvent(kind="stop"))
                self.stopped = True
                return events
            parsed = self._parse_inner_calls(leftover, pre_id, fallback_name=name_hint)
            if parsed:
                events.extend(parsed)
            else:
                self.saw_unclosed_tool_call = True
            return events

        if leftover:
            hallucinated_pos = _find_earliest_hallucinated_marker(leftover)
            if hallucinated_pos != -1:
                before = leftover[:hallucinated_pos].rstrip()
                if before:
                    events.append(TextProtocolEvent(kind="text", text=before))
                events.append(TextProtocolEvent(kind="stop"))
                self.stopped = True
            else:
                events.append(TextProtocolEvent(kind="text", text=leftover))
        return events


_CODE_FENCE_SPLIT_RE = re.compile(r"(```[\s\S]*?(?:```|\Z)|~~~[\s\S]*?(?:~~~|\Z))")
_RESIDUAL_CLOSED_TOOL_BLOCK_RE = re.compile(
    r"(?:\[|&#91;|&#x5b;)\s*Tool (?:Use|Result)\b[^\]]*\][\s\S]*?"
    r"(?:\[|&#91;|&#x5b;)\s*/Tool (?:Use|Result)\s*\]"
    r"(?:\s*(?:\[|&#91;|&#x5b;)\s*Assistant\s*\]|Assistant(?=\s|[A-ZÀ-Ú0-9*_`\"'\[\(\n]|$)\s*:?\s*)?",
    re.IGNORECASE,
)
_RESIDUAL_UNCLOSED_TOOL_BLOCK_RE = re.compile(
    r"(?:\[|&#91;|&#x5b;)\s*Tool (?:Use|Result)\b[^\]]*\][\s\S]*\Z",
    re.IGNORECASE,
)
_RESIDUAL_XML_TOOL_BLOCK_RE = re.compile(
    r"(?:<|&lt;)(?:orin_tool_call|tool_call)\b[^>&]*(?:>|&gt;)[\s\S]*?"
    r"(?:(?:<|&lt;)/(?:orin_tool_call|tool_call)\s*(?:>|&gt;)|\Z)",
    re.IGNORECASE,
)
_RESIDUAL_STREAM_PREFIXES: tuple[str, ...] = (
    "[Tool Use",
    "[Tool Result",
    "&#91;Tool Use",
    "&#91;Tool Result",
    "&#x5b;Tool Use",
    "&#x5b;Tool Result",
    "<orin_tool_call",
    "<tool_call",
    "&lt;orin_tool_call",
    "&lt;tool_call",
)
_RESIDUAL_STREAM_OPEN_RE = re.compile(
    r"(?:\[|&#91;|&#x5b;)\s*Tool (?P<bkind>Use|Result)\b[^\]]*\]"
    r"|(?:<|&lt;)(?P<xtag>orin_tool_call|tool_call)\b[^>&]*(?:>|&gt;)",
    re.IGNORECASE,
)
_GLUED_ASSISTANT_PREFIX_RE = re.compile(
    r"^(?:\s*(?:\[|&#91;|&#x5b;)\s*Assistant\s*\]\s*|Assistant(?=\s|[A-ZÀ-Ú0-9*_`\"'\[\(\n]|$)\s*:?\s*)",
)


def strip_residual_tool_protocol_blocks(text: str) -> str:
    """Strip leaked `[Tool Use ...][/Tool Use]`, `[Tool Result ...][/Tool Result]`, `&#91;...]` and `<orin_tool_call>` blocks outside code fences."""
    if not isinstance(text, str) or not text:
        return ""
    lowered = text.lower()
    if (
        "tool use" not in lowered
        and "tool result" not in lowered
        and "orin_tool_call" not in lowered
        and "<tool_call" not in lowered
        and "&lt;tool_call" not in lowered
    ):
        return text

    parts = _CODE_FENCE_SPLIT_RE.split(text)
    cleaned_parts: list[str] = []
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            cleaned_parts.append(part)
            continue
        segment = _RESIDUAL_CLOSED_TOOL_BLOCK_RE.sub("", part)
        segment = _RESIDUAL_UNCLOSED_TOOL_BLOCK_RE.sub("", segment)
        segment = _RESIDUAL_XML_TOOL_BLOCK_RE.sub("", segment)
        cleaned_parts.append(segment)
    return "".join(cleaned_parts)


class ResidualToolTextStreamFilter:
    """Stateful stream filter that suppresses hallucinated `[Tool Use]` / `[Tool Result]` / `&#91;Tool Use]` text blocks in native turns."""

    def __init__(self) -> None:
        self._buf: str = ""
        self._active_close_re: re.Pattern[str] | None = None
        self._consume_glued_assistant: bool = False
        self._in_code_fence: bool = False

    def _update_fence_state(self, emitted: str) -> None:
        count = emitted.count("```")
        if count % 2 == 1:
            self._in_code_fence = not self._in_code_fence

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        if (
            not self._buf
            and self._active_close_re is None
            and not self._consume_glued_assistant
            and "[" not in chunk
            and "&" not in chunk
            and "<" not in chunk
        ):
            self._update_fence_state(chunk)
            return chunk

        self._buf += chunk
        out: list[str] = []

        while self._buf:
            if self._consume_glued_assistant:
                if len(self._buf) < 16 and any(
                    cand.startswith(self._buf)
                    for cand in ("Assistant", "[Assistant]", "&#91;Assistant]", " [Assistant]", "\n[Assistant]")
                ):
                    break
                m_glued = _GLUED_ASSISTANT_PREFIX_RE.match(self._buf)
                if m_glued is not None:
                    self._buf = self._buf[m_glued.end() :]
                self._consume_glued_assistant = False
                if not self._buf:
                    break

            if self._active_close_re is not None:
                m_close = self._active_close_re.search(self._buf)
                if m_close is None:
                    # Keep bounded tail while waiting for close tag so memory stays small
                    if len(self._buf) > 65_536:
                        self._buf = self._buf[-128:]
                    break
                after = self._buf[m_close.end() :]
                self._active_close_re = None
                self._consume_glued_assistant = True
                self._buf = after
                continue

            m_open = None if self._in_code_fence else _RESIDUAL_STREAM_OPEN_RE.search(self._buf)
            if m_open is not None:
                before = self._buf[: m_open.start()]
                if before:
                    self._update_fence_state(before)
                    out.append(before)
                    if self._in_code_fence:
                        # Opened a fence right before the match; emit match literally
                        lit = self._buf[m_open.start() : m_open.end()]
                        self._update_fence_state(lit)
                        out.append(lit)
                        self._buf = self._buf[m_open.end() :]
                        continue
                bkind = m_open.group("bkind")
                xtag = m_open.group("xtag")
                if bkind:
                    self._active_close_re = re.compile(
                        rf"(?:\[|&#91;|&#x5b;)\s*/Tool {re.escape(bkind)}\s*\]",
                        re.IGNORECASE,
                    )
                else:
                    self._active_close_re = re.compile(
                        rf"(?:<|&lt;)/{re.escape(xtag or 'orin_tool_call')}\s*(?:>|&gt;)",
                        re.IGNORECASE,
                    )
                self._buf = self._buf[m_open.end() :]
                continue

            if self._in_code_fence:
                emit_all = self._buf
                self._buf = ""
                self._update_fence_state(emit_all)
                out.append(emit_all)
                break

            hold = max(
                (_longest_suffix_prefix(self._buf, p) for p in _RESIDUAL_STREAM_PREFIXES),
                default=0,
            )
            lower_buf = self._buf.lower()
            for p in _RESIDUAL_STREAM_PREFIXES:
                idx = lower_buf.rfind(p.lower())
                if idx != -1:
                    tail = self._buf[idx:]
                    if p.startswith(("[", "&")) and "tool " in p.lower() and "]" not in tail and len(tail) < 256:
                        hold = max(hold, len(tail))
                    elif p.startswith(("<", "&lt;")) and ">" not in tail and "&gt;" not in tail and len(tail) < 256:
                        hold = max(hold, len(tail))

            if hold > 0:
                emit_part = self._buf[:-hold]
                self._buf = self._buf[-hold:]
            else:
                emit_part = self._buf
                self._buf = ""

            if emit_part:
                self._update_fence_state(emit_part)
                out.append(emit_part)
            break

        return "".join(out)

    def finish(self) -> str:
        if self._active_close_re is not None:
            # Unclosed residual tool block at EOF: drop it cleanly
            self._buf = ""
            self._active_close_re = None
            self._consume_glued_assistant = False
            return ""
        if self._consume_glued_assistant and self._buf:
            m_glued = _GLUED_ASSISTANT_PREFIX_RE.match(self._buf)
            if m_glued is not None:
                self._buf = self._buf[m_glued.end() :]
            self._consume_glued_assistant = False
        leftover = strip_residual_tool_protocol_blocks(self._buf)
        self._buf = ""
        if leftover:
            self._update_fence_state(leftover)
        return leftover

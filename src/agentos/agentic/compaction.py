"""What a compaction summary looks like, and the message that replaces the history it folds.

One set of headings for Chat and Code mode, in English for every model, with the summary
itself written in the conversation's language.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Iterable, Mapping

COMPACTION_SECTIONS = (
    "Primary Request and Intent",
    "Key Technical Concepts",
    "Files and Code Sections",
    "Errors and fixes",
    "Problem Solving",
    "All user messages",
    "Pending Tasks",
    "Current Work",
    "Optional Next Step",
)
COMPACTION_HEADING = "## Compacted context"
COMPACTION_HEADER = (
    f"{COMPACTION_HEADING}\n"
    "[This conversation continues from an earlier part that no longer fits in the context window. "
    "The summary below is authoritative for the paths, decisions and values it records; "
    "read a file again only when you expect it to have changed since then.]"
)
COMPACTION_INSTRUCTION = (
    "Summarize the conversation below so that another agent can continue the work without redoing it.\n"
    "First think through the conversation in order inside <analysis>...</analysis> "
    "(keep <analysis> concise, at most ~350 words, so the main output budget stays reserved for the sections below); "
    "that part is discarded.\n"
    "Then, outside the analysis, write exactly these nine markdown sections in this order, each as a "
    "'### ' heading, writing '- none' under a section that has nothing:\n"
    + "\n".join(f"### {section}" for section in COMPACTION_SECTIONS)
    + "\nUnder 'Files and Code Sections' give every file read or changed with its exact path and what matters about it. "
    "Under 'Errors and fixes' give each error and how it was resolved. Under 'All user messages' list every request "
    "the person made, in order. Under 'Pending Tasks' and 'Current Work' say exactly what is left and what was being "
    "done right before this summary. Keep exact paths, identifiers and figures, and invent nothing. "
    "Write the summary in the same language the person uses in the conversation."
)
MICROCOMPACT_MARKER = "[microcompacted: an older tool result was cleared to save context; run the tool again if you need it]"

COMPACT_RETAINED_USER_TOKENS = 16_000
RETAINED_USER_REQUESTS_HEADING = "### Verbatim User Requests (authoritative)"
_MAX_SINGLE_USER_MESSAGE_CHARS = 8_000
MAX_SINGLE_USER_REQUEST_CHARS = _MAX_SINGLE_USER_MESSAGE_CHARS
_HALF_SINGLE_USER_MESSAGE_CHARS = 4_000

_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>[\s\S]*?</system-reminder>", flags=re.IGNORECASE)
_DATA_URI_RE = re.compile(r"data:[a-zA-Z0-9.+/-]+;base64,[A-Za-z0-9+/=\s]+")
_TURN_ENTRY_SPLIT_RE = re.compile(r"(?:^|\n\n)\[\s*Turno\s*#\d+\s*\]\n")


def compaction_message(body: str) -> dict[str, object]:
    """The message that stands in for everything a compaction folded."""
    return {"role": "system", "content": f"{COMPACTION_HEADER}\n{body}"}


def strip_retained_user_requests_section(text: str) -> str:
    """Remove an existing Verbatim User Requests section from a summary body."""
    if not text or RETAINED_USER_REQUESTS_HEADING not in text:
        return text
    idx = text.find(RETAINED_USER_REQUESTS_HEADING)
    return text[:idx].rstrip()


def extract_retained_user_requests(text: str) -> list[str]:
    """Extract raw user request strings from a summary's Verbatim User Requests section."""
    if not text or RETAINED_USER_REQUESTS_HEADING not in text:
        return []
    idx = text.find(RETAINED_USER_REQUESTS_HEADING)
    section_body = text[idx + len(RETAINED_USER_REQUESTS_HEADING) :].strip()
    if not section_body:
        return []
    parts = _TURN_ENTRY_SPLIT_RE.split(section_body)
    return [part.strip() for part in parts if part and part.strip()]


def _clean_single_user_string(raw_text: str) -> str:
    if not raw_text:
        return ""
    cleaned = _SYSTEM_REMINDER_RE.sub("", raw_text)
    cleaned = _DATA_URI_RE.sub("", cleaned).strip()
    if not cleaned:
        return ""
    if (
        cleaned.startswith(COMPACTION_HEADING)
        or cleaned.startswith("[Sistema] ")
        or cleaned.startswith("[Hook Stop]")
        or cleaned.startswith("/compact")
        or cleaned == "Conteúdo visual do arquivo solicitado:"
        or cleaned == "Analise os anexos enviados e responda de forma objetiva."
    ):
        return ""
    return cleaned


def extract_real_user_texts(item: Mapping[str, object] | str) -> list[str]:
    """Return real user request strings from a message dict or raw string."""
    if isinstance(item, str):
        stripped = item.lstrip()
        if RETAINED_USER_REQUESTS_HEADING in item and (
            stripped.startswith(COMPACTION_HEADING)
            or stripped.startswith(RETAINED_USER_REQUESTS_HEADING)
            or "### Primary Request and Intent" in item
        ):
            return extract_retained_user_requests(item)
        single = _clean_single_user_string(item)
        return [single] if single else []

    if not isinstance(item, Mapping):
        return []

    if (
        bool(item.get("_first_user_context"))
        or bool(item.get("_tool_image_result"))
        or bool(item.get("_is_command_expansion"))
    ):
        return []

    role = str(item.get("role") or "user")
    content = item.get("content")

    if role == "system":
        if isinstance(content, str) and RETAINED_USER_REQUESTS_HEADING in content:
            return extract_retained_user_requests(content)
        return []

    if role != "user":
        return []

    if isinstance(content, list):
        if any(isinstance(block, Mapping) and block.get("type") == "tool_result" for block in content):
            return []
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "text":
                t_val = str(block.get("text") or "").strip()
                if t_val:
                    text_parts.append(t_val)
        raw = "\n".join(text_parts)
    elif isinstance(content, str):
        raw = content
    else:
        return []

    if RETAINED_USER_REQUESTS_HEADING in raw and raw.lstrip().startswith(COMPACTION_HEADING):
        return extract_retained_user_requests(raw)

    single = _clean_single_user_string(raw)
    return [single] if single else []


def _truncate_individual_user_message(text: str, max_chars: int = _MAX_SINGLE_USER_MESSAGE_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    half = min(_HALF_SINGLE_USER_MESSAGE_CHARS, max_chars // 2)
    omitted = len(text) - (2 * half)
    return f"{text[:half]}\n...[trecho intermediário omitido ({omitted} chars)]...\n{text[-half:]}"


_truncate_single_user_request = _truncate_individual_user_message


def build_retained_user_requests_section(
    user_messages: Iterable[Mapping[str, object] | str],
    *,
    max_tokens: int = COMPACT_RETAINED_USER_TOKENS,
    max_single_chars: int = _MAX_SINGLE_USER_MESSAGE_CHARS,
) -> str:
    """Build the authoritative verbatim user requests markdown section up to ``max_tokens``."""
    if max_tokens <= 0:
        return ""
    collected: list[str] = []
    for item in user_messages:
        for text in extract_real_user_texts(item):
            if not text:
                continue
            canon_text = _truncate_individual_user_message(text, max_chars=max_single_chars)
            if not collected or _truncate_individual_user_message(collected[-1], max_chars=max_single_chars) != canon_text:
                collected.append(text)
    if not collected:
        return ""

    formatted = [_truncate_individual_user_message(text, max_chars=max_single_chars) for text in collected]
    indexed_entries = [
        (turn_idx, f"[ Turno #{turn_idx} ]\n{body}")
        for turn_idx, body in enumerate(formatted, start=1)
    ]

    max_chars = max(0, int(max_tokens) * 4)
    selected: list[tuple[int, str]] = []
    used_chars = len(RETAINED_USER_REQUESTS_HEADING) + 1

    for turn_idx, entry in reversed(indexed_entries):
        sep_chars = 2 if selected else 0
        if used_chars + sep_chars + len(entry) <= max_chars:
            selected.append((turn_idx, entry))
            used_chars += sep_chars + len(entry)
        else:
            if not selected:
                prefix = f"[ Turno #{turn_idx} ]\n"
                avail = max_chars - used_chars - len(prefix)
                if avail >= 32:
                    raw_body = formatted[turn_idx - 1]
                    clipped = _truncate_individual_user_message(raw_body, max_chars=avail)
                    selected.append((turn_idx, f"{prefix}{clipped[:avail]}"))
            break

    if not selected:
        return ""

    selected.reverse()
    return f"{RETAINED_USER_REQUESTS_HEADING}\n" + "\n\n".join(entry for _, entry in selected)


def attach_retained_user_requests(
    summary: str,
    user_messages: Iterable[Mapping[str, object] | str],
    *,
    max_tokens: int = COMPACT_RETAINED_USER_TOKENS,
    max_single_chars: int = _MAX_SINGLE_USER_MESSAGE_CHARS,
) -> str:
    """Idempotently attach or replace the Verbatim User Requests section on ``summary``."""
    prior_requests = extract_retained_user_requests(summary)
    clean_summary = strip_retained_user_requests_section(summary)

    explicit_requests: list[str] = []
    for item in user_messages:
        for text in extract_real_user_texts(item):
            if not text:
                continue
            canon_text = _truncate_individual_user_message(text, max_chars=max_single_chars)
            if not explicit_requests or _truncate_individual_user_message(explicit_requests[-1], max_chars=max_single_chars) != canon_text:
                explicit_requests.append(text)

    if prior_requests:
        explicit_set: set[str] = set()
        for m in explicit_requests:
            explicit_set.add(m)
            explicit_set.add(_truncate_individual_user_message(m, max_chars=max_single_chars))
            if max_single_chars != _MAX_SINGLE_USER_MESSAGE_CHARS:
                explicit_set.add(_truncate_individual_user_message(m, max_chars=_MAX_SINGLE_USER_MESSAGE_CHARS))
        missing_prior: list[str] = []
        seen_prior: set[str] = set()
        for req in prior_requests:
            canon_req = _truncate_individual_user_message(req, max_chars=max_single_chars)
            canon_default = _truncate_individual_user_message(req, max_chars=_MAX_SINGLE_USER_MESSAGE_CHARS)
            if (
                req not in explicit_set
                and canon_req not in explicit_set
                and canon_default not in explicit_set
                and canon_req not in seen_prior
            ):
                missing_prior.append(req)
                seen_prior.add(req)
                seen_prior.add(canon_req)
                seen_prior.add(canon_default)
        combined: list[str] = [*missing_prior, *explicit_requests]
    else:
        combined = explicit_requests

    section = build_retained_user_requests_section(
        combined,
        max_tokens=max_tokens,
        max_single_chars=max_single_chars,
    )
    if not section:
        return clean_summary
    if not clean_summary:
        return section
    return f"{clean_summary}\n\n{section}"


def compress_tool_output_head_tail(text: str, max_chars: int = 1600) -> str:
    """Compress a tool output keeping 60% head + 40% tail with omitted chars/lines marker."""
    if "\n[compressed: " in text and (
        " characters total; " in text
        or text.endswith(" characters total; re-run the tool if you need the rest]")
    ):
        return text
    if len(text) <= max_chars:
        return text
    head_len = max(1, int(max_chars * 0.60))
    tail_len = max(1, max_chars - head_len)
    omitted_text = text[head_len : len(text) - tail_len]
    omitted_chars = len(omitted_text)
    omitted_lines = omitted_text.count("\n")
    return (
        f"{text[:head_len]}\n"
        f"[compressed: {len(text)} characters total; {omitted_chars} chars / {omitted_lines} lines omitted; "
        f"re-run the tool if you need the rest]\n"
        f"{text[-tail_len:]}"
    )


_MEDIA_BLOCK_TYPES = frozenset({"image", "image_url", "input_audio", "document", "audio", "video"})


def _is_media_dict(block: Mapping[str, object]) -> bool:
    b_type = str(block.get("type") or "").strip().lower()
    if b_type in _MEDIA_BLOCK_TYPES:
        return True
    for key in ("data", "image_url", "input_audio"):
        if key in block:
            val = block.get(key)
            if isinstance(val, Mapping) or (isinstance(val, str) and (len(val) > 128 or _DATA_URI_RE.search(val))):
                return True
    source = block.get("source")
    if isinstance(source, Mapping):
        if str(source.get("type") or "").strip().lower() in {"base64", "url"} or "data" in source:
            return True
    return False


def _sanitize_content_value(content: object) -> object:
    if isinstance(content, str):
        return _DATA_URI_RE.sub("[media attachment omitted]", content)
    if isinstance(content, list):
        sanitized_list: list[object] = []
        for item in content:
            if isinstance(item, Mapping):
                if _is_media_dict(item):
                    sanitized_list.append("[media attachment omitted]")
                elif str(item.get("type") or "").strip().lower() == "tool_result":
                    sanitized_list.append({**item, "content": _sanitize_content_value(item.get("content"))})
                else:
                    cleaned_item: dict[str, object] = {}
                    for k, v in item.items():
                        if k in {"data", "image_url", "input_audio"} and len(str(v)) > 128:
                            cleaned_item[k] = "[media attachment omitted]"
                        elif isinstance(v, (str, list)):
                            cleaned_item[k] = _sanitize_content_value(v)
                        else:
                            cleaned_item[k] = v
                    sanitized_list.append(cleaned_item)
            elif isinstance(item, str):
                sanitized_list.append(_DATA_URI_RE.sub("[media attachment omitted]", item))
            else:
                sanitized_list.append(item)
        return sanitized_list
    return content


def _stringify_content(content: object) -> str:
    """Serialize message content while replacing multimodal/base64 blocks with '[media attachment omitted]'."""
    if content is None:
        return ""
    if isinstance(content, str):
        return _DATA_URI_RE.sub("[media attachment omitted]", content)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(_DATA_URI_RE.sub("[media attachment omitted]", item))
            elif isinstance(item, Mapping):
                if _is_media_dict(item):
                    parts.append("[media attachment omitted]")
                elif str(item.get("type") or "").strip().lower() == "text" and "text" in item:
                    parts.append(_DATA_URI_RE.sub("[media attachment omitted]", str(item.get("text") or "")))
                else:
                    sanitized = _sanitize_content_value([item])
                    entry = sanitized[0] if isinstance(sanitized, list) and sanitized else item
                    if isinstance(entry, str):
                        parts.append(entry)
                    else:
                        try:
                            parts.append(json.dumps(entry, ensure_ascii=False, default=str))
                        except (TypeError, ValueError):
                            parts.append(str(entry))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    if isinstance(content, Mapping):
        if _is_media_dict(content):
            return "[media attachment omitted]"
        try:
            return json.dumps(_sanitize_content_value([content])[0], ensure_ascii=False, default=str)  # type: ignore[index]
        except (TypeError, ValueError):
            return str(content)
    return str(content)


def _encode_compaction_message(
    msg: Mapping[str, object],
    *,
    max_piece: int,
    is_tool_result: Callable[[Mapping[str, object]], bool],
    compress_tool_message: Callable[[Mapping[str, object]], dict[str, object]],
) -> str:
    target: Mapping[str, object] = compress_tool_message(msg) if is_tool_result(msg) else msg
    if "content" in target:
        sanitized_content = _sanitize_content_value(target.get("content"))
        if sanitized_content is not target.get("content"):
            target = {**target, "content": sanitized_content}
    try:
        encoded = json.dumps(target, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        encoded = str(target)
    if len(encoded) <= max_piece:
        return encoded
    if max_piece > 400:
        usable = max(2, max_piece - 56)
        head_len = max(1, int(usable * 0.60))
        tail_len = max(1, usable - head_len)
        omitted_chars = max(0, len(encoded) - head_len - tail_len)
        return f"{encoded[:head_len]}\n...[trecho truncado ({omitted_chars} chars omitidos)]...\n{encoded[-tail_len:]}"
    return encoded[: max(0, max_piece)]


def build_compact_source_partitioned(
    messages: list[dict[str, object]],
    indices: set[int],
    *,
    budget_chars: int,
    is_tool_result: Callable[[Mapping[str, object]], bool],
    compress_tool_message: Callable[[Mapping[str, object]], dict[str, object]],
) -> str:
    """Build compaction source using deterministic Head (20%) + User Spine (25%) + Tail (55% + remainder)."""
    valid_indices = [idx for idx in sorted(indices) if 0 <= idx < len(messages)]
    if not valid_indices or budget_chars <= 0:
        return ""

    selected_chunks: dict[int, str] = {}

    # 1. Head: 20% of budget (floor 8,000 chars, capped at budget_chars).
    # Starts from the first real user message, including any preceding system summaries.
    head_budget = min(budget_chars, max(8_000, int(budget_chars * 0.20)))
    head_used = 0
    first_user_pos = next(
        (
            pos
            for pos, idx in enumerate(valid_indices)
            if messages[idx].get("role") == "user" and bool(extract_real_user_texts(messages[idx]))
        ),
        0,
    )
    head_candidates: list[int] = [
        idx for idx in valid_indices[:first_user_pos] if messages[idx].get("role") == "system"
    ]
    head_candidates.extend(valid_indices[first_user_pos:])
    first_user_idx = valid_indices[first_user_pos] if valid_indices else None

    for idx in head_candidates:
        rem = head_budget - head_used
        if rem <= 0 and idx != first_user_idx:
            break
        max_piece = max(1_000, rem) if idx == first_user_idx else rem
        chunk = _encode_compaction_message(
            messages[idx],
            max_piece=max_piece,
            is_tool_result=is_tool_result,
            compress_tool_message=compress_tool_message,
        )
        if head_used > 0 and idx != first_user_idx and head_used + len(chunk) > head_budget:
            break
        selected_chunks[idx] = chunk
        head_used += len(chunk)
        if head_used >= head_budget:
            break

    # 2. User Spine: 25% of budget for all real role == "user" messages in compact_indices.
    spine_budget = max(0, min(int(budget_chars * 0.25), budget_chars - head_used))
    spine_used = 0
    spine_candidates = [
        idx
        for idx in valid_indices
        if idx not in selected_chunks
        and messages[idx].get("role") == "user"
        and bool(extract_real_user_texts(messages[idx]))
    ]
    for pos_s, idx in enumerate(spine_candidates):
        rem = spine_budget - spine_used
        if rem <= 0:
            break
        remaining_count = max(1, len(spine_candidates) - pos_s)
        per_item_cap = max(800, rem // remaining_count) if remaining_count > 1 else rem
        chunk = _encode_compaction_message(
            messages[idx],
            max_piece=max(per_item_cap, min(rem, 4_000)),
            is_tool_result=is_tool_result,
            compress_tool_message=compress_tool_message,
        )
        selected_chunks[idx] = chunk
        spine_used += len(chunk)

    # 3. Tail: 55% of budget + unused remainder from Head and User Spine.
    tail_budget = max(0, budget_chars - head_used - spine_used)
    tail_used = 0
    for idx in reversed(valid_indices):
        if idx in selected_chunks:
            continue
        rem = tail_budget - tail_used
        if rem <= 0:
            break
        raw_chunk = _encode_compaction_message(
            messages[idx],
            max_piece=max(rem, 4_000),
            is_tool_result=is_tool_result,
            compress_tool_message=compress_tool_message,
        )
        if len(raw_chunk) <= rem:
            selected_chunks[idx] = raw_chunk
            tail_used += len(raw_chunk)
        elif rem >= 900 or tail_used == 0:
            clipped = _encode_compaction_message(
                messages[idx],
                max_piece=rem,
                is_tool_result=is_tool_result,
                compress_tool_message=compress_tool_message,
            )
            selected_chunks[idx] = clipped
            tail_used += len(clipped)
            break
        else:
            break

    # 4. Reorder in ascending order and insert gap markers where indices were omitted.
    ordered_parts: list[str] = []
    prev_pos: int | None = None
    for pos, idx in enumerate(valid_indices):
        if idx not in selected_chunks:
            continue
        if prev_pos is None and pos > 0:
            ordered_parts.append(f"[... {pos} passos intermediários de ferramenta omitidos ...]")
        elif prev_pos is not None and pos - prev_pos > 1:
            omitted = pos - prev_pos - 1
            ordered_parts.append(f"[... {omitted} passos intermediários de ferramenta omitidos ...]")
        ordered_parts.append(selected_chunks[idx])
        prev_pos = pos

    if prev_pos is not None and prev_pos < len(valid_indices) - 1:
        trailing = len(valid_indices) - 1 - prev_pos
        ordered_parts.append(f"[... {trailing} passos intermediários de ferramenta omitidos ...]")

    return "\n".join(ordered_parts)


def shrink_compaction_source_for_retry(source: str) -> str:
    """Halve the compaction source on context-length overflow while keeping Head and Tail."""
    if len(source) <= 400:
        return source
    marker = "\n[... passos intermediários de ferramenta reduzidos pela metade após overflow de contexto ...]\n"
    target_budget = max(200, (len(source) // 2) - len(marker))
    head_len = max(100, int(target_budget * 0.50))
    tail_len = max(100, target_budget - head_len)
    return f"{source[:head_len]}{marker}{source[-tail_len:]}"


def _extract_tool_text(
    message: Mapping[str, object],
    *,
    block_index: int | None = None,
    tool_use_id: str | None = None,
) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if tool_use_id:
            for block in content:
                if (
                    isinstance(block, Mapping)
                    and block.get("type") == "tool_result"
                    and str(block.get("tool_use_id") or block.get("tool_call_id") or "") == tool_use_id
                ):
                    return str(block.get("content") or "")
        if block_index is not None and 0 <= block_index < len(content):
            block = content[block_index]
            if isinstance(block, Mapping) and block.get("type") == "tool_result":
                return str(block.get("content") or "")
        parts: list[str] = []
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "tool_result":
                parts.append(str(block.get("content") or ""))
        return "\n".join(parts)
    return ""


def _replace_tool_text(
    message: dict[str, object],
    replacement: str,
    *,
    block_index: int | None = None,
    tool_use_id: str | None = None,
) -> bool:
    content = message.get("content")
    if isinstance(content, str):
        if content == replacement:
            return False
        message["content"] = replacement
        message["_content_shrunk"] = True
        return True
    if isinstance(content, list):
        changed = False
        blocks: list[object] = []
        for idx, block in enumerate(content):
            if isinstance(block, Mapping) and block.get("type") == "tool_result":
                b_id = str(block.get("tool_use_id") or block.get("tool_call_id") or "")
                if tool_use_id:
                    matches_target = b_id == tool_use_id
                elif block_index is not None:
                    matches_target = idx == block_index
                else:
                    matches_target = True
                if matches_target and str(block.get("content") or "") != replacement:
                    blocks.append({**block, "content": replacement})
                    changed = True
                else:
                    blocks.append(block)
            else:
                blocks.append(block)
        if changed:
            message["content"] = blocks
            message["_content_shrunk"] = True
            return True
    return False


def _norm_key_path(raw_path: object) -> str:
    if not isinstance(raw_path, str):
        return ""
    cleaned = raw_path.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def _record_tool_result_event(
    per_path_events: dict[str, list[dict[str, object]]],
    *,
    msg_idx: int,
    block_index: int | None,
    call_id: str,
    text_now: str,
    inferred_name: str,
    inferred_args: Mapping[str, object],
    raw_mutated_path: object,
    raw_rk: object,
    raw_read_path: object,
    has_read_full_key: bool,
    raw_read_full: object,
    raw_read_sha: object,
) -> None:
    mutated_path = _norm_key_path(raw_mutated_path)
    if not mutated_path and inferred_name in {"edit_file", "write_file", "Edit", "Write"}:
        if not text_now.lower().startswith("error") and "failed" not in text_now.lower()[:80]:
            mutated_path = _norm_key_path(inferred_args.get("path") or inferred_args.get("file_path"))

    if mutated_path:
        per_path_events.setdefault(mutated_path, []).append(
            {
                "index": msg_idx,
                "block_index": block_index,
                "tool_use_id": call_id,
                "kind": "mutate",
                "path": mutated_path,
            }
        )
        return

    read_path = _norm_key_path(raw_read_path)
    if isinstance(raw_rk, (tuple, list)) and len(raw_rk) >= 1:
        rk_path = _norm_key_path(raw_rk[0]) or read_path
        rk_offset = raw_rk[1] if len(raw_rk) > 1 else 1
        rk_limit = raw_rk[2] if len(raw_rk) > 2 else None
        read_key = (rk_path, rk_offset, rk_limit)
        read_path = rk_path
    elif read_path or inferred_name in {"read_file", "Read"}:
        rk_path = read_path or _norm_key_path(inferred_args.get("path") or inferred_args.get("file_path"))
        if not rk_path:
            return
        rk_offset = inferred_args.get("offset", 1)
        rk_limit = inferred_args.get("limit", None)
        read_key = (rk_path, rk_offset, rk_limit)
        read_path = rk_path
    else:
        return

    if not read_path:
        return

    if has_read_full_key:
        read_full = bool(raw_read_full)
    else:
        read_full = bool(
            inferred_name in {"read_file", "Read"}
            and inferred_args.get("offset") in (None, 1)
            and inferred_args.get("limit") is None
        )

    per_path_events.setdefault(read_path, []).append(
        {
            "index": msg_idx,
            "block_index": block_index,
            "tool_use_id": call_id,
            "kind": "read",
            "path": read_path,
            "read_key": read_key,
            "read_full": read_full,
            "read_sha": raw_read_sha,
        }
    )


def dedupe_stale_reads_in_messages(
    messages: list[dict[str, object]],
    *,
    protected_indices: set[int],
    last_call: int,
    min_chars: int = 200,
) -> int:
    """Elide duplicate or post-mutation stale read_file results before ``last_call``."""
    cutoff = last_call if last_call >= 0 else len(messages)
    if cutoff <= 0:
        return 0

    call_meta: dict[str, tuple[str, dict[str, object]]] = {}
    for idx in range(min(cutoff, len(messages))):
        msg = messages[idx]
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or ():
            if isinstance(call, Mapping):
                c_id = str(call.get("id") or "")
                fn = call.get("function") if isinstance(call.get("function"), Mapping) else {}
                c_name = str(fn.get("name") or "")
                raw_args = fn.get("arguments")
                parsed: dict[str, object] = {}
                if isinstance(raw_args, str):
                    try:
                        val = json.loads(raw_args)
                        if isinstance(val, dict):
                            parsed = val
                    except (TypeError, ValueError):
                        parsed = {}
                elif isinstance(raw_args, Mapping):
                    parsed = dict(raw_args)
                if c_id and c_name:
                    call_meta[c_id] = (c_name, parsed)
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, Mapping) and block.get("type") == "tool_use":
                    c_id = str(block.get("id") or "")
                    c_name = str(block.get("name") or "")
                    inp = block.get("input")
                    parsed = dict(inp) if isinstance(inp, Mapping) else {}
                    if c_id and c_name:
                        call_meta[c_id] = (c_name, parsed)

    per_path_events: dict[str, list[dict[str, object]]] = {}
    for idx in range(min(cutoff, len(messages))):
        msg = messages[idx]
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if (
            role == "user"
            and isinstance(content, list)
            and any(isinstance(b, Mapping) and b.get("type") == "tool_result" for b in content)
        ):
            tr_blocks = [
                (b_idx, b)
                for b_idx, b in enumerate(content)
                if isinstance(b, Mapping) and b.get("type") == "tool_result"
            ]
            single_block = len(tr_blocks) == 1
            for b_idx, block in tr_blocks:
                call_id = str(
                    block.get("tool_use_id")
                    or block.get("tool_call_id")
                    or (msg.get("tool_call_id") if single_block else "")
                    or ""
                )
                inferred_name, inferred_args = call_meta.get(call_id, ("", {}))
                has_rf = "_read_full" in block or (single_block and "_read_full" in msg)
                raw_rf = block.get("_read_full") if "_read_full" in block else (msg.get("_read_full") if single_block else None)
                _record_tool_result_event(
                    per_path_events,
                    msg_idx=idx,
                    block_index=b_idx,
                    call_id=call_id,
                    text_now=str(block.get("content") or ""),
                    inferred_name=inferred_name,
                    inferred_args=inferred_args,
                    raw_mutated_path=block.get("_mutated_path") or (msg.get("_mutated_path") if single_block else None),
                    raw_rk=block.get("_read_key") if "_read_key" in block else (msg.get("_read_key") if single_block else None),
                    raw_read_path=block.get("_read_path") or (msg.get("_read_path") if single_block else None),
                    has_read_full_key=has_rf,
                    raw_read_full=raw_rf,
                    raw_read_sha=block.get("_read_sha") or (msg.get("_read_sha") if single_block else None),
                )
            continue

        if role != "tool":
            continue

        call_id = str(msg.get("tool_call_id") or "")
        inferred_name, inferred_args = call_meta.get(call_id, ("", {}))
        _record_tool_result_event(
            per_path_events,
            msg_idx=idx,
            block_index=None,
            call_id=call_id,
            text_now=_extract_tool_text(msg),
            inferred_name=inferred_name,
            inferred_args=inferred_args,
            raw_mutated_path=msg.get("_mutated_path"),
            raw_rk=msg.get("_read_key"),
            raw_read_path=msg.get("_read_path"),
            has_read_full_key="_read_full" in msg,
            raw_read_full=msg.get("_read_full"),
            raw_read_sha=msg.get("_read_sha"),
        )

    elided = 0
    for norm_path, events in per_path_events.items():
        total_events = len(events)
        if total_events < 2:
            continue
        for pos, ev in enumerate(events):
            if ev.get("kind") != "read":
                continue
            msg_idx = int(ev["index"])
            if msg_idx in protected_indices or msg_idx >= cutoff:
                continue
            target_msg = messages[msg_idx]
            ev_block_idx = ev.get("block_index") if isinstance(ev.get("block_index"), int) else None
            ev_tool_use_id = str(ev.get("tool_use_id") or "") or None
            current_text = _extract_tool_text(
                target_msg,
                block_index=ev_block_idx,
                tool_use_id=ev_tool_use_id,
            )
            if (
                not current_text
                or current_text == MICROCOMPACT_MARKER
                or current_text.startswith("[duplicate read elided:")
                or current_text.startswith("[stale read elided:")
            ):
                continue

            later_events = events[pos + 1 :]
            # Check duplicate read before any intervening mutation (or covered by later read with same key/full)
            duplicate_later = False
            mutated_later = False
            for later in later_events:
                if later.get("kind") == "mutate":
                    mutated_later = True
                elif later.get("kind") == "read" and not mutated_later:
                    if later.get("read_full") is True or later.get("read_key") == ev.get("read_key"):
                        duplicate_later = True
                        break

            if duplicate_later:
                marker = f"[duplicate read elided: newer read of {norm_path} appears later in this conversation]"
                if _replace_tool_text(
                    target_msg,
                    marker,
                    block_index=ev_block_idx,
                    tool_use_id=ev_tool_use_id,
                ):
                    elided += 1
                continue

            if mutated_later and pos < total_events - 2 and len(current_text) > min_chars:
                marker = f"[stale read elided: {norm_path} was modified later in this conversation]"
                if _replace_tool_text(
                    target_msg,
                    marker,
                    block_index=ev_block_idx,
                    tool_use_id=ev_tool_use_id,
                ):
                    elided += 1

    return elided


__all__ = [
    "COMPACT_RETAINED_USER_TOKENS",
    "COMPACTION_HEADER",
    "COMPACTION_HEADING",
    "COMPACTION_INSTRUCTION",
    "COMPACTION_SECTIONS",
    "MAX_SINGLE_USER_REQUEST_CHARS",
    "MICROCOMPACT_MARKER",
    "RETAINED_USER_REQUESTS_HEADING",
    "_stringify_content",
    "_truncate_single_user_request",
    "attach_retained_user_requests",
    "build_compact_source_partitioned",
    "build_retained_user_requests_section",
    "compaction_message",
    "compress_tool_output_head_tail",
    "dedupe_stale_reads_in_messages",
    "extract_real_user_texts",
    "extract_retained_user_requests",
    "shrink_compaction_source_for_retry",
    "strip_retained_user_requests_section",
]

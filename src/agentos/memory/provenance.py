"""Runtime-verified memory provenance inference (WS-D4 / D-RF7).

The model can NEVER declare itself ``source_kind="user"`` via tool arguments.
Provenance is always derived by the runtime from the turn's actual state:
consolidator origin, explicit user memorization request in the current turn,
or untrusted external inputs read before saving (web, external/MCP tools, files).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Any

from .models_write import MemoryWriteContext, SourceKind

_MAX_SOURCE_REF_CHARS = 512

_FRIEND_MESSAGE_PREFIX = "Mensagem enviada pelo Orin Friend em nome do usuário:"
_TRAILING_SYSTEM_REMINDER_RE = re.compile(
    r"(?:\s*<system-reminder>[\s\S]*?</system-reminder>\s*)+$",
    re.IGNORECASE,
)

_WEB_TOOL_NAMES: frozenset[str] = frozenset({
    "fetch_url",
    "web_fetch",
    "webfetch",
    "web_search",
    "websearch",
    "search_web",
    "browse_page",
    "browser_navigate",
    "browser_open",
    "browser_click",
    "browser_extract",
})

_EXTERNAL_TOOL_NAMES: frozenset[str] = frozenset({
    "call_mcp_tool",
    "callmcptool",
    "read_mcp_resource",
    "readmcpresource",
    "read_mcp_resource_dir",
    "readmcpresourcedir",
    "inspect_mcp_server",
    "inspectmcpserver",
    "mcp",
    "inspect_plugin",
    "inspectplugin",
    "search_plugin",
    "searchplugin",
})

_SHELL_TOOL_NAMES: frozenset[str] = frozenset({
    "run_command",
    "bash",
    "powershell",
    "monitor",
})

_DOCUMENT_FILE_TOOL_NAMES: frozenset[str] = frozenset({
    "view_file",
    "viewfile",
    "transcribe_pdf",
    "transcribepdf",
})

_READ_FILE_TOOL_NAMES: frozenset[str] = frozenset({
    "read_file",
    "read",
})

_INSTRUCTION_FILENAMES: frozenset[str] = frozenset({
    "claude.md",
    "claude.local.md",
    "orin.md",
    "orin.local.md",
    "agents.md",
})

_NETWORK_COMMAND_RE = re.compile(
    r"(?i)(?:^|[\s;|&(])(?:curl|wget|invoke-webrequest|invoke-restmethod|iwr|irm|httpie)\b"
)

# Negated or interrogative/action phrases must NOT trigger explicit memorization intent.
_NEGATED_MEMORY_RE = re.compile(
    r"(?iu)\b(?:"
    r"(?:n[aã]o|nunca|jamais)\s+(?:consigo\s+|consegui\s+|me\s+|te\s+|posso\s+|quero\s+)?lembr\w*"
    r"|(?:don['’]?t|do\s+not|didn['’]?t|did\s+not|can['’]?t|cannot|couldn['’]?t|never)\s+remember\b"
    r"|(?:do|did|can|could|would|will)\s+you\s+(?:even\s+)?remember\b"
    r"|voc[eê]\s+(?:se\s+)?lembra\w*\b"
    r"|remember\s+to\s+(?!always\b|never\b|prefer\b|keep\b|save\b|store\b)\w+"
    r"|lembre(?:-se)?\s+de\s+(?!sempre\b|nunca\b|preferir\b|guardar\b|salvar\b|manter\b|anotar\b)\w+"
    r")"
)

_EXPLICIT_USER_MEMORY_RE = re.compile(
    r"(?iu)(?:"
    r"\blembre(?:-se)?(?:\s*[:,-]|\s+(?:que|disso|disto|de\s+(?:sempre|nunca|preferir|guardar|salvar|manter|anotar)|na\s+mem[oó]ria|em\s+mem[oó]ria)\b)"
    r"|\bmemoriz(?:e|ar)\b"
    r"|\b(?:guarde|guarda|guardar|salve|salva|salvar|grave|grava|gravar|anote|anota|anotar|registre|registra|registrar)\b"
    r".{0,48}\b(?:mem[oó]ria|prefer[eê]ncia|prefer[eê]ncias)\b"
    r"|\b(?:na|em)\s+(?:sua\s+)?mem[oó]ria\b"
    r"|\bremember(?:\s*[:,-]|\s+(?:that|this|my|our|i|we|always|never|to\s+(?:always|never|prefer|keep|save|store))\b)"
    r"|\b(?:save|store|keep|add|record|note|put)\b.{0,48}\b(?:memory|preference|preferences)\b"
    r"|\b(?:to|in)\s+(?:your\s+)?memory\b"
    r")"
)


@dataclass(frozen=True, slots=True)
class TurnProvenanceState:
    """Snapshot of the active turn used by the runtime to infer memory provenance."""

    user_message: str = ""
    tool_calls: Sequence[str | Mapping[str, Any]] = ()
    history: Sequence[Mapping[str, Any]] = ()
    has_external_tool_calls: bool = False
    read_files: Sequence[str] = ()
    workspace_root: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    actor: str | None = None
    agent_key: str | None = None
    is_consolidator: bool = False
    source_ref: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


def is_explicit_user_memory_request(user_message: str | None) -> bool:
    """Return True only when the user's current turn message explicitly asks to remember/save a fact."""
    raw = str(user_message or "").strip()
    if not raw:
        return False
    cleaned = _NEGATED_MEMORY_RE.sub(" ", raw)
    return bool(_EXPLICIT_USER_MEMORY_RE.search(cleaned))


def _field(state: Any, name: str, default: Any = None) -> Any:
    if isinstance(state, Mapping):
        return state.get(name, default)
    return getattr(state, name, default)


def _normalize_path_str(raw_path: str) -> str:
    return str(raw_path or "").strip().replace("\\", "/")


def _is_instruction_path(raw_path: str) -> bool:
    norm = _normalize_path_str(raw_path).lower()
    if not norm:
        return False
    name = norm.rsplit("/", 1)[-1]
    if name in _INSTRUCTION_FILENAMES:
        return True
    if "/.claude/rules/" in f"/{norm}" or "/.orin/rules/" in f"/{norm}":
        return True
    return False


def _is_absolute_path(raw_path: str) -> bool:
    s = str(raw_path or "").strip()
    if not s:
        return False
    if s.startswith("/") or s.startswith("\\"):
        return True
    if len(s) >= 2 and s[1] == ":" and s[0].isalpha():
        return True
    return False


def _is_outside_workspace(raw_path: str, workspace_root: str | None) -> bool:
    if not workspace_root or not _is_absolute_path(raw_path):
        return False
    norm_path = _normalize_path_str(raw_path)
    norm_root = _normalize_path_str(workspace_root).rstrip("/")
    if not norm_root:
        return False
    # Compare case-insensitively when Windows drive letters appear
    if (len(norm_path) >= 2 and norm_path[1] == ":") or (len(norm_root) >= 2 and norm_root[1] == ":"):
        try:
            p = PureWindowsPath(raw_path)
            r = PureWindowsPath(workspace_root)
            return str(p).lower() != str(r).lower() and not str(p).lower().startswith(str(r).lower().rstrip("\\") + "\\")
        except Exception:
            return False
    try:
        p_posix = PurePosixPath(norm_path)
        r_posix = PurePosixPath(norm_root)
        p_posix.relative_to(r_posix)
        return False
    except ValueError:
        return True


def _is_untrusted_file_path(raw_path: str, workspace_root: str | None) -> bool:
    norm = _normalize_path_str(raw_path)
    if not norm or _is_instruction_path(norm):
        return False
    lowered = norm.lower().lstrip("./")
    if (
        lowered.startswith(".orin/uploads/")
        or "/.orin/uploads/" in f"/{lowered}"
        or lowered.startswith("uploads/")
        or "/uploads/" in f"/{lowered}"
    ):
        return True
    if _is_outside_workspace(raw_path, workspace_root):
        return True
    return False


def _is_web_tool(name: str) -> bool:
    lowered = str(name or "").strip().lower()
    if not lowered:
        return False
    return lowered in _WEB_TOOL_NAMES or lowered.startswith("browser")


def _is_external_tool(name: str) -> bool:
    lowered = str(name or "").strip().lower()
    if not lowered:
        return False
    return lowered in _EXTERNAL_TOOL_NAMES or lowered.startswith("mcp__")


def _iter_tool_entries(turn_state: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    raw_calls = _field(turn_state, "tool_calls") or ()
    for item in raw_calls:
        if isinstance(item, str):
            if item.strip():
                entries.append({"tool_name": item.strip()})
        elif isinstance(item, Mapping):
            name = str(item.get("tool_name") or item.get("name") or "").strip()
            entry = dict(item)
            if name:
                entry["tool_name"] = name
            entries.append(entry)

    raw_ledger = _field(turn_state, "tool_ledger") or ()
    for item in raw_ledger:
        if isinstance(item, Mapping):
            name = str(item.get("tool_name") or item.get("name") or "").strip()
            entry = dict(item)
            if name:
                entry["tool_name"] = name
            entries.append(entry)

    raw_history = _field(turn_state, "history") or ()
    for item in raw_history:
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("source") or "").strip().lower()
        tainted = item.get("tainted") is True
        name = str(item.get("tool_name") or item.get("name") or "").strip()
        if name or source in {"web", "mcp", "tool", "file"} or tainted:
            entries.append({
                "tool_name": name,
                "source": source,
                "tainted": tainted,
                "url": item.get("url"),
                "path": item.get("path"),
            })
        tool_calls = item.get("tool_calls")
        if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, (str, bytes)):
            for tc in tool_calls:
                if isinstance(tc, Mapping):
                    fn = tc.get("function") if isinstance(tc.get("function"), Mapping) else tc
                    tc_name = str(fn.get("name") or tc.get("name") or "").strip()
                    if tc_name:
                        entry: dict[str, Any] = {"tool_name": tc_name}
                        raw_args = fn.get("arguments")
                        args_obj: Mapping[str, Any] | None = None
                        if isinstance(raw_args, Mapping):
                            args_obj = raw_args
                        elif isinstance(raw_args, str) and raw_args.strip():
                            try:
                                parsed = json.loads(raw_args)
                                if isinstance(parsed, Mapping):
                                    args_obj = parsed
                            except (TypeError, ValueError):
                                args_obj = None
                        if args_obj is not None:
                            for k in ("url", "query", "command"):
                                if args_obj.get(k):
                                    entry[k] = args_obj.get(k)
                            path_val = args_obj.get("path") or args_obj.get("file_path") or args_obj.get("notebook_path")
                            if path_val:
                                entry["path"] = path_val
                        entries.append(entry)
        content_blocks = item.get("content")
        if isinstance(content_blocks, Sequence) and not isinstance(content_blocks, (str, bytes)):
            for block in content_blocks:
                if isinstance(block, Mapping) and str(block.get("type") or "") == "tool_use":
                    b_name = str(block.get("name") or "").strip()
                    if b_name:
                        entry = {"tool_name": b_name}
                        b_input = block.get("input")
                        if isinstance(b_input, Mapping):
                            for k in ("url", "query", "command"):
                                if b_input.get(k):
                                    entry[k] = b_input.get(k)
                            path_val = b_input.get("path") or b_input.get("file_path") or b_input.get("notebook_path")
                            if path_val:
                                entry["path"] = path_val
                        entries.append(entry)
    return entries


def _bounded_ref(value: str | None) -> str | None:
    if not value:
        return None
    clean = " ".join(str(value).split()).strip()
    return clean[:_MAX_SOURCE_REF_CHARS] if clean else None


def infer_source_kind(turn_state: Any) -> tuple[SourceKind, int, str | None]:
    """Infer ``(source_kind, trust, source_ref)`` strictly from runtime turn state.

    Security invariant: any ``source_kind`` or ``trust`` attribute/key inside
    ``turn_state`` (as might be injected by a model) is completely ignored.
    """
    turn_id = str(_field(turn_state, "turn_id") or "").strip() or None
    conv_id = str(_field(turn_state, "conversation_id") or "").strip() or None
    explicit_ref = _bounded_ref(_field(turn_state, "source_ref"))
    default_ref = explicit_ref or (f"turn:{turn_id}" if turn_id else (f"conv:{conv_id}" if conv_id else None))

    # 1. Consolidation routine (internal runtime path)
    if bool(_field(turn_state, "is_consolidator", False)):
        ref = explicit_ref or (f"consolidator:{conv_id}" if conv_id else "consolidator")
        return ("consolidator", 1, _bounded_ref(ref))

    workspace_root = str(_field(turn_state, "workspace_root") or "").strip() or None
    entries = _iter_tool_entries(turn_state)

    # 2. Untrusted web inputs (WebFetch / WebSearch / browser) -> ("web", 0, ...)
    # Evaluated BEFORE explicit user intent so prompt injections read in the same turn
    # can NEVER bypass quarantine, user-memory protection, or tombstones.
    for entry in entries:
        tool_name = str(entry.get("tool_name") or "").strip()
        entry_source = str(entry.get("source") or "").strip().lower()
        if _is_web_tool(tool_name) or entry_source == "web":
            url = str(entry.get("url") or entry.get("query") or "").strip()
            label = tool_name or "web"
            ref = f"web:{label}:{url}" if url else f"web:{label}"
            return ("web", 0, _bounded_ref(ref))

    # 3. External / untrusted tools (MCP, plugins, network shell, tainted tool) -> ("tool", 0, ...)
    for entry in entries:
        tool_name = str(entry.get("tool_name") or "").strip()
        lowered_name = tool_name.lower()
        entry_source = str(entry.get("source") or "").strip().lower()
        if _is_external_tool(tool_name) or entry_source in {"mcp", "tool"}:
            return ("tool", 0, _bounded_ref(f"tool:{tool_name or entry_source}"))
        if lowered_name in _SHELL_TOOL_NAMES:
            cmd = str(entry.get("command") or "")
            if entry.get("tainted") is True or _NETWORK_COMMAND_RE.search(cmd):
                return ("tool", 0, _bounded_ref(f"tool:{tool_name}"))
        if entry.get("tainted") is True and lowered_name not in _READ_FILE_TOOL_NAMES and lowered_name not in _DOCUMENT_FILE_TOOL_NAMES:
            return ("tool", 0, _bounded_ref(f"tool:{tool_name or 'tainted'}"))

    if bool(_field(turn_state, "has_external_tool_calls", False)):
        ref = f"tool:external:{conv_id}" if conv_id else "tool:external"
        return ("tool", 0, _bounded_ref(ref))

    # 4. Untrusted external files (uploads, PDFs/docs via view_file/transcribe_pdf, files outside workspace) -> ("file", 0, ...)
    for entry in entries:
        tool_name = str(entry.get("tool_name") or "").strip()
        lowered_name = tool_name.lower()
        path = str(entry.get("path") or entry.get("file_path") or "").strip()
        entry_source = str(entry.get("source") or "").strip().lower()
        if entry_source == "file" or entry.get("untrusted_file") is True:
            return ("file", 0, _bounded_ref(f"file:{path or tool_name or 'external'}"))
        if lowered_name in _DOCUMENT_FILE_TOOL_NAMES:
            if not _is_instruction_path(path):
                return ("file", 0, _bounded_ref(f"file:{path or tool_name}"))
        if lowered_name in _READ_FILE_TOOL_NAMES:
            if entry.get("tainted") is True or _is_untrusted_file_path(path, workspace_root):
                return ("file", 0, _bounded_ref(f"file:{path or tool_name}"))

    for raw_file in (_field(turn_state, "read_files") or ()):
        path = str(raw_file or "").strip()
        if _is_untrusted_file_path(path, workspace_root):
            return ("file", 0, _bounded_ref(f"file:{path}"))

    # 5. Explicit user memorization request in a clean turn (no untrusted external input consumed)
    user_message = str(_field(turn_state, "user_message") or "")
    if is_explicit_user_memory_request(user_message):
        ref = default_ref or "user_prompt"
        return ("user", 2, _bounded_ref(ref))

    # 6. Default clean agent write
    return ("agent", 1, _bounded_ref(default_ref))


def build_write_context_from_turn(
    turn_state: Any,
    *,
    actor: str | None = None,
    agent_key: str | None = None,
) -> MemoryWriteContext:
    """Build a runtime-verified ``MemoryWriteContext`` from turn state."""
    source_kind, trust, source_ref = infer_source_kind(turn_state)
    resolved_agent_key = (
        (str(agent_key).strip() if agent_key and str(agent_key).strip() else None)
        or (str(_field(turn_state, "agent_key") or "").strip() or None)
    )
    resolved_actor = (
        (str(actor).strip() if actor and str(actor).strip() else None)
        or (str(_field(turn_state, "actor") or "").strip() or None)
        or (
            "user"
            if source_kind == "user"
            else (
                "consolidator"
                if source_kind == "consolidator"
                else (f"agent:{resolved_agent_key}" if resolved_agent_key else "agent")
            )
        )
    )
    conv_id = str(_field(turn_state, "conversation_id") or "").strip() or None
    turn_id = str(_field(turn_state, "turn_id") or "").strip() or None
    return MemoryWriteContext(
        source_kind=source_kind,
        source_ref=source_ref,
        actor=resolved_actor,
        agent_key=resolved_agent_key,
        conversation_id=conv_id,
        turn_id=turn_id,
        trust=trust,
    )


def _strip_trailing_system_reminders(text: str) -> str:
    """Remove trailing hook-appended ``<system-reminder>`` blocks for provenance matching."""
    return _TRAILING_SYSTEM_REMINDER_RE.sub("", text).strip()


def _starts_with_friend_prefix(text: str) -> bool:
    return text.lstrip().casefold().startswith(_FRIEND_MESSAGE_PREFIX.casefold())


def _is_friend_origin_turn(store: Any, turn: Mapping[str, Any], user_msg: str) -> bool:
    """Return True when the current turn was started or promoted by Orin Friend."""
    if str(turn.get("origin") or "").strip().lower() == "friend":
        return True
    idem = str(turn.get("idempotency_key") or "").strip()
    if idem.lower().startswith("friend:"):
        return True
    if idem.lower().startswith(("queue:", "defer:")):
        qid = idem.split(":", 1)[1].strip()
        if qid:
            lookup = getattr(store, "queued_inputs_by_ids", None)
            if callable(lookup):
                try:
                    found = lookup([qid])
                except Exception:  # noqa: BLE001
                    found = None
                rec: Any = None
                if isinstance(found, Mapping):
                    rec = found.get(qid)
                elif isinstance(found, Sequence) and not isinstance(found, (str, bytes)):
                    for item in found:
                        if isinstance(item, Mapping) and str(item.get("queue_id") or item.get("id") or "") == qid:
                            rec = item
                            break
                if isinstance(rec, Mapping) and str(rec.get("origin") or "").strip().lower() == "friend":
                    return True
    if user_msg and _starts_with_friend_prefix(_strip_trailing_system_reminders(user_msg)):
        return True
    return False


def _is_human_user_history_entry(entry: Any, non_user_queued_texts: frozenset[str]) -> bool:
    """Return True only for plain human user messages, rejecting tool_result lists and non-human injections."""
    if not isinstance(entry, Mapping):
        return False
    if entry.get("role") != "user":
        return False
    content = entry.get("content")
    if not isinstance(content, str):
        return False
    stripped = content.strip()
    if not stripped:
        return False
    if entry.get("is_tool_result") is True:
        return False
    origin = entry.get("origin")
    if origin is not None and str(origin).strip().lower() not in {"", "user"}:
        return False
    source = entry.get("source")
    if source is not None and str(source).strip().lower() in {"tool", "friend", "system", "subagent", "web", "mcp"}:
        return False
    if stripped.startswith("<system-reminder>") and stripped.endswith("</system-reminder>"):
        return False
    base_stripped = _strip_trailing_system_reminders(stripped)
    if not base_stripped:
        return False
    if _starts_with_friend_prefix(stripped) or _starts_with_friend_prefix(base_stripped):
        return False
    if stripped in non_user_queued_texts or base_stripped in non_user_queued_texts:
        return False
    return True


def _extract_current_turn_step_state(
    store: Any,
    conv_id: str | None,
    turn_id: str | None,
) -> tuple[list[dict[str, Any]], frozenset[str]]:
    """Extract tool calls and non-human queued input texts strictly for the active turn_id."""
    entries: list[dict[str, Any]] = []
    non_user_texts: set[str] = set()
    if not conv_id or not turn_id:
        return entries, frozenset()
    reader = getattr(store, "turn_steps", None)
    if not callable(reader):
        return entries, frozenset()
    try:
        grouped = reader(conv_id, turn_ids=[turn_id])
    except Exception:  # noqa: BLE001
        return entries, frozenset()
    steps = grouped.get(turn_id, []) if isinstance(grouped, Mapping) else []
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        kind = str(step.get("kind") or "")
        payload = step.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if kind == "queued_input":
            origin = str(payload.get("origin") or "user").strip().lower()
            if origin != "user" or payload.get("resume_reason"):
                text = str(payload.get("content") or payload.get("text") or "").strip()
                if text:
                    non_user_texts.add(text)
            continue
        if kind != "assistant_tool_call":
            continue
        calls = payload.get("calls")
        if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
            continue
        for call in calls:
            if not isinstance(call, Mapping):
                continue
            name = str(call.get("name") or "").strip()
            if not name:
                continue
            entry: dict[str, Any] = {"tool_name": name}
            raw_args = call.get("arguments")
            args_obj: Mapping[str, Any] | None = None
            if isinstance(raw_args, Mapping):
                args_obj = raw_args
            elif isinstance(raw_args, str) and raw_args.strip():
                try:
                    parsed = json.loads(raw_args)
                    if isinstance(parsed, Mapping):
                        args_obj = parsed
                except (TypeError, ValueError):
                    args_obj = None
            if args_obj is not None:
                for key in ("url", "query", "command"):
                    if args_obj.get(key):
                        entry[key] = args_obj.get(key)
                path_val = args_obj.get("path") or args_obj.get("file_path") or args_obj.get("notebook_path")
                if path_val:
                    entry["path"] = path_val
            entries.append(entry)
    return entries, frozenset(non_user_texts)


def session_memory_turn_state(session: Any) -> dict[str, Any]:
    """Extract runtime turn provenance state strictly scoped to the current turn."""
    store = getattr(session, "store", None)
    turn = getattr(session, "turn", None) or {}
    conv_id = str(turn.get("conversation_id") or "") or None
    turn_id = str(turn.get("turn_id") or "") or None

    turn_tool_calls, non_user_queued = _extract_current_turn_step_state(store, conv_id, turn_id)
    session_extra_tools = getattr(session, "_turn_external_tool_entries", None)
    if isinstance(session_extra_tools, Sequence) and not isinstance(session_extra_tools, (str, bytes)):
        turn_tool_calls.extend(item for item in session_extra_tools if isinstance(item, Mapping))

    reader = getattr(store, "history_for_turn", None)
    try:
        history = list(reader(turn)) if callable(reader) else []
    except Exception:  # noqa: BLE001
        history = []

    # Locate the last real human user message in history so we never mistake a provider
    # tool_result list or non-user queued input for the user's own prompt, and never let
    # earlier turns' tool calls contaminate the current turn.
    last_user_idx = -1
    history_user_msg = ""
    for idx in range(len(history) - 1, -1, -1):
        entry = history[idx]
        if _is_human_user_history_entry(entry, non_user_queued):
            last_user_idx = idx
            history_user_msg = str(entry.get("content") or "")
            break

    # Prefer the exact user message stored for this turn when the store exposes it.
    user_msg = ""
    user_msg_reader = getattr(store, "user_message_for_turn", None)
    if callable(user_msg_reader):
        try:
            direct_msg = user_msg_reader(turn)
            if isinstance(direct_msg, str) and direct_msg.strip():
                user_msg = direct_msg
        except Exception:  # noqa: BLE001
            pass
    if not user_msg:
        raw_turn_msg = turn.get("user_message")
        if isinstance(raw_turn_msg, str) and raw_turn_msg.strip():
            user_msg = raw_turn_msg
    if not user_msg:
        user_msg = history_user_msg
    if _is_friend_origin_turn(store, turn, user_msg):
        user_msg = ""

    # Only messages AFTER the last real user message belong to the current (resumed) turn.
    current_turn_history = history[last_user_idx + 1:] if last_user_idx >= 0 else []

    ws = getattr(session, "workspace", None)
    root = getattr(ws, "root", None) if ws is not None else None
    return {
        "user_message": user_msg,
        "tool_calls": turn_tool_calls,
        "history": current_turn_history,
        "has_external_tool_calls": False,
        "effective_settings": getattr(session, "effective_settings", None),
        "turn_id": turn_id,
        "conversation_id": conv_id,
        "workspace_root": str(root) if root is not None else None,
    }


__all__ = [
    "SourceKind",
    "TurnProvenanceState",
    "build_write_context_from_turn",
    "infer_source_kind",
    "is_explicit_user_memory_request",
    "session_memory_turn_state",
]

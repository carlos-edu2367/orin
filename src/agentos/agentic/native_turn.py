from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import logging
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence
from uuid import uuid4

from agentos.installation.paths import orin_paths
from agentos.providers.claude_cli.command import (
    TOOL_USE_ID_META_KEY,
    default_isolated_cwd,
    ensure_isolated_cwd,
)
from agentos.providers.claude_cli.errors import (
    CliSecurityAbort,
    NativeLoopUnavailable,
    ProviderTransportError,
    RateLimitState,
)
from agentos.providers.claude_cli.native_loop import (
    NATIVE_LOOP_VALIDATED,
    PERSISTENT_STDIN_OK,
    NativeTurnHandle,
    NativeTurnOutcome,
    NativeTurnRequest,
    native_workdir,
)
from agentos.providers.claude_cli.stream import CliInit, CliUsage
from agentos.providers.claude_cli.text_protocol import (
    TranscriptState,
    _extract_content_text_and_images,
)
from agentos.providers.cli_tools_mcp import (
    McpToolSpec,
    ToolCallRequest,
    ToolCallResult,
    ToolExecutor,
)

from . import phases, transcript
from .lifecycle import HookEvent
from .reminders import render_reminders, system_reminder
from .runtime import (
    CLOSING_INSTRUCTION,
    INTERRUPTED_TOOL_RESULT,
    MAX_STOP_CONTINUATIONS,
    VERIFY_REQUIRED_MESSAGE,
    AgenticRunResult,
    _SpeakState,
)
from .speak_stream import SpeakStreamParser

if TYPE_CHECKING:
    from .runtime import AgenticTurnRuntime

_LOGGER = logging.getLogger(__name__)

BATCH_GATHER_SECONDS: float = 0.1
CLI_PARALLEL_MCP: bool = True
CLI_PARALLEL_NEEDS_READONLY_HINT: bool = True
SESSION_BRANCH: str = "B"
NATIVE_CWD: Path = native_workdir("claude")


def _resolve_native_cwd(adapter_name: str = "claude") -> Path:
    """Orin's own workdir, unless it sits inside a git checkout (the development layout): then the CLI
    would walk up into the repository (CLAUDE.md, git), so it gets the per-user isolated folder instead."""
    import os

    workdir = native_workdir(adapter_name)
    try:
        resolved = workdir.resolve()
    except OSError:
        resolved = workdir
    slug = str(adapter_name or "claude").strip() or "claude"
    ancestors = resolved.parents if slug == "antigravity" else (resolved, *resolved.parents)
    if any((ancestor / ".git").exists() for ancestor in ancestors):
        base = default_isolated_cwd()
        if slug == "claude":
            return base.with_name(base.name + "-native")
        uid_suffix = base.name.removeprefix("orin-claude-cli-cwd")
        return base.with_name(f"orin-{slug}-cli-cwd{uid_suffix}-native")
    return workdir


@dataclass(frozen=True, slots=True)
class SessionPlan:
    session_id: str = ""
    resume: bool = False

    def cli_flags(self) -> tuple[str, ...]:
        return ()


def prompt_digest(system_prompt: str) -> str:
    return hashlib.sha256(str(system_prompt or "").encode("utf-8")).hexdigest()


def history_digest(messages: Sequence[Mapping[str, object]]) -> str:
    hasher = hashlib.sha256()
    for msg in messages:
        if not isinstance(msg, Mapping):
            continue
        if bool(msg.get("_reminder")):
            continue
        role = str(msg.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            continue
        if role == "assistant" and msg.get("tool_calls"):
            continue
        raw_content = msg.get("content")
        if isinstance(raw_content, Sequence) and not isinstance(raw_content, (str, bytes)):
            blocks = [b for b in raw_content if isinstance(b, Mapping)]
            if role == "assistant" and any(b.get("type") == "tool_use" for b in blocks):
                continue
            if role == "user" and blocks and all(b.get("type") == "tool_result" for b in blocks):
                continue
        text, _ = _extract_content_text_and_images(raw_content)
        if role == "user" and text.strip().startswith("<system-reminder>") and text.strip().endswith("</system-reminder>"):
            continue
        entry = json.dumps([role, text], ensure_ascii=False)
        hasher.update(entry.encode("utf-8") + b"\n")
    return hasher.hexdigest()


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value or {}, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


_ORIN_MCP_PREFIX = "mcp__orin__"
# Limits the sink enforces from the CLI's usage; when one fired, it is the turn's error code.
_LIMIT_ABORT_CODES = frozenset({"PROVIDER_TOKEN_LIMIT", "PROVIDER_COST_LIMIT", "MAX_BUDGET_USD", "MAX_TURNS"})


def _strip_mcp_prefix(name: str) -> str:
    """Drop only the CLI's prefix for Orin's own server; a user MCP tool (mcp__<slug>__<tool>) keeps its name."""
    raw = str(name or "").strip()
    if raw.startswith(_ORIN_MCP_PREFIX) and len(raw) > len(_ORIN_MCP_PREFIX):
        return raw[len(_ORIN_MCP_PREFIX):]
    return raw


def _convert_tool_images(raw_images: Any) -> tuple[Mapping[str, str], ...]:
    if not isinstance(raw_images, Sequence) or isinstance(raw_images, (str, bytes)):
        return ()
    converted: list[Mapping[str, str]] = []
    for item in raw_images:
        if not isinstance(item, Mapping):
            continue
        source = item.get("source")
        if isinstance(source, Mapping) and source.get("data"):
            data = str(source["data"])
            mime = str(
                source.get("media_type")
                or source.get("mime_type")
                or item.get("mime_type")
                or "image/png"
            )
            converted.append({"data": data, "mime_type": mime})
        elif item.get("data"):
            data = str(item["data"])
            mime = str(
                item.get("mime_type")
                or item.get("media_type")
                or "image/png"
            )
            converted.append({"data": data, "mime_type": mime})
    return tuple(converted)


@dataclass(slots=True)
class _BatchEntry:
    call_id: str
    internal_name: str
    arguments: dict[str, Any]
    message_id: str
    read_only: bool
    done: threading.Event = field(default_factory=threading.Event)
    result: ToolCallResult | None = None
    error: BaseException | None = None


class _OpenReadBatch:
    def __init__(self, message_id: str, deadline: float) -> None:
        self.message_id = message_id
        self.deadline = deadline
        self.entries: list[_BatchEntry] = []
        self.closed = False
        self.cond = threading.Condition()


class ToolBatchCollector:
    """Gathers concurrent read-only MCP calls from the same assistant message into one `_run_toolset` batch."""

    def __init__(
        self,
        run_batch: Callable[[list[_BatchEntry], str], list[ToolCallResult]],
        *,
        gather_seconds: float = BATCH_GATHER_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._run_batch = run_batch
        self._gather_seconds = max(0.0, float(gather_seconds))
        self._clock = clock
        self._state_lock = threading.Lock()
        self._exec_lock = threading.Lock()
        self._open_batches: dict[str, _OpenReadBatch] = {}
        self._in_flight_reads: dict[str, list[_BatchEntry]] = {}

    def submit(
        self,
        call_id: str,
        internal_name: str,
        arguments: Mapping[str, Any],
        *,
        message_id: str = "default",
        read_only: bool = False,
    ) -> ToolCallResult:
        msg_key = str(message_id or "default")
        entry = _BatchEntry(
            call_id=str(call_id),
            internal_name=str(internal_name),
            arguments=dict(arguments or {}),
            message_id=msg_key,
            read_only=bool(read_only),
        )

        if not entry.read_only:
            with self._state_lock:
                open_batch = self._open_batches.pop(msg_key, None)
                if open_batch is not None and not open_batch.closed:
                    open_batch.closed = True
                    with open_batch.cond:
                        open_batch.cond.notify_all()
                prior_entries = list(self._in_flight_reads.get(msg_key, ()))
            for prior in prior_entries:
                prior.done.wait()

            with self._exec_lock:
                results = self._run_batch([entry], msg_key)
            if not results:
                return ToolCallResult(text="", is_error=True)
            return results[0]

        with self._state_lock:
            now = self._clock()
            self._in_flight_reads.setdefault(msg_key, []).append(entry)
            batch = self._open_batches.get(msg_key)
            if batch is not None and not batch.closed:
                batch.entries.append(entry)
                is_leader = False
            else:
                batch = _OpenReadBatch(msg_key, now + self._gather_seconds)
                batch.entries.append(entry)
                self._open_batches[msg_key] = batch
                is_leader = True

        if not is_leader:
            entry.done.wait()
            if entry.error is not None:
                raise entry.error
            assert entry.result is not None
            return entry.result

        with batch.cond:
            while not batch.closed:
                rem = batch.deadline - self._clock()
                if rem <= 0:
                    break
                batch.cond.wait(timeout=rem)

        with self._state_lock:
            batch.closed = True
            if self._open_batches.get(msg_key) is batch:
                del self._open_batches[msg_key]
            entries = list(batch.entries)

        try:
            with self._exec_lock:
                results = self._run_batch(entries, msg_key)
            if len(results) != len(entries):
                raise RuntimeError(
                    f"ToolBatchCollector expected {len(entries)} results, got {len(results)}"
                )
            for ent, res in zip(entries, results):
                ent.result = res
        except BaseException as exc:
            for ent in entries:
                ent.error = exc
        finally:
            with self._state_lock:
                in_flight = self._in_flight_reads.get(msg_key)
                if in_flight is not None:
                    for ent in entries:
                        try:
                            in_flight.remove(ent)
                        except ValueError:
                            pass
                    if not in_flight:
                        del self._in_flight_reads[msg_key]
            for ent in entries:
                ent.done.set()

        if entry.error is not None:
            raise entry.error
        assert entry.result is not None
        return entry.result


class RuntimeToolExecutor(ToolExecutor):
    """Bridges MCP `tools/list` and `tools/call` into `AgenticTurnRuntime._run_toolset`."""

    def __init__(
        self,
        runtime: AgenticTurnRuntime,
        turn: dict[str, object],
        *,
        tool_schemas: list[dict[str, object]] | None = None,
        correlate: Callable[[str, Mapping[str, Any], Mapping[str, Any]], str] | None = None,
        messages: list[dict[str, object]] | None = None,
        gather_seconds: float = BATCH_GATHER_SECONDS,
        deadline: datetime | float | None = None,
        allow_deferral: bool = True,
    ) -> None:
        self.runtime = runtime
        self.turn = turn
        self._allow_deferral = bool(allow_deferral)
        self._explicit_tool_schemas: list[dict[str, object]] | None = (
            list(tool_schemas) if tool_schemas is not None else None
        )
        self._last_discovered_snapshot: tuple[frozenset[str], frozenset[str]] = (
            self._snapshot_discovered()
        )
        if tool_schemas is None:
            tool_schemas = self._compute_live_schemas()
        self._tool_schemas = list(tool_schemas)
        self._custom_correlate = correlate
        self.messages: list[dict[str, object]] = messages if messages is not None else []
        self.deadline: datetime | float | None = deadline
        self.abort_code: str | None = None
        self._deferred_queued_records: list[tuple[str, str]] | None = None
        self.stop_reason: str | None = None
        self.action_count: int = 0
        self.tool_rounds: int = 0
        self.handle: NativeTurnHandle | Any | None = None
        self.speak_state: _SpeakState | None = None
        self.on_batch_finished: Callable[[list[dict[str, object]]], None] | None = None

        self._corr_lock = threading.Lock()
        self._pending_tool_uses: list[tuple[str, str, str, str]] = []
        self._tool_use_message_id: dict[str, str] = {}
        self._current_message_id: str = "msg_1"
        self._msg_seq: int = 1
        self._closed = threading.Event()
        self._first_read_keys_by_message_id: dict[str, set[str]] = {}
        self._seen_tool_round_message_ids: set[str] = set()
        self.consume_round_buffers: Callable[[], tuple[list[str], list[str]]] | None = None

        self._specs: list[McpToolSpec] = []
        self._valid_public_names: set[str] = set()
        self._valid_internal_names: set[str] = set()
        self._build_specs()

        self.collector = ToolBatchCollector(
            self._execute_batch,
            gather_seconds=gather_seconds,
        )

    def close(self) -> None:
        self._closed.set()

    def is_known_tool(self, name: str) -> bool:
        raw_name = _strip_mcp_prefix(name)
        if not raw_name:
            return False
        internal_name = self._resolve_internal_name(raw_name)
        toolset = self.runtime.toolset
        active_mcp = set(getattr(toolset, "active_mcp_tool_names", ()) or ())
        deferred = set(getattr(toolset, "deferred_tool_names", ()) or ())
        discovered = set(getattr(toolset, "discovered_tool_names", ()) or ())
        return (
            raw_name in self._valid_public_names
            or internal_name in self._valid_internal_names
            or internal_name in active_mcp
            or internal_name in deferred
            or internal_name in discovered
        )

    def _resolve_internal_name(self, name: str) -> str:
        cleaned = _strip_mcp_prefix(name)
        resolved = cleaned
        if self.runtime._names is not None:
            resolved = self.runtime._names.name_in(resolved)
        key_for = getattr(self.runtime.toolset, "key_for", None)
        if callable(key_for):
            resolved = key_for(resolved)
        return str(resolved)

    def _is_read_only(self, public_name: str, internal_name: str) -> bool:
        is_ro = getattr(self.runtime.toolset, "is_read_only", None)
        if not callable(is_ro):
            return False
        try:
            if is_ro(internal_name):
                return True
            if public_name != internal_name and is_ro(public_name):
                return True
        except Exception:
            pass
        return False

    def _build_specs(self) -> None:
        specs: list[McpToolSpec] = []
        pub_names: set[str] = set()
        int_names: set[str] = set()
        for item in self._tool_schemas:
            if not isinstance(item, Mapping):
                continue
            fn = item.get("function") if isinstance(item.get("function"), Mapping) else item
            pub_name = str(fn.get("name") or "").strip()
            if not pub_name:
                continue
            desc = str(fn.get("description") or "").strip()
            params = (
                dict(fn["parameters"])
                if isinstance(fn.get("parameters"), Mapping)
                else (
                    dict(fn["input_schema"])
                    if isinstance(fn.get("input_schema"), Mapping)
                    else {"type": "object", "properties": {}}
                )
            )
            int_name = self._resolve_internal_name(pub_name)
            ro = self._is_read_only(pub_name, int_name)
            specs.append(
                McpToolSpec(
                    name=pub_name,
                    description=desc,
                    input_schema=params,
                    read_only=ro,
                )
            )
            pub_names.add(pub_name)
            int_names.add(int_name)
        self._specs = specs
        self._valid_public_names = pub_names
        self._valid_internal_names = int_names

    def _snapshot_discovered(self) -> tuple[frozenset[str], frozenset[str]]:
        toolset = getattr(self.runtime, "toolset", None)
        discovered = frozenset(getattr(toolset, "discovered_tool_names", ()) or ())
        active_mcp = frozenset(getattr(toolset, "active_mcp_tool_names", ()) or ())
        return discovered, active_mcp

    def _compute_live_schemas(self) -> list[dict[str, object]]:
        raw_schemas = self.runtime._tool_schemas(
            self.turn, allow_deferral=self._allow_deferral
        )
        if self.runtime.phases is not None:
            try:
                all_raw = list(self.runtime.toolset.schemas())
            except Exception:
                all_raw = list(raw_schemas)
            seen_names = {
                str(
                    (s.get("function") if isinstance(s.get("function"), Mapping) else s).get(
                        "name", ""
                    )
                )
                for s in all_raw
                if isinstance(s, Mapping)
            }
            for s in raw_schemas:
                if isinstance(s, Mapping):
                    fn = s.get("function") if isinstance(s.get("function"), Mapping) else s
                    nm = str(fn.get("name", ""))
                    if nm and nm not in seen_names:
                        all_raw.append(s)
                        seen_names.add(nm)
            if self._explicit_tool_schemas is not None:
                for s in self._explicit_tool_schemas:
                    if isinstance(s, Mapping):
                        fn = s.get("function") if isinstance(s.get("function"), Mapping) else s
                        nm = str(fn.get("name", ""))
                        if nm and nm not in seen_names:
                            all_raw.append(s)
                            seen_names.add(nm)
            raw_schemas = all_raw
        return (
            self.runtime._names.schemas_out(raw_schemas)
            if self.runtime._names is not None
            else list(raw_schemas)
        )

    def list_tools(self) -> Sequence[McpToolSpec]:
        if (
            getattr(self.runtime, "toolset", None) is not None
            and (
                (self._explicit_tool_schemas is None and self.runtime.phases is None)
                or self._snapshot_discovered() != self._last_discovered_snapshot
            )
        ):
            self._tool_schemas = self._compute_live_schemas()
            self._last_discovered_snapshot = self._snapshot_discovered()
            self._build_specs()
        return list(self._specs)

    def note_message_id(self, message_id: str | None) -> None:
        if message_id and str(message_id).strip():
            with self._corr_lock:
                self._current_message_id = str(message_id).strip()

    def register_tool_use_started(
        self,
        tool_use_id: str,
        tool_name: str,
        *,
        message_id: str | None = None,
    ) -> None:
        with self._corr_lock:
            msg_id = (
                str(message_id).strip()
                if message_id and str(message_id).strip()
                else self._current_message_id
            )
            if tool_use_id:
                self._tool_use_message_id[str(tool_use_id)] = msg_id

    def register_tool_use_complete(
        self,
        tool_use_id: str,
        tool_name: str,
        tool_input: Mapping[str, Any],
        *,
        message_id: str | None = None,
    ) -> None:
        int_name = self._resolve_internal_name(tool_name)
        canon_args = _canonical_json(tool_input)
        with self._corr_lock:
            msg_id = (
                str(message_id).strip()
                if message_id and str(message_id).strip()
                else self._tool_use_message_id.get(str(tool_use_id), self._current_message_id)
            )
            if tool_use_id:
                self._tool_use_message_id[str(tool_use_id)] = msg_id
                self._pending_tool_uses.append(
                    (str(tool_use_id), int_name, canon_args, msg_id)
                )

    def _default_correlate(
        self,
        public_name: str,
        arguments: Mapping[str, Any],
        meta: Mapping[str, Any],
    ) -> str:
        meta_id = meta.get(TOOL_USE_ID_META_KEY) if isinstance(meta, Mapping) else None
        if isinstance(meta_id, str) and meta_id.strip():
            cid = meta_id.strip()
            with self._corr_lock:
                for idx, (pid, _, _, _) in enumerate(self._pending_tool_uses):
                    if pid == cid:
                        self._pending_tool_uses.pop(idx)
                        break
            return cid

        int_name = self._resolve_internal_name(public_name)
        canon_args = _canonical_json(arguments)
        with self._corr_lock:
            for idx, (pid, pname, pargs, _) in enumerate(self._pending_tool_uses):
                if pname == int_name and pargs == canon_args:
                    self._pending_tool_uses.pop(idx)
                    return pid
            for idx, (pid, pname, _, _) in enumerate(self._pending_tool_uses):
                if pname == int_name:
                    self._pending_tool_uses.pop(idx)
                    return pid
        return f"mcp_{uuid4().hex[:12]}"

    def correlate(
        self,
        public_name: str,
        arguments: Mapping[str, Any],
        meta: Mapping[str, Any],
    ) -> str:
        if self._custom_correlate is not None:
            return str(self._custom_correlate(public_name, arguments, meta))
        return self._default_correlate(public_name, arguments, meta)

    def call_tool(self, request: ToolCallRequest) -> ToolCallResult:
        if self.abort_code is not None:
            return ToolCallResult(
                text=f"turn stopped: {self.abort_code}",
                is_error=True,
            )

        raw_name = _strip_mcp_prefix(request.name)
        internal_name = self._resolve_internal_name(raw_name)
        toolset = self.runtime.toolset
        active_mcp = set(getattr(toolset, "active_mcp_tool_names", ()) or ())
        deferred = set(getattr(toolset, "deferred_tool_names", ()) or ())
        discovered = set(getattr(toolset, "discovered_tool_names", ()) or ())

        if (
            internal_name not in self._valid_internal_names
            and raw_name not in self._valid_public_names
            and internal_name not in active_mcp
            and internal_name not in deferred
            and internal_name not in discovered
        ):
            valid_list = ", ".join(sorted(self._valid_public_names))
            return ToolCallResult(
                text=f"Unknown tool '{request.name}'. Valid tools: {valid_list}",
                is_error=True,
            )

        if self.runtime.phases is not None:
            phase_schemas = self.runtime._tool_schemas(self.turn)
            phase_allowed: set[str] = set()
            for s in phase_schemas:
                if isinstance(s, Mapping):
                    fn = s.get("function") if isinstance(s.get("function"), Mapping) else s
                    sname = str(fn.get("name") or "").strip()
                    if sname:
                        phase_allowed.add(self._resolve_internal_name(sname))
            if internal_name not in phase_allowed and (
                self.runtime.phases.current == phases.Phase.VERIFY
                or internal_name not in active_mcp
            ):
                allowed_str = ", ".join(sorted(phase_allowed)) or "none"
                return ToolCallResult(
                    text=(
                        f"Tool '{request.name}' is not allowed in phase "
                        f"'{self.runtime.phases.current.value}'. "
                        f"Allowed tools in this phase: {allowed_str}"
                        + phases.toolkit_hint(internal_name, self.runtime.phases.current)
                    ),
                    is_error=True,
                )

        call_id = self.correlate(raw_name, request.arguments, request.meta)
        with self._corr_lock:
            meta_msg = (
                request.meta.get("message_id")
                or request.meta.get("claudecode/messageId")
                if isinstance(request.meta, Mapping)
                else None
            )
            if isinstance(meta_msg, str) and meta_msg.strip():
                message_id = meta_msg.strip()
            else:
                message_id = self._tool_use_message_id.get(
                    call_id, self._current_message_id
                )

        read_only = self._is_read_only(raw_name, internal_name)
        return self.collector.submit(
            call_id,
            internal_name,
            request.arguments,
            message_id=message_id,
            read_only=read_only,
        )

    def _deadline_exceeded(self) -> bool:
        if self.deadline is None:
            return False
        try:
            now = self.runtime.clock()
        except Exception:
            return False
        if isinstance(now, (int, float)) and isinstance(self.deadline, (int, float)):
            return float(now) >= float(self.deadline)
        if isinstance(now, datetime) and isinstance(self.deadline, datetime):
            return now >= self.deadline
        if isinstance(self.deadline, (int, float)):
            return time.monotonic() >= float(self.deadline)
        return False

    def _claim_queued_texts(self) -> list[str]:
        claim = getattr(self.runtime.store, "claim_queued_inputs", None)
        if not callable(claim):
            return []
        try:
            items = list(claim(self.turn) or ())
        except Exception:  # noqa: BLE001
            return []
        claimed: list[str] = []
        for item in items:
            text = str((item or {}).get("content") or "").strip()
            queue_id = str((item or {}).get("queue_id") or "")
            if not text:
                continue
            decision = self.runtime._emit(HookEvent.USER_PROMPT_SUBMIT, prompt=text)
            if decision is not None and (decision.blocked or decision.stop):
                marker = getattr(self.runtime.store, "mark_queued_input_blocked", None)
                if callable(marker):
                    try:
                        marker(queue_id)
                    except Exception:  # noqa: BLE001
                        pass
                reason = decision.reason or decision.stop_reason or "blocked by hook"
                self.runtime.store.delta(
                    self.turn,
                    f"\n\n⚠️ Mensagem da fila bloqueada por um hook: {reason}\n\n",
                )
                continue
            extra = [
                entry
                for entry in (decision.additional_context if decision is not None else [])
                if entry
            ]
            content = (
                f"{text}\n\n{system_reminder(chr(10).join(extra))}"
                if extra
                else text
            )
            if self._deferred_queued_records is not None:
                # Claimed while an ask_user batch is open: recorded after that batch's tool call and results,
                # so the transcript never shows the answer before the question.
                self._deferred_queued_records.append((content, text))
            else:
                self._record_queued_input(content, text)
            self.runtime._life(
                self.turn,
                "queued_input",
                queued_text=text[:2000],
                queue_id=queue_id,
            )
            claimed.append(content)
        return claimed

    def _record_queued_input(self, content: str, text: str) -> None:
        self.messages.append({"role": "user", "content": content})
        self.runtime._queued_turn_count += 1
        self.runtime._record_step(
            self.turn,
            kind=transcript.STEP_QUEUED_INPUT,
            payload={"content": text},
        )

    def _turn_notes(self, *, claim_queue: bool = True) -> str:
        self.runtime._check_external_changes(self.turn)
        self.runtime._add_periodic_reminders()
        try:
            pending = self.runtime.reminders.drain()
        except Exception:  # noqa: BLE001
            pending = []
        reminder_body = render_reminders(pending)
        queued_texts = self._claim_queued_texts() if claim_queue else []
        parts: list[str] = []
        phase_note = getattr(self.runtime, "_phase_transition_note", None)
        if phase_note:
            self.runtime._phase_transition_note = None
            self.messages.append({"role": "user", "content": phase_note})
            parts.append(str(phase_note))
        if reminder_body:
            parts.append(reminder_body)
        if queued_texts:
            parts.append(
                system_reminder(
                    "The user sent a message while you were working:\n"
                    + "\n\n".join(queued_texts)
                )
            )
        return "\n\n".join(parts)

    def _wait_for_answer(self, poll_seconds: float = 0.25) -> str | None:
        step = max(0.02, min(0.1, float(poll_seconds)))
        while True:
            if (
                self._closed.is_set()
                or self.runtime.cancelled(self.turn)
                or self._deadline_exceeded()
                or (
                    self.handle is not None
                    and callable(getattr(self.handle, "is_process_alive", None))
                    and not self.handle.is_process_alive()
                )
            ):
                return None
            claimed = self._claim_queued_texts()
            if claimed:
                return "\n\n".join(claimed)
            waited = 0.0
            while waited < poll_seconds:
                if (
                    self._closed.is_set()
                    or self.runtime.cancelled(self.turn)
                    or self._deadline_exceeded()
                    or (
                        self.handle is not None
                        and callable(getattr(self.handle, "is_process_alive", None))
                        and not self.handle.is_process_alive()
                    )
                ):
                    return None
                time.sleep(min(step, poll_seconds - waited))
                waited += step

    def _execute_batch(
        self,
        entries: list[_BatchEntry],
        message_id: str,
    ) -> list[ToolCallResult]:
        if self.abort_code is not None:
            return [
                ToolCallResult(text=f"turn stopped: {self.abort_code}", is_error=True)
                for _ in entries
            ]

        max_actions = self.runtime.limits.max_actions
        if max_actions is not None and self.action_count + len(entries) > max_actions:
            self.abort_code = "ACTION_LIMIT"
            if self.handle is not None:
                try:
                    self.handle.refuse_tools("turn stopped: ACTION_LIMIT")
                except Exception:
                    pass
            return [
                ToolCallResult(text="turn stopped: ACTION_LIMIT", is_error=True)
                for _ in entries
            ]

        if self._deferred_queued_records is not None:  # an earlier batch broke off before recording them
            leftover, self._deferred_queued_records = self._deferred_queued_records, None
            for content, text in leftover:
                self._record_queued_input(content, text)
        self.runtime._life(self.turn, "waiting_tool", count=len(entries))

        calls_dict: dict[str, dict[str, str]] = {}
        raw_calls: list[dict[str, str]] = []
        for ent in entries:
            arg_json = json.dumps(ent.arguments, ensure_ascii=False, default=str)
            call_obj = {
                "id": ent.call_id,
                "name": ent.internal_name,
                "arguments": arg_json,
            }
            calls_dict[ent.call_id] = call_obj
            raw_calls.append(call_obj)

        msg_first_reads = self._first_read_keys_by_message_id.setdefault(
            str(message_id or "default"), set()
        )
        before_discovered = self._snapshot_discovered()
        self.runtime._native_message_first_read_keys = msg_first_reads
        try:
            results = self.runtime._run_toolset(self.turn, raw_calls)
        finally:
            self.runtime._native_message_first_read_keys = None
        after_discovered = self._snapshot_discovered()
        tools_changed = after_discovered != before_discovered
        if tools_changed:
            self._tool_schemas = self._compute_live_schemas()
            self._last_discovered_snapshot = after_discovered
            self._build_specs()

        self.action_count += len(results)
        msg_round_key = str(message_id or "").strip()
        if not msg_round_key or msg_round_key == "default":
            self.tool_rounds += 1
        elif msg_round_key not in self._seen_tool_round_message_ids:
            self._seen_tool_round_message_ids.add(msg_round_key)
            self.tool_rounds += 1

        prev_phase = (
            self.runtime.phases.current if self.runtime.phases is not None else None
        )
        self.runtime._advance_phase(self.turn, results)
        if (
            self.runtime.phases is not None
            and self.runtime.phases.current != prev_phase
            and self.runtime.phases.current != phases.Phase.RESPOND
            and not getattr(self.runtime, "_phase_transition_note", None)
        ):
            self.runtime._phase_transition_note = phases.PHASE_INSTRUCTIONS[
                self.runtime.phases.current
            ]

        ask_user_cancelled = False
        had_ask_user_wait = False
        pre_ask_user_queued: list[str] = []
        if any(bool(res.get("wait_for_user")) and str(res.get("name") or "") == "ask_user" for res in results):
            self._deferred_queued_records = []
        for res in results:
            if bool(res.get("wait_for_user")) and str(res.get("name") or "") == "ask_user":
                had_ask_user_wait = True
                pre_claimed = self._claim_queued_texts()
                direct_answers = [
                    item
                    for item in pre_claimed
                    if item.lstrip().startswith("Respostas às perguntas do agente:")
                ]
                for item in pre_claimed:
                    if not item.lstrip().startswith("Respostas às perguntas do agente:"):
                        pre_ask_user_queued.append(item)
                self.runtime._life(self.turn, "waiting_user_in_turn")
                answer = (
                    "\n\n".join(direct_answers)
                    if direct_answers
                    else self._wait_for_answer()
                )
                if answer is None:
                    ask_user_cancelled = True
                    res["status"] = "failed"
                    res["content"] = INTERRUPTED_TOOL_RESULT
                    res["wait_for_user"] = False
                else:
                    res["status"] = "succeeded"
                    res["content"] = f"Resposta da pessoa:\n{answer}"
                    res["wait_for_user"] = False

        round_text_parts, round_thinking_parts = (
            self.consume_round_buffers()
            if callable(self.consume_round_buffers)
            else ([], [])
        )
        self.messages.append(
            self.runtime._assistant_tool_message(
                self.turn,
                round_text_parts,
                calls_dict,
                round_thinking_parts,
            )
        )
        self.runtime._record_step(
            self.turn,
            kind=transcript.STEP_ASSISTANT_TOOL_CALL,
            payload=transcript.assistant_tool_call_payload(
                "".join(round_text_parts),
                calls_dict.values(),
            ),
        )
        for res in results:
            self.runtime._record_step(
                self.turn,
                kind=transcript.STEP_TOOL_RESULT,
                payload=transcript.tool_result_payload(
                    call_id=str(res.get("id") or ""),
                    name=str(res.get("name") or ""),
                    status=str(res.get("status") or ""),
                    content=str(res.get("content") or ""),
                ),
                tool_name=str(res.get("name") or ""),
                tool_call_id=str(res.get("id") or ""),
            )
            self.messages.extend(self.runtime._tool_result_messages(self.turn, res))
        deferred_records, self._deferred_queued_records = self._deferred_queued_records, None
        for content, text in deferred_records or ():
            self._record_queued_input(content, text)

        if self.on_batch_finished is not None:
            try:
                self.on_batch_finished(results)
            except Exception:
                pass

        stop_reason = getattr(self.runtime.toolset, "stop_requested", None)
        if ask_user_cancelled:
            self.abort_code = "TURN_CANCELLED"
        elif stop_reason:
            self.stop_reason = str(stop_reason)
            self.abort_code = "HOOK_STOPPED"
        elif max(self.runtime._consecutive_tool_failures.values(), default=0) >= 3:
            self.abort_code = "REPEATED_TOOL_FAILURE"
        elif self.runtime.reconciliation_required(self.turn):
            self.abort_code = "EFFECT_RECONCILIATION_REQUIRED"
        elif max_actions is not None and self.action_count > max_actions:
            self.abort_code = "ACTION_LIMIT"
        elif (
            self.runtime.limits.safety_max_tool_rounds is not None
            and self.tool_rounds >= self.runtime.limits.safety_max_tool_rounds
        ):
            self.abort_code = "SAFETY_TOOL_ROUNDS"
        elif any(bool(r.get("wait_for_user")) for r in results):
            self.abort_code = "WAITING_USER"

        if self.abort_code is not None and self.handle is not None:
            try:
                self.handle.refuse_tools(f"turn stopped: {self.abort_code}")
            except Exception:
                pass

        trailing_notes = (
            self._turn_notes(claim_queue=not had_ask_user_wait)
            if self.abort_code is None
            else ""
        )
        if pre_ask_user_queued and self.abort_code is None:
            pre_note = system_reminder(
                "The user sent a message while you were working:\n"
                + "\n\n".join(pre_ask_user_queued)
            )
            trailing_notes = (
                f"{trailing_notes}\n\n{pre_note}" if trailing_notes else pre_note
            )

        out_results: list[ToolCallResult] = []
        for idx, res in enumerate(results):
            status = str(res.get("status") or "failed")
            is_err = status != "succeeded"
            text = str(res.get("content") or "")
            if self.abort_code is not None and self.abort_code not in {"WAITING_USER", "TURN_CANCELLED"}:
                is_err = True
                stop_suffix = f"turn stopped: {self.abort_code}"
                text = f"{text}\n{stop_suffix}".strip() if text else stop_suffix
            elif self.abort_code == "TURN_CANCELLED":
                is_err = True
                text = INTERRUPTED_TOOL_RESULT
            elif trailing_notes and idx == len(results) - 1:
                text = f"{text}\n\n{trailing_notes}" if text else trailing_notes
            images = _convert_tool_images(res.get("images"))
            out_results.append(
                ToolCallResult(
                    text=text,
                    is_error=is_err,
                    images=images,
                    tools_changed=tools_changed,
                )
            )

        if self.abort_code is None:
            self.runtime._life(self.turn, "running")

        return out_results


def _resolve_cli_autocompact(setting: object) -> str | int | None:
    if setting is False:
        return None
    if (
        isinstance(setting, int)
        and not isinstance(setting, bool)
        and 100_000 <= setting <= 1_000_000
    ):
        return int(setting)
    return "auto"


class _RunnerSink:
    def __init__(
        self,
        runner: NativeTurnRunner,
        executor: RuntimeToolExecutor,
    ) -> None:
        self._runner = runner
        self._executor = executor
        self.had_visible_output = False
        self._thinking_buffer: list[str] = []
        self._last_thinking_flush = time.monotonic()
        self._last_empty_thinking_flush: float | None = None
        self._tool_gen_last_emit: dict[str, float] = {}
        self._seen_usage_message_ids: set[str] = set()
        self._current_sink_message_id: str | None = None
        self._emitted_text_in_turn = False
        self._last_text_ended_with_newline = True
        self._message_wrote_text = False
        self.text_parts: list[str] = []
        self._round_text_parts: list[str] = []
        self._round_thinking_parts: list[str] = []
        self.total_tokens: int = 0
        self.total_cost: Decimal = Decimal("0")
        # Model calls of the whole turn (one per assistant message id), across every follow-up message:
        # --max-turns only caps one message of the live process.
        self.model_calls: int = 0
        self._is_live_or_friend = bool(
            runner.runtime._is_live_or_friend(runner.turn)
        )
        self._live_fallback_enabled = bool(runner.turn.get("live_mode")) or (
            self._is_live_or_friend and runner.turn.get("live_mode") is not False
        )
        self.speak_state = _SpeakState()
        self._speak_parser: SpeakStreamParser | None = (
            SpeakStreamParser(live_voice_enabled=self._live_fallback_enabled)
            if self._is_live_or_friend
            else None
        )
        executor.speak_state = self.speak_state
        executor.on_batch_finished = self._on_tool_batch_finished
        executor.consume_round_buffers = self.consume_round_buffers

    def on_message_id(self, message_id: str | None) -> None:
        msg_key = str(message_id or "").strip()
        if not msg_key or msg_key == self._current_sink_message_id:
            return
        prev = self._current_sink_message_id
        self._current_sink_message_id = msg_key
        if prev is not None and self._is_live_or_friend:
            self.speak_state.iteration_had_speak = False

    def consume_round_buffers(self) -> tuple[list[str], list[str]]:
        t_parts = list(self._round_text_parts)
        th_parts = list(self._round_thinking_parts)
        self._round_text_parts.clear()
        self._round_thinking_parts.clear()
        return t_parts, th_parts

    def _emit_visual_delta(self, chunk_text: str) -> None:
        if not chunk_text:
            return
        if (
            not self._message_wrote_text
            and self._emitted_text_in_turn
            and not self._last_text_ended_with_newline
            and not chunk_text.startswith("\n")
        ):
            chunk_text = "\n\n" + chunk_text
        self._message_wrote_text = True
        self.text_parts.append(chunk_text)
        self._runner.runtime.store.delta(self._runner.turn, chunk_text)
        self._emitted_text_in_turn = True
        self._last_text_ended_with_newline = chunk_text.endswith("\n")

    def _on_tool_batch_finished(self, results: list[dict[str, object]]) -> None:
        if self._speak_parser is not None:
            fin_res = self._speak_parser.finish(live_voice_enabled=False)
            fin_tail = self._runner.runtime._emit_speak_events(
                self._runner.turn,
                self._speak_parser,
                fin_res,
                iteration=self._runner.iteration,
                state=self.speak_state,
            )
            if fin_tail:
                self._emit_visual_delta(fin_tail)
            if self.speak_state.iteration_had_speak:
                if any(
                    str(res.get("status") or "").lower() == "failed"
                    and self._runner.runtime._is_mutating_tool_name(
                        str(res.get("name") or "")
                    )
                    for res in results
                ):
                    self.speak_state.pending_retraction = True
            self._speak_parser = SpeakStreamParser(
                live_voice_enabled=self._live_fallback_enabled
                and not self.speak_state.pending_retraction
            )
        self._runner.iteration += 1

    def finish_speak(self) -> None:
        if self._speak_parser is None:
            return
        allow_fallback = (
            self._live_fallback_enabled
            and not self.speak_state.pending_retraction
        )
        fin_res = self._speak_parser.finish(live_voice_enabled=allow_fallback)
        fin_tail = self._runner.runtime._emit_speak_events(
            self._runner.turn,
            self._speak_parser,
            fin_res,
            iteration=self._runner.iteration,
            state=self.speak_state,
        )
        if fin_tail:
            self._emit_visual_delta(fin_tail)

    def reset_for_new_message(self) -> None:
        self._message_wrote_text = False
        self._current_sink_message_id = None
        if self._is_live_or_friend:
            self.speak_state.iteration_had_speak = False
            self._speak_parser = SpeakStreamParser(
                live_voice_enabled=self._live_fallback_enabled
                and not self.speak_state.pending_retraction
            )

    def flush_thinking_buffer(self) -> None:
        if not self._thinking_buffer:
            return
        delta_text = "".join(self._thinking_buffer)
        self._thinking_buffer.clear()
        self._last_thinking_flush = time.monotonic()
        self._runner.runtime._life(
            self._runner.turn,
            "thinking_delta",
            delta=delta_text,
            iteration=self._runner.iteration,
        )

    def on_init(self, init: CliInit) -> None:
        reported = str(init.model or "").strip()
        current = str(self._runner.turn.get("model_id") or "").strip()
        if reported and reported != current:
            self._runner.turn["model_id"] = reported
            if self._runner.runtime._model_switched is not None:
                try:
                    self._runner.runtime._model_switched(reported)
                except Exception:
                    pass
            self._runner.runtime._life(
                self._runner.turn,
                "model_switched",
                from_model=current,
                to_model=reported,
                reason="cli_reported",
            )

    def on_text(self, text: str) -> None:
        if not text:
            return
        self.had_visible_output = True
        self._round_text_parts.append(text)
        self.flush_thinking_buffer()
        chunk_text = text
        if self._speak_parser is not None:
            chunk_text = self._runner.runtime._emit_speak_events(
                self._runner.turn,
                self._speak_parser,
                self._speak_parser.feed(chunk_text),
                iteration=self._runner.iteration,
                state=self.speak_state,
            )
        if chunk_text:
            self._emit_visual_delta(chunk_text)

    def on_thinking(self, text: str) -> None:
        if text:
            self._round_thinking_parts.append(text)
            self._thinking_buffer.append(text)
            buffered_chars = sum(len(p) for p in self._thinking_buffer)
            if (
                buffered_chars >= 60
                or (time.monotonic() - self._last_thinking_flush) >= 0.12
            ):
                self.flush_thinking_buffer()
        else:
            now_empty = time.monotonic()
            if (
                self._last_empty_thinking_flush is None
                or (now_empty - self._last_empty_thinking_flush) >= 2.0
            ):
                self._last_empty_thinking_flush = now_empty
                self._runner.runtime._life(
                    self._runner.turn,
                    "thinking_delta",
                    delta="",
                    iteration=self._runner.iteration,
                )

    def on_tool_use_started(
        self,
        tool_use_id: str,
        tool_name: str,
        message_id: str | None = None,
    ) -> None:
        self.had_visible_output = True
        if message_id:
            self.on_message_id(message_id)
        self.flush_thinking_buffer()
        if self._speak_parser is not None:
            tail_text = self._runner.runtime._emit_speak_events(
                self._runner.turn,
                self._speak_parser,
                self._speak_parser.on_non_text_boundary("tool_call"),
                iteration=self._runner.iteration,
                state=self.speak_state,
            )
            if tail_text:
                self._emit_visual_delta(tail_text)
        self._message_wrote_text = False
        self._executor.register_tool_use_started(
            tool_use_id, tool_name, message_id=message_id
        )
        internal_name = self._executor._resolve_internal_name(tool_name)
        now_mono = time.monotonic()
        if (
            tool_use_id not in self._tool_gen_last_emit
            or (now_mono - self._tool_gen_last_emit[tool_use_id]) >= 1.5
        ):
            self._tool_gen_last_emit[tool_use_id] = now_mono
            self._runner.runtime._life(
                self._runner.turn,
                "generating_tool_call",
                tool_name=internal_name,
                call_id=tool_use_id,
                bytes_received=0,
                iteration=self._runner.iteration,
            )

    def on_tool_input_progress(
        self,
        tool_use_id: str,
        tool_name: str,
        bytes_received: int,
    ) -> None:
        internal_name = self._executor._resolve_internal_name(tool_name)
        now_mono = time.monotonic()
        if (
            tool_use_id not in self._tool_gen_last_emit
            or (now_mono - self._tool_gen_last_emit[tool_use_id]) >= 1.5
        ):
            self._tool_gen_last_emit[tool_use_id] = now_mono
            self._runner.runtime._life(
                self._runner.turn,
                "generating_tool_call",
                tool_name=internal_name,
                call_id=tool_use_id,
                bytes_received=bytes_received,
                iteration=self._runner.iteration,
            )

    def on_tool_use_complete(
        self,
        tool_use_id: str,
        tool_name: str,
        tool_input: Mapping[str, Any],
        message_id: str | None = None,
    ) -> None:
        if message_id:
            self.on_message_id(message_id)
        self.flush_thinking_buffer()
        self._executor.register_tool_use_complete(
            tool_use_id,
            tool_name,
            tool_input,
            message_id=message_id,
        )

    def on_assistant_usage(self, message_id: str, usage: CliUsage) -> None:
        msg_key = str(message_id or "").strip()
        if msg_key:
            self.on_message_id(msg_key)
            if msg_key in self._seen_usage_message_ids:
                return
            self._seen_usage_message_ids.add(msg_key)
            self._executor.note_message_id(msg_key)

        used_in = usage.context_tokens
        out_tok = usage.output_tokens
        tot_tok = used_in + out_tok
        self.total_tokens += tot_tok
        self.model_calls += 1

        self._runner.runtime.counters.note_usage(
            input_tokens=used_in,
            output_tokens=out_tok,
            cached_input_tokens=usage.cache_read_input_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
        )
        self._runner.runtime.counters.settle_provider_call()

        reporter = getattr(self._runner.runtime.store, "record_usage", None)
        if callable(reporter):
            reporter(
                self._runner.turn,
                input_tokens=used_in,
                output_tokens=out_tok,
                total_tokens=tot_tok,
            )

        runtime = self._runner.runtime
        if runtime._cost_estimator is not None:
            # Same contract as the classic loop: (input_tokens, output_tokens).
            step_cost = Decimal("0")
            try:
                estimated = runtime._cost_estimator(used_in, out_tok)
                if estimated is not None:
                    step_cost = Decimal(str(estimated))
            except Exception:  # noqa: BLE001 - pricing never breaks a turn
                step_cost = Decimal("0")
            if step_cost.is_finite() and step_cost > 0:
                self.total_cost += step_cost
                runtime._add_spending(step_cost)

        limits = runtime.limits
        breach: str | None = None
        if limits.max_provider_tokens is not None and self.total_tokens > limits.max_provider_tokens:
            breach = "PROVIDER_TOKEN_LIMIT"
        elif limits.max_cost is not None and self.total_cost > limits.max_cost:
            breach = "PROVIDER_COST_LIMIT"
        elif limits.max_budget_usd is not None and runtime._spent_usd(self.total_cost) >= limits.max_budget_usd:
            breach = "MAX_BUDGET_USD"
        elif limits.max_turns is not None and self.model_calls > limits.max_turns:
            # As the classic loop: max_turns model calls are allowed; the one after them stops the turn.
            breach = "MAX_TURNS"
        if breach is not None and self._executor.abort_code is None:
            self._executor.abort_code = breach
            handle = self._executor.handle
            if handle is not None:
                # Stop the CLI at the breach, as the classic loop stops at the call that crossed the limit:
                # a refused tool result would be one more paid model call.
                for stop in ("refuse_tools", "close"):
                    try:
                        fn = getattr(handle, stop, None)
                        if callable(fn):
                            fn(f"turn stopped: {breach}") if stop == "refuse_tools" else fn()
                    except Exception:  # noqa: BLE001 - the abort code is what stops the turn
                        pass

        limit = (
            self._runner.runtime.limits.context_window_tokens
            or self._runner.runtime.limits.max_context_tokens
        )
        updated_ctx = {
            **self._runner.last_context,
            "used_tokens": used_in,
            "limit_tokens": limit,
            "free_tokens": max(0, (limit or 0) - used_in),
            "percentage": min(100, round(used_in / limit * 100)) if limit else 0,
            "compaction_count": self._runner.runtime._compaction_count,
        }
        self._runner.last_context = updated_ctx
        self._runner.runtime._life(
            self._runner.turn, "context_updated", **updated_ctx
        )

    def on_compacted(self) -> None:
        self._runner.runtime._compaction_count += 1
        try:
            self._runner.runtime._release_read_stubs(
                self._runner.messages, folded=True
            )
        except Exception:
            pass
        try:
            self._runner.runtime._succeeded_reads.clear()
        except Exception:
            pass

    def on_rate_limit(self, state: RateLimitState) -> None:
        pass


class NativeTurnRunner:
    """Executes an entire turn inside a CLI adapter's native MCP tool loop."""

    def __init__(
        self,
        runtime: AgenticTurnRuntime,
        provider: Any,
        turn: dict[str, object],
        *,
        turn_id: str,
        messages: list[dict[str, object]],
        deadline: datetime,
    ) -> None:
        self.runtime = runtime
        self.provider = provider
        self.turn = turn
        self.turn_id = turn_id
        self.messages = messages
        self.deadline = deadline
        self.iteration = 1
        self.last_context: dict[str, object] = {}

    def _stop_code_after_stream_error(self, exc: BaseException, executor: RuntimeToolExecutor) -> str | None:
        """Why the turn really stopped when the CLI stream ended in an error: a limit that fired, or the deadline."""
        if executor.abort_code in _LIMIT_ABORT_CODES:
            return executor.abort_code
        if str(exc).startswith("TURN_DEADLINE_EXCEEDED") or self.runtime.clock() >= self.deadline:
            return "TURN_DEADLINE_EXCEEDED"
        return None

    def _build_initial_user_content(
        self,
        tool_schemas: list[dict[str, object]],
        window: list[dict[str, object]] | None = None,
    ) -> list[dict[str, Any]]:
        self.runtime._check_external_changes(self.turn)
        self.runtime._add_periodic_reminders()
        try:
            pending_reminders = self.runtime.reminders.drain()
        except Exception:
            pending_reminders = []
        reminder_body = render_reminders(pending_reminders)

        source_msgs = window if window is not None else self.messages
        pinned = self.runtime._last_user_index(source_msgs)

        if pinned is not None:
            prior_messages = [
                m for m in source_msgs[:pinned] if m.get("role") != "system"
            ]
            current_msg = source_msgs[pinned]
        else:
            prior_messages = (
                [m for m in source_msgs[:-1] if m.get("role") != "system"]
                if source_msgs
                else []
            )
            current_msg = (
                source_msgs[-1]
                if source_msgs
                else {"role": "user", "content": ""}
            )

        blocks: list[dict[str, Any]] = []
        if prior_messages:
            out_prior = (
                self.runtime._names.messages_out(prior_messages)
                if (window is None and self.runtime._names is not None)
                else prior_messages
            )
            blocks.extend(
                TranscriptState().blocks_for(out_prior, native_history=True)
            )

        volatile_parts: list[str] = []
        if self.runtime.first_user_context:
            volatile_parts.append(self.runtime.first_user_context)
        if self.runtime.volatile_prompt:
            volatile_parts.append(self.runtime.volatile_prompt)
        if self.runtime.contract is not None:
            volatile_parts.append(self.runtime.contract.render())
        if self.runtime.phases is not None:
            published = (
                ", ".join(
                    sorted(
                        str(item.get("function", {}).get("name", ""))
                        for item in tool_schemas
                        if isinstance(item, Mapping)
                    )
                )
                or "nenhuma"
            )
            volatile_parts.append(
                f"{phases.PHASE_INSTRUCTIONS[self.runtime.phases.current]}\n"
                f"Ferramentas disponíveis agora: {published}."
            )
        if volatile_parts:
            blocks.append(
                {
                    "type": "text",
                    "text": system_reminder("\n\n".join(volatile_parts)),
                }
            )
        if reminder_body:
            blocks.append({"type": "text", "text": reminder_body})

        cur_text, cur_images = _extract_content_text_and_images(
            current_msg.get("content")
        )
        if cur_text or not (blocks or cur_images):
            blocks.append({"type": "text", "text": cur_text})
        blocks.extend(cur_images)
        return blocks

    def _remaining_deadline_monotonic(self) -> float:
        now_dt = self.runtime.clock()
        rem_sec = max(0.1, (self.deadline - now_dt).total_seconds())
        return time.monotonic() + rem_sec

    def run(self) -> AgenticRunResult | None:
        self._executor: RuntimeToolExecutor | None = None
        try:
            return self._run()
        finally:
            # Every exit closes the executor, so no ask_user waiter outlives the runner (retries keep it open).
            if self._executor is not None:
                self._executor.close()

    def _run(self) -> AgenticRunResult | None:
        adapter = getattr(self.provider, "adapter", None)
        adapter_name = str(getattr(adapter, "name", "claude") or "claude")
        adapter_caps = getattr(adapter, "capabilities", None)
        allow_deferral = bool(
            getattr(adapter_caps, "tool_list_changed", True)
            if adapter_caps is not None
            else True
        )
        persistent_stdin = bool(
            PERSISTENT_STDIN_OK
            and (
                getattr(adapter_caps, "persistent_stdin", True)
                if adapter_caps is not None
                else True
            )
        )
        supports_session_resume = bool(
            getattr(adapter_caps, "supports_session_resume", not persistent_stdin)
            if adapter_caps is not None
            else False
        )
        raw_schemas = self.runtime._tool_schemas(
            self.turn, allow_deferral=allow_deferral
        )
        tool_schemas = (
            self.runtime._names.schemas_out(raw_schemas)
            if self.runtime._names is not None
            else raw_schemas
        )
        # If phases are active, advertise the full toolset schemas in MCP tools/list
        # so tools that only become active in later phases (e.g. report_verification)
        # are already known to the CLI after system/init, while call_tool enforces
        # the current phase's allowed tool list dynamically.
        if self.runtime.phases is not None:
            try:
                all_raw = list(self.runtime.toolset.schemas())
            except Exception:
                all_raw = list(raw_schemas)
            seen_names = {
                str(s.get("function", {}).get("name", ""))
                for s in all_raw
                if isinstance(s, Mapping)
            }
            for s in raw_schemas:
                if isinstance(s, Mapping):
                    nm = str(s.get("function", {}).get("name", ""))
                    if nm and nm not in seen_names:
                        all_raw.append(s)
                        seen_names.add(nm)
            executor_schemas: list[dict[str, object]] | None = (
                self.runtime._names.schemas_out(all_raw)
                if self.runtime._names is not None
                else all_raw
            )
        else:
            executor_schemas = None

        window = self.runtime._request_messages(self.messages)
        if self.runtime._names is not None:
            window = self.runtime._names.messages_out(window)

        context = dict(
            self.runtime._context_usage(self.messages, window, tool_schemas)
        )
        limit = (
            self.runtime.limits.context_window_tokens
            or self.runtime.limits.max_context_tokens
        )
        if limit:
            used = int(context.get("used_tokens") or 0)
            context["limit_tokens"] = limit
            context["free_tokens"] = max(0, limit - used)
            context["percentage"] = min(100, round(used / limit * 100))
        self.last_context = context
        self.runtime._life(self.turn, "context_updated", **context)

        user_content = self._build_initial_user_content(
            tool_schemas, window=window
        )
        executor = RuntimeToolExecutor(
            self.runtime,
            self.turn,
            tool_schemas=executor_schemas,
            messages=self.messages,
            deadline=self.deadline,
            allow_deferral=allow_deferral,
        )
        self._executor = executor
        sink = _RunnerSink(self, executor)

        native_cwd = _resolve_native_cwd(adapter_name)
        try:
            ensure_isolated_cwd(native_cwd)
        except CliSecurityAbort:
            # An unsafe working folder: the CLI never starts (and no text fallback, which would face the same).
            _LOGGER.warning("claude_cli native turn refused: unsafe working directory %s", native_cwd, exc_info=True)
            return self.runtime._fail(self.turn, "PROVIDER_STREAM_NOT_STARTED", 1, 0)
        limits = self.runtime.limits
        max_turns = int(limits.max_iterations or 200)
        if limits.max_turns is not None and limits.max_turns > 0:
            max_turns = min(max_turns, int(limits.max_turns))
        # The classic loop fails with MAX_TURNS when that limit is the binding one.
        turns_limit_code = (
            "MAX_TURNS"
            if limits.max_turns is not None and max_turns == int(limits.max_turns)
            and (limits.max_iterations is None or limits.max_turns < limits.max_iterations)
            else "ITERATION_LIMIT"
        )
        autocompact = _resolve_cli_autocompact(self.runtime.autocompact)
        effort = (
            self.runtime.effort
            if (self.runtime.effort and not bool(self.turn.get("live_mode")))
            else None
        )
        max_output = self.runtime._output_cap or self.runtime.limits.max_output_tokens

        provider_retries = 0
        process_count = 0
        verify_gate_attempts = 0
        closing_sent = False
        resume_session_id: str | None = None

        while True:
            self.runtime.counters.note_iteration()
            if self.runtime.cancelled(self.turn):
                return self.runtime._cancel(
                    self.turn, max(1, process_count), executor.action_count
                )
            if self.runtime.clock() >= self.deadline:
                return self.runtime._fail(
                    self.turn,
                    "TURN_DEADLINE_EXCEEDED",
                    max(1, process_count),
                    executor.action_count,
                )

            process_count += 1
            self.iteration = process_count
            provider_effect_id = self.runtime._effect_started(
                self.turn,
                kind="provider",
                invocation_ref=f"provider:native:{process_count}",
                request_ref=f"conversation-turn:{self.turn_id}:provider:native:{process_count}",
            )

            fallback_model = (
                self.runtime._fallback_models[0]
                if self.runtime._fallback_models
                else None
            )
            last_waiting_state: list[tuple[int, str | None] | None] = [None]

            def _on_waiting_slot(position: int, limit: int | None = None, reason: str | None = None) -> None:
                eff_reason = reason or ("memory" if limit is None else "limit")
                state_key = (int(position), eff_reason)
                if state_key == last_waiting_state[0]:
                    return
                last_waiting_state[0] = state_key
                self.runtime._life(
                    self.turn,
                    "provider_waiting",
                    position=position,
                    limit=limit,
                    reason=eff_reason,
                )

            req = NativeTurnRequest(
                model=str(self.turn.get("model_id") or ""),
                system_prompt=str(self.runtime.system_prompt or ""),
                user_content=user_content,
                executor=executor,
                cancel_check=lambda: self.runtime.cancelled(self.turn),
                deadline_monotonic=self._remaining_deadline_monotonic(),
                max_turns=max_turns,
                cwd=native_cwd,
                effort=effort,
                fallback_model=fallback_model,
                max_output_tokens=max_output,
                autocompact=autocompact,
                resume_session_id=resume_session_id,
                conversation_id=str(self.turn.get("conversation_id") or ""),
                on_waiting_slot=_on_waiting_slot,
            )

            handle: NativeTurnHandle | Any | None = None
            next_process = False
            try:
                handle = self.provider.start_native_turn(req)
                executor.handle = handle

                while True:
                    try:
                        outcome: NativeTurnOutcome = handle.run_until_result(sink)
                    except ProviderTransportError as stream_exc:
                        if closing_sent and self._stop_code_after_stream_error(stream_exc, executor) is None:
                            self.runtime._effect_finished(
                                self.turn,
                                provider_effect_id,
                                state="APPLIED",
                                error_code=turns_limit_code,
                            )
                            return self.runtime._fail(
                                self.turn,
                                turns_limit_code,
                                process_count,
                                executor.action_count,
                            )
                        raise
                    if supports_session_resume:
                        sid = (
                            (outcome.init.session_id if outcome.init else "")
                            or (outcome.result.session_id if outcome.result else "")
                        )
                        if sid:
                            resume_session_id = sid
                    sink.flush_thinking_buffer()
                    if not sink._emitted_text_in_turn and outcome.final_text:
                        sink.on_text(outcome.final_text)
                    sink.finish_speak()

                    if (
                        outcome.result.subtype == "cancelled"
                        or self.runtime.cancelled(self.turn)
                    ):
                        self.runtime._effect_finished(
                            self.turn,
                            provider_effect_id,
                            state="UNKNOWN"
                            if (sink.had_visible_output or executor.action_count > 0)
                            else "NOT_APPLIED",
                            error_code="PROVIDER_CANCELLED_DURING_EFFECT",
                        )
                        return self.runtime._cancel(
                            self.turn, process_count, executor.action_count
                        )

                    if executor.abort_code is not None:
                        code = executor.abort_code
                        self.runtime._effect_finished(
                            self.turn,
                            provider_effect_id,
                            state="APPLIED",
                            private_result={"outcome": code.lower()},
                        )
                        if code == "HOOK_STOPPED":
                            reason = executor.stop_reason or "stopped"
                            self.runtime.store.delta(
                                self.turn, f"\n\n⏹ Interrompido por hook: {reason}"
                            )
                            self.runtime._life(self.turn, "completed")
                            self.runtime.store.finish(self.turn, code="HOOK_STOPPED")
                            self.runtime._settle_quality(self.turn, "completed")
                            return AgenticRunResult(
                                "completed",
                                process_count,
                                executor.action_count,
                                "HOOK_STOPPED",
                            )
                        if code == "EFFECT_RECONCILIATION_REQUIRED":
                            self.runtime._emit(
                                HookEvent.STOP_FAILURE,
                                error_code="EFFECT_RECONCILIATION_REQUIRED",
                            )
                            self.runtime._settle_quality(
                                self.turn,
                                "reconciliation_required",
                                "EFFECT_RECONCILIATION_REQUIRED",
                            )
                            return AgenticRunResult(
                                "reconciliation_required",
                                process_count,
                                executor.action_count,
                                "EFFECT_RECONCILIATION_REQUIRED",
                            )
                        if code == "SAFETY_TOOL_ROUNDS":
                            return self.runtime._pause_for_safety(
                                self.turn, process_count, executor.action_count
                            )
                        if code == "WAITING_USER":
                            self.runtime._life(self.turn, "waiting_user")
                            self.runtime._emit(
                                HookEvent.STOP,
                                stop_hook_active=False,
                                stop_reason="waiting_user",
                                last_assistant_message="".join(sink.text_parts)[:4000],
                            )
                            self.runtime.store.finish(self.turn, code="WAITING_USER")
                            self.runtime._settle_quality(self.turn, "waiting_user")
                            return AgenticRunResult(
                                "waiting_user",
                                process_count,
                                executor.action_count,
                            )
                        return self.runtime._fail(
                            self.turn, code, process_count, executor.action_count
                        )

                    if outcome.result.subtype == "error_max_turns":
                        proc_alive = True
                        alive_fn = getattr(handle, "is_process_alive", None)
                        if callable(alive_fn):
                            try:
                                proc_alive = bool(alive_fn())
                            except Exception:
                                proc_alive = False
                        turns_spent = (
                            self.runtime.limits.max_turns is not None
                            and sink.model_calls >= self.runtime.limits.max_turns
                        )
                        if not closing_sent and proc_alive and not turns_spent:
                            closing_sent = True
                            handle.refuse_tools("tool budget exhausted")
                            try:
                                handle.send_user(
                                    [{"type": "text", "text": CLOSING_INSTRUCTION}]
                                )
                            except (RuntimeError, NativeLoopUnavailable):
                                self.runtime._effect_finished(
                                    self.turn,
                                    provider_effect_id,
                                    state="APPLIED",
                                    error_code=turns_limit_code,
                                )
                                return self.runtime._fail(
                                    self.turn,
                                    turns_limit_code,
                                    process_count,
                                    executor.action_count,
                                )
                            handle.refuse_tools("tool budget exhausted")
                            sink.reset_for_new_message()
                            continue
                        self.runtime._effect_finished(
                            self.turn,
                            provider_effect_id,
                            state="APPLIED",
                            error_code=turns_limit_code,
                        )
                        return self.runtime._fail(
                            self.turn,
                            turns_limit_code,
                            process_count,
                            executor.action_count,
                        )

                    # Normal completion: check verification gate and Stop hooks
                    turns_used = (
                        self.runtime.limits.max_turns is not None
                        and sink.model_calls >= self.runtime.limits.max_turns
                    )

                    if (
                        not closing_sent
                        and not turns_used
                        and verify_gate_attempts < 1
                        and self.runtime._needs_verification()
                    ):
                        verify_gate_attempts += 1
                        if self.runtime.phases is not None:
                            previous_phase = self.runtime.phases.current
                            self.runtime.phases.force_verify()
                            self.runtime._life(
                                self.turn,
                                "phase_changed",
                                phase=str(self.runtime.phases.current),
                                previous_phase=str(previous_phase),
                            )
                        if persistent_stdin:
                            handle.send_user(
                                [{"type": "text", "text": VERIFY_REQUIRED_MESSAGE}]
                            )
                            sink.reset_for_new_message()
                            self.runtime._life(self.turn, "running")
                            continue
                        if supports_session_resume and resume_session_id:
                            self.runtime._effect_finished(
                                self.turn,
                                provider_effect_id,
                                state="APPLIED",
                                private_result={"outcome": "verify_continuation"},
                            )
                            user_content = [{"type": "text", "text": VERIFY_REQUIRED_MESSAGE}]
                            sink.reset_for_new_message()
                            self.runtime._life(self.turn, "running")
                            next_process = True
                            break

                    stop_decision = self.runtime._emit(
                        HookEvent.STOP,
                        stop_hook_active=self.runtime._stop_hook_active,
                        stop_reason="completed",
                        last_assistant_message="".join(sink.text_parts)[:4000],
                    )
                    if (
                        not closing_sent
                        and not turns_used
                        and stop_decision is not None
                        and stop_decision.blocked
                        and not stop_decision.stop
                        and self.runtime._stop_continuations < MAX_STOP_CONTINUATIONS
                    ):
                        self.runtime._stop_continuations += 1
                        self.runtime._stop_hook_active = True
                        hook_msg = f"[Hook Stop] {stop_decision.reason or 'continue working'}"
                        if persistent_stdin:
                            handle.send_user([{"type": "text", "text": hook_msg}])
                            sink.reset_for_new_message()
                            self.runtime._life(self.turn, "running")
                            continue
                        if supports_session_resume and resume_session_id:
                            self.runtime._effect_finished(
                                self.turn,
                                provider_effect_id,
                                state="APPLIED",
                                private_result={"outcome": "stop_hook_continuation"},
                            )
                            user_content = [{"type": "text", "text": hook_msg}]
                            sink.reset_for_new_message()
                            self.runtime._life(self.turn, "running")
                            next_process = True
                            break

                    self.runtime._effect_finished(
                        self.turn,
                        provider_effect_id,
                        state="APPLIED",
                        result_ref=f"conversation-message:{self.turn.get('assistant_message_id', '')}",
                        private_result={"outcome": "final"},
                    )
                    self.runtime._emit_optimistic_lie_retraction(
                        self.turn, sink.speak_state
                    )
                    verification = self.runtime._code_verification(self.turn)
                    if verification is not None:
                        self.runtime._life(
                            self.turn, "code_verification", status=verification
                        )
                    self.runtime._life(self.turn, "completed")
                    self.runtime.store.finish(self.turn)
                    self.runtime._settle_quality(self.turn, "completed")
                    return AgenticRunResult(
                        "completed",
                        process_count,
                        executor.action_count,
                        budget_exhausted=closing_sent,
                        verification=verification,
                    )

            except NativeLoopUnavailable as exc:
                stop_code = self._stop_code_after_stream_error(exc, executor)
                if stop_code is not None and not self.runtime.cancelled(self.turn):
                    self.runtime._effect_finished(
                        self.turn,
                        provider_effect_id,
                        state="APPLIED" if stop_code in _LIMIT_ABORT_CODES else "UNKNOWN",
                        error_code=stop_code,
                    )
                    return self.runtime._fail(self.turn, stop_code, process_count, executor.action_count)
                if getattr(exc, "status_code", None) == 499 or self.runtime.cancelled(
                    self.turn
                ):
                    self.runtime._effect_finished(
                        self.turn,
                        provider_effect_id,
                        state="NOT_APPLIED",
                        error_code="PROVIDER_CANCELLED_DURING_EFFECT",
                    )
                    return self.runtime._cancel(
                        self.turn, process_count, executor.action_count
                    )
                recorder = getattr(self.provider, "record_native_unavailable", None)
                if callable(recorder):
                    try:
                        recorder(exc)
                    except Exception:
                        pass
                if sink.had_visible_output or executor.action_count > 0:
                    self.runtime._effect_finished(
                        self.turn,
                        provider_effect_id,
                        state="UNKNOWN",
                        error_code="PROVIDER_STREAM_INTERRUPTED",
                    )
                    self.runtime._emit(
                        HookEvent.STOP_FAILURE,
                        error_code="PROVIDER_STREAM_INTERRUPTED",
                    )
                    self.runtime._settle_quality(
                        self.turn,
                        "reconciliation_required",
                        "PROVIDER_STREAM_INTERRUPTED",
                    )
                    return AgenticRunResult(
                        "reconciliation_required",
                        process_count,
                        executor.action_count,
                        "PROVIDER_STREAM_INTERRUPTED",
                    )
                self.runtime._effect_finished(
                    self.turn,
                    provider_effect_id,
                    state="NOT_APPLIED",
                    error_code="NATIVE_LOOP_UNAVAILABLE",
                )
                return None

            except Exception as exc:
                stop_code = self._stop_code_after_stream_error(exc, executor)
                if self.runtime.cancelled(self.turn):
                    stop_code = None
                if stop_code is not None:
                    self.runtime._effect_finished(
                        self.turn,
                        provider_effect_id,
                        state="APPLIED" if stop_code in _LIMIT_ABORT_CODES else "UNKNOWN",
                        error_code=stop_code,
                    )
                    return self.runtime._fail(self.turn, stop_code, process_count, executor.action_count)
                if sink.had_visible_output or executor.action_count > 0:
                    self.runtime._effect_finished(
                        self.turn,
                        provider_effect_id,
                        state="UNKNOWN",
                        error_code="PROVIDER_STREAM_INTERRUPTED",
                    )
                    self.runtime._emit(
                        HookEvent.STOP_FAILURE,
                        error_code="PROVIDER_STREAM_INTERRUPTED",
                    )
                    self.runtime._settle_quality(
                        self.turn,
                        "reconciliation_required",
                        "PROVIDER_STREAM_INTERRUPTED",
                    )
                    return AgenticRunResult(
                        "reconciliation_required",
                        process_count,
                        executor.action_count,
                        "PROVIDER_STREAM_INTERRUPTED",
                    )

                self.runtime._effect_finished(
                    self.turn,
                    provider_effect_id,
                    state="NOT_APPLIED",
                    error_code="PROVIDER_STREAM_FAILED",
                )
                recovery = self.runtime._recover_provider_failure(
                    self.turn,
                    exc,
                    retries_used=provider_retries,
                    deadline=self.deadline,
                    code="PROVIDER_STREAM_FAILED",
                )
                if recovery == "retry":
                    provider_retries += 1
                    next_process = True
                    continue
                if recovery == "fallback":
                    provider_retries = 0
                    new_native = self.runtime._native_provider(self.turn)
                    if new_native is not None:
                        self.provider = new_native
                        effort = (
                            self.runtime.effort
                            if (
                                self.runtime.effort
                                and not bool(self.turn.get("live_mode"))
                            )
                            else None
                        )
                        next_process = True
                        continue
                    return None
                if recovery == "cancel":
                    return self.runtime._cancel(
                        self.turn, process_count, executor.action_count
                    )
                return self.runtime._fail(
                    self.turn,
                    "PROVIDER_STREAM_FAILED",
                    process_count,
                    executor.action_count,
                )
            finally:
                # The executor serves every process of the turn: closing it on a retry or fallback would make
                # the next process's ask_user return "interrupted" at once.
                if not next_process:
                    executor.close()
                if handle is not None:
                    try:
                        handle.close()
                    except Exception:
                        pass


__all__ = [
    "BATCH_GATHER_SECONDS",
    "CLI_PARALLEL_MCP",
    "CLI_PARALLEL_NEEDS_READONLY_HINT",
    "NATIVE_CWD",
    "NativeTurnRunner",
    "RuntimeToolExecutor",
    "SESSION_BRANCH",
    "SessionPlan",
    "TOOL_USE_ID_META_KEY",
    "ToolBatchCollector",
    "history_digest",
    "native_workdir",
    "prompt_digest",
]

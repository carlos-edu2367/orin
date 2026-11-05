from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence
from uuid import uuid4

from agentos.installation.paths import orin_paths
from agentos.providers.claude_cli.command import TOOL_USE_ID_META_KEY
from agentos.providers.claude_cli.errors import NativeLoopUnavailable, RateLimitState
from agentos.providers.claude_cli.native_loop import (
    NATIVE_LOOP_VALIDATED,
    PERSISTENT_STDIN_OK,
    NativeTurnHandle,
    NativeTurnOutcome,
    NativeTurnRequest,
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
)

if TYPE_CHECKING:
    from .runtime import AgenticTurnRuntime

BATCH_GATHER_SECONDS: float = 0.1
CLI_PARALLEL_MCP: bool = True
CLI_PARALLEL_NEEDS_READONLY_HINT: bool = True
SESSION_BRANCH: str = "B"
NATIVE_CWD: Path = orin_paths().data / "claude-cli" / "workdir"


def _resolve_native_cwd() -> Path:
    return orin_paths().data / "claude-cli" / "workdir"


@dataclass(frozen=True, slots=True)
class SessionPlan:
    session_id: str = ""
    resume: bool = False


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


def _strip_mcp_prefix(name: str) -> str:
    raw = str(name or "").strip()
    if raw.startswith("mcp__"):
        parts = raw.split("__", 2)
        if len(parts) == 3 and parts[2]:
            return parts[2]
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
            prior_entries: list[_BatchEntry] = []
            with self._state_lock:
                open_batch = self._open_batches.pop(msg_key, None)
                if open_batch is not None and not open_batch.closed:
                    open_batch.closed = True
                    prior_entries = list(open_batch.entries)
                    with open_batch.cond:
                        open_batch.cond.notify_all()
            for prior in prior_entries:
                prior.done.wait()

            with self._exec_lock:
                results = self._run_batch([entry], msg_key)
            if not results:
                return ToolCallResult(text="", is_error=True)
            return results[0]

        with self._state_lock:
            now = self._clock()
            batch = self._open_batches.get(msg_key)
            if batch is not None and not batch.closed and now < batch.deadline:
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
    ) -> None:
        self.runtime = runtime
        self.turn = turn
        if tool_schemas is None:
            raw_schemas = runtime._tool_schemas(turn)
            tool_schemas = (
                runtime._names.schemas_out(raw_schemas)
                if runtime._names is not None
                else raw_schemas
            )
        self._tool_schemas = list(tool_schemas)
        self._custom_correlate = correlate
        self.messages: list[dict[str, object]] = messages if messages is not None else []
        self.deadline: datetime | float | None = deadline
        self.abort_code: str | None = None
        self.stop_reason: str | None = None
        self.action_count: int = 0
        self.tool_rounds: int = 0
        self.handle: NativeTurnHandle | Any | None = None

        self._corr_lock = threading.Lock()
        self._pending_tool_uses: list[tuple[str, str, str, str]] = []
        self._tool_use_message_id: dict[str, str] = {}
        self._current_message_id: str = "msg_1"
        self._msg_seq: int = 1

        self._specs: list[McpToolSpec] = []
        self._valid_public_names: set[str] = set()
        self._valid_internal_names: set[str] = set()
        self._build_specs()

        self.collector = ToolBatchCollector(
            self._execute_batch,
            gather_seconds=gather_seconds,
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

    def list_tools(self) -> Sequence[McpToolSpec]:
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
        active_mcp = set(getattr(self.runtime.toolset, "active_mcp_tool_names", ()) or ())

        if (
            self._valid_internal_names
            and internal_name not in self._valid_internal_names
            and raw_name not in self._valid_public_names
            and internal_name not in active_mcp
        ):
            valid_list = ", ".join(sorted(self._valid_public_names))
            return ToolCallResult(
                text=f"Unknown tool '{request.name}'. Valid tools: {valid_list}",
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
            self.messages.append({"role": "user", "content": content})
            self.runtime._queued_turn_count += 1
            self.runtime._record_step(
                self.turn,
                kind=transcript.STEP_QUEUED_INPUT,
                payload={"content": text},
            )
            self.runtime._life(
                self.turn,
                "queued_input",
                queued_text=text[:2000],
                queue_id=queue_id,
            )
            claimed.append(content)
        return claimed

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
            if self.runtime.cancelled(self.turn) or self._deadline_exceeded():
                return None
            claimed = self._claim_queued_texts()
            if claimed:
                return "\n\n".join(claimed)
            waited = 0.0
            while waited < poll_seconds:
                if self.runtime.cancelled(self.turn) or self._deadline_exceeded():
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

        results = self.runtime._run_toolset(self.turn, raw_calls)
        self.action_count += len(results)
        self.tool_rounds += 1
        self.runtime._advance_phase(self.turn, results)

        ask_user_cancelled = False
        had_ask_user_wait = False
        for res in results:
            if bool(res.get("wait_for_user")) and str(res.get("name") or "") == "ask_user":
                had_ask_user_wait = True
                self.runtime._life(self.turn, "waiting_user_in_turn")
                answer = self._wait_for_answer()
                if answer is None:
                    ask_user_cancelled = True
                    res["status"] = "failed"
                    res["content"] = INTERRUPTED_TOOL_RESULT
                    res["wait_for_user"] = False
                else:
                    res["status"] = "succeeded"
                    res["content"] = f"Resposta da pessoa:\n{answer}"
                    res["wait_for_user"] = False

        self.messages.append(
            self.runtime._assistant_tool_message(
                self.turn,
                [],
                calls_dict,
                [],
            )
        )
        self.runtime._record_step(
            self.turn,
            kind=transcript.STEP_ASSISTANT_TOOL_CALL,
            payload=transcript.assistant_tool_call_payload("", calls_dict.values()),
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
        self._emitted_text_in_turn = False
        self._last_text_ended_with_newline = True
        self._message_wrote_text = False
        self.text_parts: list[str] = []

    def reset_for_new_message(self) -> None:
        self._message_wrote_text = False

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
        self.flush_thinking_buffer()
        chunk_text = text
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

    def on_thinking(self, text: str) -> None:
        self.had_visible_output = True
        if text:
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
        self.flush_thinking_buffer()
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
            if msg_key in self._seen_usage_message_ids:
                return
            self._seen_usage_message_ids.add(msg_key)
            self._executor.note_message_id(msg_key)

        used_in = usage.context_tokens
        out_tok = usage.output_tokens
        tot_tok = used_in + out_tok

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

    def on_rate_limit(self, state: RateLimitState) -> None:
        pass


class NativeTurnRunner:
    """Executes an entire turn inside `claude -p`'s native MCP tool loop."""

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

    def _build_initial_user_content(
        self,
        tool_schemas: list[dict[str, object]],
    ) -> list[dict[str, Any]]:
        self.runtime._check_external_changes(self.turn)
        self.runtime._add_periodic_reminders()
        try:
            pending_reminders = self.runtime.reminders.drain()
        except Exception:
            pending_reminders = []
        reminder_body = render_reminders(pending_reminders)

        pinned = self.runtime._pinned_index
        if not isinstance(pinned, int) or pinned < 0 or pinned >= len(self.messages):
            pinned = self.runtime._last_user_index(self.messages)

        if pinned is not None:
            prior_messages = self.messages[:pinned]
            current_msg = self.messages[pinned]
        else:
            prior_messages = self.messages[:-1] if self.messages else []
            current_msg = self.messages[-1] if self.messages else {"role": "user", "content": ""}

        blocks: list[dict[str, Any]] = []
        if prior_messages:
            out_prior = (
                self.runtime._names.messages_out(prior_messages)
                if self.runtime._names is not None
                else prior_messages
            )
            blocks.extend(TranscriptState().blocks_for(out_prior))

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
        raw_schemas = self.runtime._tool_schemas(self.turn)
        tool_schemas = (
            self.runtime._names.schemas_out(raw_schemas)
            if self.runtime._names is not None
            else raw_schemas
        )
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

        user_content = self._build_initial_user_content(tool_schemas)
        executor = RuntimeToolExecutor(
            self.runtime,
            self.turn,
            tool_schemas=tool_schemas,
            messages=self.messages,
            deadline=self.deadline,
        )
        sink = _RunnerSink(self, executor)

        native_cwd = _resolve_native_cwd()
        native_cwd.mkdir(parents=True, exist_ok=True)
        max_turns = int(self.runtime.limits.max_iterations or 200)
        autocompact = _resolve_cli_autocompact(self.runtime.autocompact)
        effort = (
            self.runtime.effort
            if (self.runtime.effort and not bool(self.turn.get("live_mode")))
            else None
        )
        max_output = self.runtime._output_cap or self.runtime.limits.max_output_tokens

        provider_retries = 0
        process_count = 0
        code_gate_rejections = 0
        closing_sent = False

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
            )

            handle: NativeTurnHandle | Any | None = None
            try:
                handle = self.provider.start_native_turn(req)
                executor.handle = handle

                while True:
                    outcome: NativeTurnOutcome = handle.run_until_result(sink)
                    sink.flush_thinking_buffer()

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
                        if not closing_sent:
                            closing_sent = True
                            handle.refuse_tools("tool budget exhausted")
                            handle.send_user(
                                [{"type": "text", "text": CLOSING_INSTRUCTION}]
                            )
                            handle.refuse_tools("tool budget exhausted")
                            sink.reset_for_new_message()
                            continue
                        self.runtime._effect_finished(
                            self.turn,
                            provider_effect_id,
                            state="APPLIED",
                            error_code="ITERATION_LIMIT",
                        )
                        return self.runtime._fail(
                            self.turn,
                            "ITERATION_LIMIT",
                            process_count,
                            executor.action_count,
                        )

                    # Normal completion: check completion gate, verification gate, and Stop hooks
                    completion_gate = getattr(
                        self.runtime.toolset, "code_completion_gate", None
                    )
                    gate_ok, gate_reason = (True, None)
                    if (
                        callable(completion_gate)
                        and self.runtime.completion_gate
                        and not self.turn.get("is_subagent")
                        and code_gate_rejections < 1
                    ):
                        try:
                            gate_ok, gate_reason = completion_gate()
                        except Exception:
                            gate_ok, gate_reason = True, None
                    if not gate_ok:
                        code_gate_rejections += 1
                        self.runtime._life(
                            self.turn,
                            "code_validation_required",
                            reason=gate_reason,
                        )
                        follow_up = (
                            f"[Sistema do Modo Code] {gate_reason} "
                            "Continue a tarefa usando ferramentas; não responda como concluída."
                        )
                        if PERSISTENT_STDIN_OK:
                            handle.send_user([{"type": "text", "text": follow_up}])
                            sink.reset_for_new_message()
                            self.runtime._life(self.turn, "running")
                            continue

                    if self.runtime._needs_verification():
                        if self.runtime.phases is not None:
                            previous_phase = self.runtime.phases.current
                            self.runtime.phases.force_verify()
                            self.runtime._life(
                                self.turn,
                                "phase_changed",
                                phase=str(self.runtime.phases.current),
                                previous_phase=str(previous_phase),
                            )
                        if PERSISTENT_STDIN_OK:
                            handle.send_user(
                                [{"type": "text", "text": VERIFY_REQUIRED_MESSAGE}]
                            )
                            sink.reset_for_new_message()
                            self.runtime._life(self.turn, "running")
                            continue

                    stop_decision = self.runtime._emit(
                        HookEvent.STOP,
                        stop_hook_active=self.runtime._stop_hook_active,
                        stop_reason="completed",
                        last_assistant_message="".join(sink.text_parts)[:4000],
                    )
                    if (
                        stop_decision is not None
                        and stop_decision.blocked
                        and not stop_decision.stop
                        and self.runtime._stop_continuations < MAX_STOP_CONTINUATIONS
                    ):
                        self.runtime._stop_continuations += 1
                        self.runtime._stop_hook_active = True
                        hook_msg = f"[Hook Stop] {stop_decision.reason or 'continue working'}"
                        if PERSISTENT_STDIN_OK:
                            handle.send_user([{"type": "text", "text": hook_msg}])
                            sink.reset_for_new_message()
                            self.runtime._life(self.turn, "running")
                            continue

                    self.runtime._effect_finished(
                        self.turn,
                        provider_effect_id,
                        state="APPLIED",
                        result_ref=f"conversation-message:{self.turn.get('assistant_message_id', '')}",
                        private_result={"outcome": "final"},
                    )
                    self.runtime._settle_remaining_todos(
                        self.turn, str(self.turn_id)
                    )
                    self.runtime._life(self.turn, "completed")
                    self.runtime.store.finish(self.turn)
                    self.runtime._settle_quality(self.turn, "completed")
                    return AgenticRunResult(
                        "completed",
                        process_count,
                        executor.action_count,
                        budget_exhausted=closing_sent,
                    )

            except NativeLoopUnavailable:
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
    "prompt_digest",
]

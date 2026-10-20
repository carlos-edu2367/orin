"""``orin code -p`` and the SDK's stream-json mode: run turns on the local Orin and speak Claude Code's protocol.

One process may run several turns (stream-json input sends one user message per turn). Three things happen at once —
stdin (user messages and control requests), the conversation's event stream, and the current turn — so stdin and the
event stream each have a reader thread feeding one inbox, and only the main thread writes to stdout.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from agentos.agentic.loop_settings import bypass_disabled
from agentos.code_mode.models import public_permission_mode
from agentos.version import __version__

from agentos.agentic.cli_lease import HEARTBEAT_SECONDS
from agentos.settings import load_effective_settings

from .args import EXIT_ERROR, EXIT_OK, CliUsageError, CodeOptions
from .errors import CliRuntimeError
from .host_bridge import HostBridge
from .protocol import Translator, TurnUsage
from .runtime import OrinClient
from .selection import ModelCatalog, Selection, choose_selection
from .sessions import CliSession, SessionIndex, is_uuid
from .turn_options import build_turn_options, permission_mode_for

MAX_PROMPT_CHARS = 16000  # what the API accepts for one message
_END_EVENTS = {"turn.completed", "turn.failed", "turn.waiting_user"}
_CALL_TIMEOUT = 5.0  # seconds: a cancel or a heartbeat is best effort and must not hold the exit (W6-R2)

# Flags this build parses and refuses, each with where it stands; never silently ignored.
UNSUPPORTED: dict[str, str] = {
    "agent": "--agent",
    "plugin_dirs": "--plugin-dir", "worktree": "--worktree", "fork_session": "--fork-session",
    "bare": "--bare", "safe_mode": "--safe-mode", "restricted": "--restricted",
    "desktop": "--desktop", "resume_session_at": "--resume-session-at",
    "resume_drops_turn": "--resume-drops-turn", "session_mirror": "--session-mirror", "task_budget": "--task-budget",
    "prompt_suggestions": "--prompt-suggestions", "exclude_dynamic_system_prompt_sections": "--exclude-dynamic-system-prompt-sections",
    "thinking_display": "--thinking-display",
}


def refuse_unsupported(options: CodeOptions) -> None:
    flags = [flag for name, flag in UNSUPPORTED.items() if getattr(options, name) not in (None, False, [], "")]
    if flags:
        raise CliUsageError(f"not supported by orin code yet: {', '.join(flags)}")
    if options.init and options.maintenance:
        raise CliUsageError("--init and --maintenance cannot be combined")
    if options.permission_prompt_tool not in (None, "stdio"):
        raise CliUsageError("--permission-prompt-tool accepts only stdio (an SDK host's can_use_tool callback)")
    if options.permission_prompt_tool == "stdio" and options.input_format != "stream-json":
        raise CliUsageError("--permission-prompt-tool stdio needs --input-format stream-json")


# Runs that have a turn in progress: a process-level exit (Ctrl-C, SIGTERM) cancels their turns (W6-R2).
_ACTIVE: set["HeadlessRun"] = set()
_ACTIVE_LOCK = threading.Lock()


def cancel_active_turns() -> None:
    """Cancel the turn of every run in this process that has one running (once per turn; errors ignored)."""
    with _ACTIVE_LOCK:
        runs = list(_ACTIVE)
    for run in runs:
        run.cancel_turn()


class _Heartbeat:
    """Renews a turn's lease while `orin code` follows it (W6-R2); a dead process stops renewing, and the turn ends."""

    def __init__(self, client: OrinClient, conversation_id: str, turn_id: str, *, interval: float = HEARTBEAT_SECONDS) -> None:
        self._client = client
        self._path = f"/v1/conversations/{conversation_id}/turns/{turn_id}/cli-lease"
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._beat, name="orin-code-heartbeat", daemon=True)
        self._thread.start()

    def _beat(self) -> None:
        while not self._stop.is_set():
            try:
                self._client.post(self._path, {}, timeout=_CALL_TIMEOUT)
            except Exception:  # noqa: BLE001 - a missed renewal is covered by the next one; it never ends the run
                pass
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)


def _prompt_text(content: object) -> str:
    """A user message's text; images and documents are refused rather than dropped."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, dict):
                raise CliRuntimeError(f"orin code does not take {block.get('type')!r} blocks in a prompt yet")
        return "\n".join(parts)
    raise CliRuntimeError("a user message needs text content")


class _Output:
    def __init__(self, stdout: TextIO) -> None:
        self._stdout = stdout
        self._lock = threading.Lock()

    def message(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._stdout.flush()

    def text(self, value: str) -> None:
        with self._lock:
            self._stdout.write(value)
            self._stdout.flush()


class HeadlessRun:
    def __init__(self, options: CodeOptions, *, client: OrinClient, data_dir: Path, cwd: Path, stdin: TextIO,
                 stdout: TextIO, stderr: TextIO, clock: Callable[[], float] = time.monotonic,
                 configured_model: str | None = None) -> None:
        self.options = options
        self.client = client
        self.cwd = cwd
        self.stdin = stdin
        self.stderr = stderr
        self.out = _Output(stdout)
        self.clock = clock
        self.index = SessionIndex(data_dir)
        self.catalog = ModelCatalog(client)
        self.configured_model = configured_model
        self.inbox: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.stream_json = options.output_format == "stream-json"
        self.session: CliSession | None = None
        self.conversation_id: str | None = None
        self.selection: Selection | None = None
        self.translator: Translator | None = None
        self._events_started = False
        self._stop = threading.Event()
        self._stdin_closed = False
        self._pending_prompts: list[tuple[str, str | None]] = []
        self._init_sent = False
        self._setup_sent = False  # --init / --maintenance fire Setup once per run
        self._cursor = "0"
        self._session_id = ""
        self._debug = self._debug_sink()
        self.bridge = HostBridge(self)
        self.current_turn_id: str | None = None
        self._cancelled_turn: str | None = None
        self.initial_selection: Selection | None = None
        self.prompt_turns: dict[str, str] = {}  # a user message's uuid (as the host saw it) -> its turn
        # W6-R4: bypass is refused until run() has read the folder's policy (fail closed). Only the start flags allow a
        # later set_permission_mode to bypass; a switch away and back needs no new flag.
        self.bypass_policy = True
        self.bypass_allowed = options.dangerously_skip_permissions or options.allow_dangerously_skip_permissions

    # -- plumbing -------------------------------------------------------------------------------------------------

    def _debug_sink(self) -> Callable[[str], None]:
        if self.options.debug is None and not self.options.debug_file:
            return lambda line: None
        target = self.options.debug_file

        def write(line: str) -> None:
            text = f"[orin code] {line}\n"
            if target:
                with open(target, "a", encoding="utf-8") as handle:
                    handle.write(text)
            else:
                self.stderr.write(text)
                self.stderr.flush()

        return write

    def _read_stdin(self) -> None:
        for raw in self.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                self.inbox.put(("bad_input", line[:200]))
                continue
            self.inbox.put(("stdin", item))
        self.inbox.put(("stdin_closed", None))

    def _follow_events(self, conversation_id: str, after: str) -> None:
        try:
            for event in self.client.events(conversation_id, after=after, stop=self._stop.is_set):
                self.inbox.put(("event", event))
        except Exception as error:  # noqa: BLE001 - after the run ends, closing the client may break the read
            if not self._stop.is_set():
                self.inbox.put(("stream_error", str(error)))

    def _start_events(self) -> None:
        if self._events_started or self.conversation_id is None:
            return
        self._events_started = True
        threading.Thread(target=self._follow_events, args=(self.conversation_id, self._cursor), daemon=True).start()

    # -- sessions and turns ---------------------------------------------------------------------------------------

    def _resolve_session(self) -> None:
        options = self.options
        if options.session_id is not None and not is_uuid(options.session_id):
            raise CliUsageError("--session-id must be a UUID")
        if options.continue_session:
            self.session = self.index.latest_for(self.cwd)
            if self.session is None:
                raise CliRuntimeError("no orin code session to continue in this folder")
        elif options.resume is not None:
            if not options.resume:
                raise CliUsageError("-r needs a session id or name in a headless run")
            self.session = self.index.find(options.resume)
            if self.session is None:
                raise CliRuntimeError(f"no orin code session {options.resume!r}")
        elif options.session_id is not None and self.index.find(options.session_id) is not None:
            raise CliRuntimeError(f"the session id {options.session_id} is already in use")
        remembered: Selection | None = None
        if self.session is not None:
            conversation = self.client.get(f"/v1/conversations/{self.session.conversation_id}")
            self.conversation_id = self.session.conversation_id
            self._cursor = str(conversation.get("activity_cursor") or "0") if isinstance(conversation, dict) else "0"
            if isinstance(conversation, dict) and conversation.get("provider") and conversation.get("model_id"):
                remembered = Selection(str(conversation["provider"]), str(conversation["model_id"]))
        self.selection = choose_selection(self.catalog, model=options.model, provider=options.provider,
                                          remembered=remembered, configured=self.configured_model)
        self.initial_selection = self.selection
        session_id = self.session.session_id if self.session is not None else (options.session_id or "").lower() or None
        self._session_id = session_id or str(uuid.uuid4())
        self.translator = Translator(session_id=self._session_id, model=self.selection.model_id,
                                     partial=options.include_partial_messages, hook_events=options.include_hook_events)

    def _send(self, prompt: str) -> str:
        if len(prompt) > MAX_PROMPT_CHARS:
            raise CliRuntimeError(f"the prompt has {len(prompt)} characters; Orin takes at most {MAX_PROMPT_CHARS} per message")
        # Only an SDK host with a can_use_tool callback (--permission-prompt-tool stdio) answers permission prompts.
        host = self.options.permission_prompt_tool == "stdio"
        turn_options = build_turn_options(self.options, self.cwd, host_answers_prompts=host,
                                          sdk_hooks=self.bridge.sdk_hooks, sdk_agents=self.bridge.sdk_agents,
                                          first_turn=not self._setup_sent).model_dump()
        self._setup_sent = True
        mode = permission_mode_for(self.options)
        body: dict[str, Any] = {"message": prompt, "selection": self.selection.as_request(), "code_mode": "code",
                                "cli": turn_options, "permission_mode": mode}
        if self.conversation_id is None:
            created = self.client.post("/v1/conversations", {**body, "workspace_path": str(self.cwd)})
            self.conversation_id = str(created["conversation_id"])
            turn_id = str(created["turn_id"])
        else:
            sent = self.client.post(f"/v1/conversations/{self.conversation_id}/messages", body)
            turn_id = str(sent["turn_id"])
        if not self.options.no_session_persistence:
            self.session = self.index.record(conversation_id=self.conversation_id, folder=self.cwd,
                                             session_id=self._session_id, name=self.options.name)
        self._debug(f"turn {turn_id} queued in {self.conversation_id}")
        return turn_id

    def _with_transcript(self, event: dict[str, Any]) -> dict[str, Any]:
        """A tool event with its input or result, read from the turn's transcript (never in the activity payload)."""
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else None
        entry_id = payload.get("transcript_id") if payload else None
        if not isinstance(entry_id, str) or self.conversation_id is None:
            return event
        try:
            entry = self.client.get(f"/v1/conversations/{self.conversation_id}/transcript/{entry_id}")
        except CliRuntimeError:
            self._debug(f"transcript {entry_id} could not be read")
            return event
        if not isinstance(entry, dict):
            return event
        merged = {**payload, **{key: entry[key] for key in ("tool_input", "tool_result") if key in entry}}
        return {**event, "payload": merged}

    def _usage(self) -> TurnUsage:
        if self.conversation_id is None:
            return TurnUsage()
        try:
            overview = self.client.get(f"/v1/conversations/{self.conversation_id}/overview")
        except CliRuntimeError:
            return TurnUsage()
        usage = overview.get("token_usage") if isinstance(overview, dict) else None
        if not isinstance(usage, dict):
            return TurnUsage()
        return TurnUsage(int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0), float(usage.get("estimated_cost_usd") or 0.0))

    def _send_init(self) -> None:
        if self._init_sent or not self.stream_json:
            return
        self._init_sent = True
        from agentos.agentic.cli_turn import tool_selection
        from agentos.agentic.runtime import CODE_MODE_CORE_TOOLS
        from agentos.agentic.tools.public_names import public_name

        turn_options = build_turn_options(self.options, self.cwd, host_answers_prompts=False)
        offered, withheld = tool_selection(turn_options, CODE_MODE_CORE_TOOLS)
        tools = sorted(public_name(name) for name in CODE_MODE_CORE_TOOLS if (offered is None or name in offered) and name not in withheld)
        mode = permission_mode_for(self.options)
        self.out.message(self.translator.init_message(
            cwd=str(self.cwd), tools=tools, permission_mode=public_permission_mode(mode), mcp_servers=[],
            slash_commands=[], version=__version__,
        ))

    def answer(self, prompt: str, *, prompt_uuid: str | None = None) -> dict[str, Any]:
        """One prompt's answer: a turn, or with ``--json-schema`` as many follow-ups as the answer needs (W3-R25)."""
        if not self.options.json_schema:
            return self.run_turn(prompt, prompt_uuid=prompt_uuid)
        from .structured import MAX_STRUCTURED_RETRIES, load_schema, problem, retry_prompt

        schema = load_schema(self.options.json_schema)
        started = self.clock()
        totals, model_calls, text = TurnUsage(), 0, prompt
        for attempt in range(MAX_STRUCTURED_RETRIES + 1):
            result = self.run_turn(text, final=False, prompt_uuid=prompt_uuid if attempt == 0 else None)
            totals = TurnUsage(totals.input_tokens + result["usage"]["input_tokens"], totals.output_tokens + result["usage"]["output_tokens"],
                               totals.cost_usd + float(result["total_cost_usd"]))
            model_calls += int(result["num_turns"])
            if result["is_error"]:
                break
            value, issue = problem(schema, str(result.get("result") or ""))
            if issue is None:
                result["structured_output"] = value
                break
            if attempt == MAX_STRUCTURED_RETRIES:
                result.update({"subtype": "error_max_structured_output_retries", "is_error": True, "errors": [issue], "stop_reason": None})
                break
            text = retry_prompt(issue)
        elapsed = int((self.clock() - started) * 1000)
        result.update({"num_turns": model_calls, "duration_ms": elapsed, "duration_api_ms": elapsed, "usage": totals.as_usage(),
                       "total_cost_usd": totals.cost_usd})
        result["modelUsage"] = {self.translator.model: {
            "inputTokens": totals.input_tokens, "outputTokens": totals.output_tokens, "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0, "webSearchRequests": 0, "costUSD": totals.cost_usd,
        }}
        if self.stream_json:
            self.out.message(result)
        return result

    def run_turn(self, prompt: str, *, final: bool = True, prompt_uuid: str | None = None) -> dict[str, Any]:
        """One turn, start to result; returns the result message (written to stdout only when ``final``)."""
        started = self.clock()
        before = self._usage()
        self._send_init()
        replayed = prompt_uuid or str(uuid.uuid4())
        if self.options.replay_user_messages and self.stream_json:
            self.out.message({"type": "user", "session_id": self._session_id, "uuid": replayed,
                              "message": {"role": "user", "content": prompt}, "parent_tool_use_id": None})
        turn_id = self._send(prompt)
        self.current_turn_id = turn_id
        self.prompt_turns[replayed] = turn_id
        self._start_events()
        beat = _Heartbeat(self.client, str(self.conversation_id), turn_id)
        beat.start()
        try:
            state, error_code = self._await_turn_end(turn_id)
        finally:
            beat.stop()
        self.current_turn_id = None
        after = self._usage()
        usage = TurnUsage(max(0, after.input_tokens - before.input_tokens), max(0, after.output_tokens - before.output_tokens),
                          max(0.0, after.cost_usd - before.cost_usd))
        elapsed = int((self.clock() - started) * 1000)
        closing = self.translator.result(state=state, error_code=str(error_code) if error_code else None, usage=usage,
                                         duration_ms=elapsed, duration_api_ms=elapsed)
        if self.stream_json:
            for message in closing if final else closing[:-1]:  # the last open block's assistant message, then the result
                self.out.message(message)
        return closing[-1]

    def _await_turn_end(self, turn_id: str) -> tuple[str, object]:
        """Relay the turn's events until its end event; returns ``(state, error code)``."""
        state, error_code = "failed", None
        while True:
            kind, item = self.inbox.get()
            if kind == "event":
                if str(item.get("turn_id") or "") != turn_id:
                    continue
                for message in self.translator.feed(self._with_transcript(item)):
                    if self.stream_json:
                        self.out.message(message)
                self.bridge.on_turn_event(item, turn_id)
                event_type = str(item.get("event_type") or "")
                if event_type in _END_EVENTS:
                    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                    state = "completed" if event_type == "turn.completed" else str(payload.get("state") or "failed")
                    if event_type == "turn.waiting_user":
                        state = "completed"  # an approval Orin waits for ends the headless turn; the person resumes it
                    error_code = payload.get("error_code")
                    return state, error_code
            elif kind == "stream_error":
                raise CliRuntimeError(str(item))
            else:
                self._handle_input(kind, item)

    def cancel_turn(self) -> None:
        """Ask the local Orin to cancel this run's turn, once per turn (W6-R2); a failure never holds the exit."""
        turn_id = self.current_turn_id
        if turn_id is None or self.conversation_id is None or self._cancelled_turn == turn_id:
            return
        self._cancelled_turn = turn_id
        try:
            self.client.post(f"/v1/conversations/{self.conversation_id}/cancel", {}, timeout=_CALL_TIMEOUT)
        except Exception as error:  # noqa: BLE001 - best effort: the lease ends the turn when this cannot be sent
            self._debug(f"turn {turn_id} could not be cancelled: {error}")

    # -- stdin ----------------------------------------------------------------------------------------------------

    def _handle_input(self, kind: str, item: Any) -> None:
        if kind == "stdin_closed":
            self._stdin_closed = True
        elif kind == "bad_input":
            self.stderr.write(f"Error: a stdin line is not JSON: {item}\n")
        elif kind == "stdin" and isinstance(item, dict):
            if item.get("type") == "control_request":
                self.bridge.handle(item)
            elif item.get("type") == "user":
                message = item.get("message") if isinstance(item.get("message"), dict) else {}
                self._pending_prompts.append((_prompt_text(message.get("content")), str(item.get("uuid") or "") or None))
            elif item.get("type") == "control_response" and isinstance(item.get("response"), dict):
                self.bridge.answered(item["response"])
            elif item.get("type") == "control_cancel_request":
                pass  # the host gave up on one of its own requests; nothing of Orin's is waiting on it

    # -- entry ----------------------------------------------------------------------------------------------------

    def _check_bypass_policy(self) -> None:
        """Read the folder's policy before the first turn; a run that asks for bypass under it exits (W6-R4)."""
        self.bypass_policy = bypass_disabled(load_effective_settings(self.cwd, trusted=self.options.trust))
        asks = self.options.dangerously_skip_permissions or self.options.permission_mode in ("bypassPermissions", "full_autonomy")
        if self.bypass_policy and asks:
            raise CliUsageError("bypass mode is disabled by policy (permissions.disableBypassPermissionsMode)")

    def run(self) -> int:
        refuse_unsupported(self.options)
        self._check_bypass_policy()
        self._resolve_session()
        with _ACTIVE_LOCK:
            _ACTIVE.add(self)
        try:
            if self.options.input_format == "stream-json":
                return self._run_stream_input()
            prompt = self.options.prompt if self.options.prompt is not None else self.stdin.read()
            if not prompt.strip():
                raise CliUsageError("no prompt: pass one after -p or on stdin")
            result = self.answer(prompt)
            if self.options.output_format == "json":
                self.out.message(result)
            elif self.options.output_format == "text":
                if result["is_error"]:
                    self.stderr.write(f"Error: {'; '.join(result.get('errors') or [result['subtype']])}\n")
                else:
                    self.out.text(str(result.get("result") or "") + "\n")
            return EXIT_ERROR if result["is_error"] else EXIT_OK
        finally:
            self.cancel_turn()  # an exit (Ctrl-C, an error) while a turn runs stops that turn (W6-R2)
            self._stop.set()
            with _ACTIVE_LOCK:
                _ACTIVE.discard(self)

    def _run_stream_input(self) -> int:
        threading.Thread(target=self._read_stdin, daemon=True).start()
        if self.options.prompt:
            self._pending_prompts.append((self.options.prompt, None))
        while True:
            while self._pending_prompts:
                text, prompt_uuid = self._pending_prompts.pop(0)
                self.answer(text, prompt_uuid=prompt_uuid)
            if self._stdin_closed:
                return EXIT_OK
            kind, item = self.inbox.get()
            if kind != "event":
                self._handle_input(kind, item)


__all__ = ["HeadlessRun", "MAX_PROMPT_CHARS", "UNSUPPORTED", "refuse_unsupported"]

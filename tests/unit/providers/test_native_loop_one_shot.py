from __future__ import annotations

import json
from pathlib import Path
import textwrap
import time
from typing import Any, Mapping, Sequence

import pytest

from agentos.agentic.native_turn import native_workdir
from agentos.installation.paths import orin_paths
from agentos.providers.claude_cli.errors import (
    CliSecurityAbort,
    NativeLoopUnavailable,
    ProviderTransportError,
)
from agentos.providers.claude_cli.native_loop import (
    NativeTurnRequest,
    NativeTurnSink,
)
from agentos.providers.claude_cli.provider import ClaudeCliProvider
from agentos.providers.cli_adapter import (
    CliCapabilities,
    CliErrorInfo,
    CliErrorKind,
    CliEvent,
    CliEventKind,
    CliInit,
    CliModelInfo,
    CliResult,
    CliTurnRequest,
    CliUsage,
    RateLimitState,
    ToolServerWiring,
)
from agentos.providers.cli_tools_mcp import (
    McpToolSpec,
    ToolCallRequest,
    ToolCallResult,
)


FAKE_ONESHOT_SCRIPT_CODE = textwrap.dedent(
    """\
    from __future__ import annotations
    import json
    import os
    import sys
    import urllib.request

    def _emit(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    def main() -> int:
        mode = os.environ.get("FAKE_ONESHOT_MODE", "mcp_tool_call")
        # One-shot CLIs read all of stdin until EOF before starting or finishing
        raw_stdin = sys.stdin.read()
        if not raw_stdin.strip():
            return 2

        resume_id = None
        argv = sys.argv[1:]
        if "resume" in argv:
            idx = argv.index("resume")
            if idx + 1 < len(argv):
                resume_id = argv[idx + 1]

        thread_id = resume_id or "thread_oneshot_1"
        _emit({"type": "thread.started", "thread_id": thread_id})

        if mode == "forbidden_item":
            _emit({
                "type": "item.started",
                "item": {"id": "item_1", "type": "command_execution", "command": "rm -rf /"},
            })
            _emit({
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            })
            return 0

        if mode == "error_exit":
            _emit({"type": "turn.failed", "error": {"message": "boom"}})
            return 1

        if mode == "mcp_tool_call":
            mcp_url = os.environ.get("FAKE_ONESHOT_MCP_URL", "")
            mcp_token = os.environ.get("FAKE_ONESHOT_MCP_TOKEN", "")
            req = urllib.request.Request(
                mcp_url,
                data=json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "echo",
                        "arguments": {"text": f"prompt={raw_stdin.strip()}|thread={thread_id}"},
                    },
                }).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {mcp_token}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            tool_out = body["result"]["content"][0]["text"]
            _emit({
                "type": "item.completed",
                "item": {
                    "id": "call_1",
                    "type": "mcp_tool_call",
                    "server": "orin",
                    "tool": "echo",
                    "arguments": {"text": raw_stdin.strip()},
                },
            })
            _emit({
                "type": "item.completed",
                "item": {"id": "msg_1", "type": "agent_message", "text": f"OneShot:{tool_out}"},
            })
            _emit({
                "type": "turn.completed",
                "usage": {"input_tokens": 12, "output_tokens": 6},
            })
            return 0

        return 0

    if __name__ == "__main__":
        raise SystemExit(main())
    """
)


class _OneShotTranslator:
    def __init__(self) -> None:
        self.init: CliInit | None = None
        self.result: CliResult | None = None
        self.rate_limit: RateLimitState | None = None
        self._last_usage: CliUsage | None = None
        self._last_text: str = ""

    def feed_line(self, line: str) -> list[CliEvent]:
        s = line.strip()
        if not s:
            return []
        raw = json.loads(s)
        kind = raw.get("type")
        if kind == "thread.started":
            self.init = CliInit(
                session_id=str(raw.get("thread_id") or ""),
                model="gpt-5.4",
                api_key_source="none",
                mcp_servers={},
                tools=(),
            )
            return [CliEvent(kind=CliEventKind.INIT, init=self.init, raw=raw)]
        if kind in ("item.started", "item.completed"):
            item = raw.get("item") or {}
            itype = str(item.get("type") or "")
            if itype == "agent_message" and kind == "item.completed":
                txt = str(item.get("text") or "")
                self._last_text = txt
                return [CliEvent(kind=CliEventKind.TEXT_DELTA, text=txt, raw=raw)]
            if itype == "mcp_tool_call" and kind == "item.completed":
                return [
                    CliEvent(
                        kind=CliEventKind.TOOL_USE_START,
                        tool_use_id=str(item.get("id") or "call_1"),
                        tool_name=f"mcp__{item.get('server', 'orin')}__{item.get('tool', 'echo')}",
                        raw=raw,
                    ),
                    CliEvent(
                        kind=CliEventKind.TOOL_USE_END,
                        tool_use_id=str(item.get("id") or "call_1"),
                        tool_name=f"mcp__{item.get('server', 'orin')}__{item.get('tool', 'echo')}",
                        tool_input=item.get("arguments") or {},
                        raw=raw,
                    ),
                ]
            return [
                CliEvent(
                    kind=CliEventKind.TOOL_USE_START,
                    tool_use_id=str(item.get("id") or "item_1"),
                    tool_name=itype,
                    raw=raw,
                )
            ]
        if kind == "turn.completed":
            u_raw = raw.get("usage") or {}
            usage = CliUsage(
                input_tokens=int(u_raw.get("input_tokens") or 0),
                output_tokens=int(u_raw.get("output_tokens") or 0),
            )
            self._last_usage = usage
            res = CliResult(
                subtype="success",
                is_error=False,
                result_text=self._last_text,
                stop_reason="end_turn",
                num_turns=1,
                duration_ms=10,
                session_id=self.init.session_id if self.init else "",
                api_error_status=None,
                usage=usage,
                model_usage={"gpt-5.4": usage},
            )
            self.result = res
            return [
                CliEvent(kind=CliEventKind.USAGE, usage=usage, raw=raw),
                CliEvent(kind=CliEventKind.RESULT, result=res, raw=raw),
            ]
        if kind == "turn.failed":
            msg = str((raw.get("error") or {}).get("message") or "turn failed")
            res = CliResult(
                subtype="error",
                is_error=True,
                result_text=msg,
                stop_reason="error",
                num_turns=1,
                duration_ms=10,
                session_id=self.init.session_id if self.init else "",
                api_error_status=500,
                usage=self._last_usage or CliUsage(),
                model_usage={},
            )
            self.result = res
            return [CliEvent(kind=CliEventKind.RESULT, result=res, raw=raw)]
        return []

    def flush_pending(self) -> list[CliEvent]:
        return []

    def reset_result(self) -> None:
        self.result = None

    def latest_usage(self) -> CliUsage | None:
        return self._last_usage

    @property
    def stop_reason(self) -> str | None:
        return self.result.stop_reason if self.result else None


class _FakeOneShotAdapter:
    name = "codex"
    display_name = "Fake One-Shot Codex"
    capabilities = CliCapabilities(
        native_tool_loop=True,
        tool_list_changed=False,
        images_in_tool_results=False,
        persistent_stdin=False,
        reports_tool_inventory=False,
        forbidden_item_kinds=frozenset({"command_execution", "file_change", "web_search"}),
    )

    def __init__(self, script_path: Path) -> None:
        self._script_path = script_path
        self.last_wiring_files: tuple[Path, ...] = ()
        self.built_requests: list[CliTurnRequest] = []

    def resolve_binary(
        self, env: Mapping[str, str] | None = None
    ) -> tuple[str | None, tuple[int, int, int] | None]:
        return str(self._script_path), (0, 114, 0)

    def version_supported(self, version: tuple[int, int, int] | None) -> bool:
        return True

    def wire_tool_server(self, handle: Any, directory: Path) -> ToolServerWiring:
        marker_file = directory / f"oneshot-wiring-{time.monotonic_ns()}.tmp"
        marker_file.write_text("temp-wiring", encoding="utf-8")
        self.last_wiring_files = (marker_file,)
        return ToolServerWiring(
            argv=("-c", f'mcp_servers.orin.url="{handle.url}"'),
            env={
                "FAKE_ONESHOT_MCP_URL": handle.url,
                "FAKE_ONESHOT_MCP_TOKEN": handle.token,
            },
            files=(marker_file,),
        )

    def write_tool_server_config(self, handle: Any, directory: Path) -> Path:
        wiring = self.wire_tool_server(handle, directory)
        return wiring.files[0]

    def encode_user_turn(self, content: list[dict[str, Any]]) -> bytes:
        texts = [str(b.get("text") or "") for b in content if isinstance(b, Mapping)]
        return (" ".join(texts).strip() + "\n").encode("utf-8")

    def build_command(self, request: CliTurnRequest) -> list[str]:
        self.built_requests.append(request)
        cmd = [str(request.binary), "exec", "--json"]
        if request.resume_session_id:
            cmd.extend(["resume", request.resume_session_id])
        if request.tool_server_wiring is not None:
            cmd.extend(request.tool_server_wiring.argv)
        cmd.append("-")
        return cmd

    def loggable_command(self, argv: Sequence[str]) -> list[str]:
        return list(argv)

    def environment(
        self,
        base_env: Mapping[str, str] | None,
        *,
        max_output_tokens: int | None,
        tool_timeout_ms: int | None,
    ) -> dict[str, str]:
        return dict(base_env or {})

    def stream_translator(self) -> _OneShotTranslator:
        return _OneShotTranslator()

    def classify_error(
        self,
        result: CliResult,
        rate_limit: RateLimitState | None,
        *,
        now: float | None = None,
    ) -> CliErrorInfo:
        return CliErrorInfo(
            kind=CliErrorKind.FATAL,
            status_code=500,
            user_message=result.result_text or "boom",
            retryable=False,
            retry_after_seconds=None,
            exception=ProviderTransportError(result.result_text or "boom", status_code=500),
        )

    def models(self) -> Sequence[CliModelInfo]:
        return ()

    def context_window(self, model_id: str | None) -> int:
        return 400_000


class _Sink(NativeTurnSink):
    def __init__(self) -> None:
        self.inits: list[CliInit] = []
        self.texts: list[str] = []
        self.tools_started: list[tuple[str, str]] = []
        self.tools_complete: list[tuple[str, str, Mapping[str, Any]]] = []

    def on_init(self, init: CliInit) -> None:
        self.inits.append(init)

    def on_text(self, text: str) -> None:
        self.texts.append(text)

    def on_thinking(self, text: str) -> None:
        pass

    def on_tool_use_started(self, tool_use_id: str, tool_name: str) -> None:
        self.tools_started.append((tool_use_id, tool_name))

    def on_tool_input_progress(self, tool_use_id: str, tool_name: str, bytes_received: int) -> None:
        pass

    def on_tool_use_complete(
        self, tool_use_id: str, tool_name: str, tool_input: Mapping[str, Any]
    ) -> None:
        self.tools_complete.append((tool_use_id, tool_name, dict(tool_input)))

    def on_assistant_usage(self, message_id: str, usage: CliUsage) -> None:
        pass

    def on_compacted(self) -> None:
        pass

    def on_rate_limit(self, state: RateLimitState) -> None:
        pass


class _EchoExecutor:
    def __init__(self) -> None:
        self.calls: list[ToolCallRequest] = []

    def list_tools(self) -> Sequence[McpToolSpec]:
        return [
            McpToolSpec(
                name="echo",
                description="Echo text back",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                read_only=True,
            )
        ]

    def call_tool(self, request: ToolCallRequest) -> ToolCallResult:
        self.calls.append(request)
        return ToolCallResult(text=f"echoed({request.arguments.get('text')})")


def _write_fake_oneshot_script(tmp_path: Path) -> Path:
    script = tmp_path / "fake_oneshot_cli.py"
    script.write_text(FAKE_ONESHOT_SCRIPT_CODE, encoding="utf-8")
    return script


def test_native_workdir_is_adapter_specific() -> None:
    assert native_workdir("claude") == orin_paths().data / "claude-cli" / "workdir"
    assert native_workdir("codex") == orin_paths().data / "codex-cli" / "workdir"
    assert native_workdir("antigravity") == orin_paths().data / "antigravity-cli" / "workdir"


def test_one_shot_closes_stdin_runs_mcp_tool_and_rejects_send_user(tmp_path: Path) -> None:
    script = _write_fake_oneshot_script(tmp_path)
    adapter = _FakeOneShotAdapter(script)
    executor = _EchoExecutor()
    provider = ClaudeCliProvider(
        binary=str(script),
        adapter=adapter,
        env={"FAKE_ONESHOT_MODE": "mcp_tool_call"},
    )
    cwd = tmp_path / "workdir"
    cwd.mkdir()
    req = NativeTurnRequest(
        model="gpt-5.4",
        system_prompt="You are a test agent.",
        user_content=[{"type": "text", "text": "Hello one-shot"}],
        executor=executor,
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        max_turns=5,
        cwd=cwd,
    )
    handle = provider.start_native_turn(req)
    try:
        # 1. Stdin is closed immediately after writing encode_user_turn
        assert handle._proc.stdin is None or handle._proc.stdin.closed is True

        # 2. Subsequent send_user raises NativeLoopUnavailable("one-shot")
        with pytest.raises(NativeLoopUnavailable) as exc_info:
            handle.send_user([{"type": "text", "text": "Follow up"}])
        assert "one-shot" in str(exc_info.value)

        # 3. Init without tools is accepted and MCP tool call is executed
        sink = _Sink()
        outcome = handle.run_until_result(sink)
        assert outcome.init is not None
        assert outcome.init.session_id == "thread_oneshot_1"
        assert outcome.init.tools == ()
        assert len(executor.calls) == 1
        assert "Hello one-shot" in executor.calls[0].arguments["text"]
        assert outcome.final_text == "OneShot:echoed(prompt=Hello one-shot|thread=thread_oneshot_1)"
    finally:
        handle.close()

    # 4. Wiring files are deleted on close
    for f in adapter.last_wiring_files:
        assert not f.exists()

    # 5. Next turn opens a new process with resume_session_id
    req2 = NativeTurnRequest(
        model="gpt-5.4",
        system_prompt="You are a test agent.",
        user_content=[{"type": "text", "text": "Second turn"}],
        executor=executor,
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        max_turns=5,
        cwd=cwd,
        resume_session_id=outcome.init.session_id,
    )
    handle2 = provider.start_native_turn(req2)
    try:
        sink2 = _Sink()
        outcome2 = handle2.run_until_result(sink2)
        assert outcome2.init is not None
        assert outcome2.init.session_id == "thread_oneshot_1"
        assert adapter.built_requests[-1].resume_session_id == "thread_oneshot_1"
        assert "Second turn" in outcome2.final_text
    finally:
        handle2.close()


def test_forbidden_item_kind_aborts_turn_with_cli_security_abort_and_cleans_wiring_files(
    tmp_path: Path,
) -> None:
    script = _write_fake_oneshot_script(tmp_path)
    adapter = _FakeOneShotAdapter(script)
    provider = ClaudeCliProvider(
        binary=str(script),
        adapter=adapter,
        env={"FAKE_ONESHOT_MODE": "forbidden_item"},
    )
    cwd = tmp_path / "workdir"
    cwd.mkdir()
    req = NativeTurnRequest(
        model="gpt-5.4",
        system_prompt="You are a test agent.",
        user_content=[{"type": "text", "text": "Try native bash"}],
        executor=_EchoExecutor(),
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        max_turns=5,
        cwd=cwd,
    )
    handle = provider.start_native_turn(req)
    wiring_files = adapter.last_wiring_files
    assert len(wiring_files) == 1

    sink = _Sink()
    with pytest.raises(CliSecurityAbort) as exc_info:
        handle.run_until_result(sink)
    assert "command_execution" in str(exc_info.value)

    # Wiring files are cleaned up even when run_until_result aborts with CliSecurityAbort
    for f in wiring_files:
        assert not f.exists()


def test_wiring_files_deleted_when_process_fails_or_popen_raises(tmp_path: Path) -> None:
    script = _write_fake_oneshot_script(tmp_path)
    adapter = _FakeOneShotAdapter(script)

    def _exploding_popen(*args: Any, **kwargs: Any) -> Any:
        raise OSError("spawn failed")

    provider = ClaudeCliProvider(
        binary=str(script),
        adapter=adapter,
        popen_factory=_exploding_popen,
    )
    cwd = tmp_path / "workdir"
    cwd.mkdir()
    req = NativeTurnRequest(
        model="gpt-5.4",
        system_prompt="You are a test agent.",
        user_content=[{"type": "text", "text": "Hello"}],
        executor=_EchoExecutor(),
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        max_turns=5,
        cwd=cwd,
    )
    with pytest.raises(NativeLoopUnavailable):
        provider.start_native_turn(req)

    assert len(adapter.last_wiring_files) == 1
    for f in adapter.last_wiring_files:
        assert not f.exists()


def test_native_turn_runner_spawns_new_process_with_resume_session_id_for_one_shot_adapter(
    tmp_path: Path,
) -> None:
    from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision
    from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
    from agentos.agentic.workspace import ConversationWorkspace
    from tests.unit.agentic.test_native_turn import (
        FakeNativeProvider,
        FakeStore,
        ScriptedHandle,
        _make_init,
        _make_result,
        _make_toolset,
    )

    store = FakeStore(model_id="gpt-5.4")
    workspace = ConversationWorkspace(tmp_path, "c1")
    bus = LifecycleBus(base={"session_id": "c1"})
    stop_calls = 0

    def _on_stop(ev: Any) -> LifecycleDecision | None:
        nonlocal stop_calls
        if ev.name is HookEvent.STOP:
            stop_calls += 1
            if stop_calls == 1:
                return LifecycleDecision(blocked=True, reason="Run verification")
        return None

    bus.subscribe(_on_stop)
    toolset = _make_toolset(workspace, lifecycle=bus)

    def _round_1(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> Any:
        init = CliInit(
            session_id="thread_oneshot_99",
            model="gpt-5.4",
            api_key_source="none",
            mcp_servers={},
            tools=(),
        )
        sink.on_init(init)
        sink.on_text("First one-shot round.")
        return type(
            "Out",
            (),
            {
                "result": _make_result(text="First one-shot round."),
                "init": init,
                "final_text": "First one-shot round.",
                "exit_code": 0,
            },
        )()

    def _round_2(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> Any:
        init = CliInit(
            session_id="thread_oneshot_99",
            model="gpt-5.4",
            api_key_source="none",
            mcp_servers={},
            tools=(),
        )
        sink.on_init(init)
        sink.on_text("Second one-shot round.")
        return type(
            "Out",
            (),
            {
                "result": _make_result(text="Second one-shot round."),
                "init": init,
                "final_text": "Second one-shot round.",
                "exit_code": 0,
            },
        )()

    rounds = [_round_1, _round_2]
    fake_provider = FakeNativeProvider(
        handle_factory=lambda req: ScriptedHandle(req, [rounds.pop(0)])
    )
    fake_provider.adapter = _FakeOneShotAdapter(tmp_path / "dummy.py")  # type: ignore[attr-defined]

    runtime = AgenticTurnRuntime(
        store=store,
        provider=fake_provider,
        toolset=toolset,
        lifecycle=bus,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=400_000),
    )

    out = runtime.run("t1", turn=store.turn)
    assert out.state == "completed"
    assert fake_provider.starts == 2
    assert fake_provider.requests[0].resume_session_id is None
    assert fake_provider.requests[1].resume_session_id == "thread_oneshot_99"
    assert fake_provider.handles[0].sent_users == []


def test_early_stdin_broken_pipe_and_pre_init_error_result_classified_correctly(
    tmp_path: Path,
) -> None:
    import io

    script = _write_fake_oneshot_script(tmp_path)
    adapter = _FakeOneShotAdapter(script)

    class _EarlyExitStdin:
        closed = False

        def write(self, _data: str) -> int:
            raise OSError(22, "Invalid argument")

        def flush(self) -> None:
            raise BrokenPipeError("pipe closed")

        def close(self) -> None:
            self.closed = True
            raise OSError(22, "Invalid argument")

    class _EarlyExitProc:
        pid = 99991

        def __init__(self) -> None:
            self.stdin = _EarlyExitStdin()
            self.stdout = io.StringIO(
                json.dumps(
                    {
                        "type": "turn.failed",
                        "error": {"message": "Not logged in. Run codex login"},
                    }
                )
                + "\n"
            )
            self.stderr = io.StringIO("auth error\n")

        def poll(self) -> int:
            return 1

        def wait(self, timeout: float | None = None) -> int:
            return 1

        def kill(self) -> None:
            pass

        def terminate(self) -> None:
            pass

    provider = ClaudeCliProvider(
        binary=str(script),
        adapter=adapter,
        popen_factory=lambda *a, **kw: _EarlyExitProc(),
    )
    cwd = tmp_path / "workdir"
    cwd.mkdir()
    req = NativeTurnRequest(
        model="gpt-5.4",
        system_prompt="You are a test agent.",
        user_content=[{"type": "text", "text": "Hello"}],
        executor=_EchoExecutor(),
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        max_turns=5,
        cwd=cwd,
    )
    handle = provider.start_native_turn(req)
    sink = _Sink()
    with pytest.raises(ProviderTransportError) as exc_info:
        handle.run_until_result(sink)
    assert not isinstance(exc_info.value, NativeLoopUnavailable)
    assert "Not logged in" in str(exc_info.value)


def test_uninitialized_non_error_result_still_fails_closed(tmp_path: Path) -> None:
    import io

    script = _write_fake_oneshot_script(tmp_path)
    adapter = _FakeOneShotAdapter(script)

    class _NoInitSuccessProc:
        pid = 99992

        def __init__(self) -> None:
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 5, "output_tokens": 2},
                    }
                )
                + "\n"
            )
            self.stderr = io.StringIO("")

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            pass

        def terminate(self) -> None:
            pass

    provider = ClaudeCliProvider(
        binary=str(script),
        adapter=adapter,
        popen_factory=lambda *a, **kw: _NoInitSuccessProc(),
    )
    cwd = tmp_path / "workdir"
    cwd.mkdir()
    req = NativeTurnRequest(
        model="gpt-5.4",
        system_prompt="You are a test agent.",
        user_content=[{"type": "text", "text": "Hello"}],
        executor=_EchoExecutor(),
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        max_turns=5,
        cwd=cwd,
    )
    handle = provider.start_native_turn(req)
    sink = _Sink()
    with pytest.raises(NativeLoopUnavailable) as exc_info:
        handle.run_until_result(sink)
    assert "before system/init" in str(exc_info.value)
    assert sink.texts == []


def test_raw_forbidden_item_aborts_even_when_translator_ignores_it_and_in_text_stream(
    tmp_path: Path,
) -> None:
    import io

    script = _write_fake_oneshot_script(tmp_path)

    class _SilentForbiddenTranslator(_OneShotTranslator):
        def feed_line(self, line: str) -> list[CliEvent]:
            raw = json.loads(line.strip())
            if raw.get("type") == "item.started":
                # Simulate a translator that ignores unknown item types and returns []
                return []
            return super().feed_line(line)

    class _SilentForbiddenAdapter(_FakeOneShotAdapter):
        def stream_translator(self) -> _OneShotTranslator:
            return _SilentForbiddenTranslator()

    adapter = _SilentForbiddenAdapter(script)

    def _make_proc(*_a: Any, **_kw: Any) -> Any:
        lines = [
            json.dumps({"type": "thread.started", "thread_id": "t_1"}),
            json.dumps(
                {
                    "type": "item.started",
                    "item": {"id": "i1", "type": "command_execution", "command": "whoami"},
                }
            ),
            json.dumps(
                {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}
            ),
        ]

        class _P:
            pid = 99993

            def __init__(self) -> None:
                self.stdin = io.StringIO()
                self.stdout = io.StringIO("\n".join(lines) + "\n")
                self.stderr = io.StringIO("")

            def poll(self) -> int:
                return 0

            def wait(self, timeout: float | None = None) -> int:
                return 0

            def kill(self) -> None:
                pass

            def terminate(self) -> None:
                pass

        return _P()

    provider = ClaudeCliProvider(
        binary=str(script),
        adapter=adapter,
        popen_factory=_make_proc,
    )
    cwd = tmp_path / "workdir"
    cwd.mkdir()
    req = NativeTurnRequest(
        model="gpt-5.4",
        system_prompt="You are a test agent.",
        user_content=[{"type": "text", "text": "Hello"}],
        executor=_EchoExecutor(),
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        max_turns=5,
        cwd=cwd,
    )
    handle = provider.start_native_turn(req)
    with pytest.raises(CliSecurityAbort) as exc_info:
        handle.run_until_result(_Sink())
    assert "command_execution" in str(exc_info.value)

    # Also verify ClaudeCliProvider.stream() (text fallback) enforces forbidden_item_kinds
    with pytest.raises(CliSecurityAbort) as exc_info2:
        list(
            provider.stream(
                {
                    "model": "gpt-5.4",
                    "messages": [{"role": "user", "content": "hi"}],
                    "tools": [],
                }
            )
        )
    assert "command_execution" in str(exc_info2.value)


def test_encode_user_turn_rejection_happens_before_popen_and_cleans_wiring(
    tmp_path: Path,
) -> None:
    script = _write_fake_oneshot_script(tmp_path)

    class _TextOnlyAdapter(_FakeOneShotAdapter):
        def encode_user_turn(self, content: list[dict[str, Any]]) -> bytes:
            for block in content:
                if block.get("type") != "text":
                    raise ValueError("Only text blocks are supported by this CLI")
            return super().encode_user_turn(content)

    adapter = _TextOnlyAdapter(script)
    spawned = False

    def _spy_popen(*_a: Any, **_kw: Any) -> Any:
        nonlocal spawned
        spawned = True
        raise AssertionError("Popen should not be called when encode_user_turn rejects input")

    provider = ClaudeCliProvider(
        binary=str(script),
        adapter=adapter,
        popen_factory=_spy_popen,
    )
    cwd = tmp_path / "workdir"
    cwd.mkdir()
    req = NativeTurnRequest(
        model="gpt-5.4",
        system_prompt="You are a test agent.",
        user_content=[{"type": "image", "source": {"type": "base64", "data": "abc"}}],
        executor=_EchoExecutor(),
        cancel_check=lambda: False,
        deadline_monotonic=time.monotonic() + 30.0,
        max_turns=5,
        cwd=cwd,
    )
    with pytest.raises(NativeLoopUnavailable) as exc_info:
        provider.start_native_turn(req)
    assert "Only text blocks are supported" in str(exc_info.value)
    assert spawned is False
    for f in adapter.last_wiring_files:
        assert not f.exists()


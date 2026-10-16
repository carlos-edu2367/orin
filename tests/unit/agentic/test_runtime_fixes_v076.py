from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path
from typing import Any

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import (
    CODE_COMPACTION_INSTRUCTION,
    COMPACTION_INSTRUCTION,
    AgenticTurnRuntime,
)
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from agentos.code_mode.preview_server import PreviewServerManager
from agentos.persistence.postgres.execution_journal import PostgresExecutionJournal
from agentos.persistence.postgres.schema import metadata


def _turn(*, code_mode: str | None = None, execution_id: str = "exec-v076") -> dict[str, Any]:
    turn: dict[str, Any] = {
        "turn_id": "turn-v076",
        "conversation_id": "conv-v076",
        "user_id": "user-1",
        "workspace_id": "ws-1",
        "agent_id": "agent-1",
        "execution_id": execution_id,
        "provider": "openrouter",
        "model_id": "qwen3.5",
        "user_message_id": "user-msg-1",
        "assistant_message_id": "assistant-msg-1",
    }
    if code_mode is not None:
        turn["code_mode"] = code_mode
    return turn


class _RecordingStore:
    def __init__(self, turn: dict[str, Any] | None = None) -> None:
        self.turn = turn or _turn()
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.deltas: list[str] = []
        self.usages: list[dict[str, int | None]] = []
        self.finished: list[tuple[bool, str | None]] = []
        self.reset_called: int = 0

    def load(self, turn_id: str) -> dict[str, Any]:
        return self.turn

    def history_for_turn(self, turn: dict[str, Any]) -> list[dict[str, str]]:
        return [{"role": "user", "content": "Execute task"}]

    def lifecycle(self, turn: dict[str, Any], state: str, **payload: Any) -> None:
        self.events.append((state, dict(payload)))

    def delta(self, turn: dict[str, Any], text: str) -> None:
        self.deltas.append(text)

    def reset_assistant_message(self, turn: dict[str, Any]) -> None:
        self.reset_called += 1
        self.deltas.clear()

    def record_usage(
        self,
        turn: dict[str, Any],
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
    ) -> None:
        self.usages.append(
            {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens}
        )

    def finish(self, turn: dict[str, Any], *, failed: bool = False, code: str | None = None) -> None:
        self.finished.append((failed, code))


class _FakeToolset:
    def __init__(self, handler: Any, *, read_only_names: set[str] | None = None) -> None:
        self._handler = handler
        self._read_only_names = read_only_names or set()

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {"name": "run_subagent", "description": "subagent", "parameters": {"type": "object"}},
            },
            {
                "type": "function",
                "function": {"name": "write_file", "description": "write", "parameters": {"type": "object"}},
            },
            {
                "type": "function",
                "function": {"name": "read_file", "description": "read", "parameters": {"type": "object"}},
            },
            {
                "type": "function",
                "function": {"name": "run_command", "description": "cmd", "parameters": {"type": "object"}},
            },
        ]

    def is_read_only(self, name: str) -> bool:
        return name in self._read_only_names

    def argument_names(self, name: str) -> frozenset[str]:
        return frozenset({"path", "content", "description", "prompt", "command"})

    def invoke(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        return self._handler(name, arguments)


class _FuncProvider:
    def __init__(self, fn: Any) -> None:
        self._fn = fn
        self.calls: list[dict[str, Any]] = []

    def stream(self, request: dict[str, Any]):
        self.calls.append(request)
        return self._fn(request, len(self.calls))


def test_runtime_emits_thinking_delta_and_generating_tool_call() -> None:
    store = _RecordingStore()

    def stream_fn(request: dict[str, Any], step: int):
        if step == 1:
            return iter(
                [
                    NormalizedStreamItem(StreamKind.THINKING, 1, thinking="Pensando profundamente sobre a estrutura "),
                    NormalizedStreamItem(
                        StreamKind.THINKING, 2, thinking="do código antes de invocar a ferramenta de leitura."
                    ),
                    NormalizedStreamItem(
                        StreamKind.TOOL_CALL,
                        3,
                        call_id="call-1",
                        tool_name="read_file",
                        arguments_delta='{"path":',
                    ),
                    NormalizedStreamItem(
                        StreamKind.TOOL_CALL,
                        4,
                        call_id="call-1",
                        tool_name="read_file",
                        arguments_delta='"README.md"}',
                    ),
                    NormalizedStreamItem(StreamKind.FINISH, 5, finish_reason="tool_calls"),
                ]
            )
        return iter(
            [
                NormalizedStreamItem(StreamKind.TEXT, 1, text="Leitura concluída."),
                NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
            ]
        )

    toolset = _FakeToolset(
        lambda name, args: ToolOutcome("succeeded", "Resolvido", "conteúdo do readme", {}),
        read_only_names={"read_file"},
    )
    runtime = AgenticTurnRuntime(store=store, provider=_FuncProvider(stream_fn), toolset=toolset)
    result = runtime.run("turn-v076")

    assert result.state == "completed"
    thinking_events = [payload for state, payload in store.events if state == "thinking_delta"]
    assert len(thinking_events) >= 1
    combined_thinking = "".join(str(p.get("delta") or "") for p in thinking_events)
    assert "Pensando profundamente" in combined_thinking
    assert "ferramenta de leitura" in combined_thinking

    gen_events = [payload for state, payload in store.events if state == "generating_tool_call"]
    assert len(gen_events) >= 1
    assert gen_events[0]["tool_name"] == "read_file"


def test_parallel_run_subagent_and_checkpoint_concurrency(tmp_path: Path) -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from agentos.execution.journal import ExecutionCheckpoint, ExecutionJournalScope

    db_path = tmp_path / "journal.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    metadata.create_all(engine)
    journal = PostgresExecutionJournal(engine)
    store = _RecordingStore(_turn(execution_id="exec-parallel-1"))

    scope = ExecutionJournalScope(
        execution_id="exec-parallel-1",
        user_id="user-1",
        workspace_id="ws-1",
        agent_id="agent-1",
        correlation_id="corr-1",
    )

    def effect_started(turn: dict[str, Any], *, kind: str, invocation_ref: str, request_ref: str) -> str:
        seq = journal.next_checkpoint_sequence(scope)
        time.sleep(0.02)
        journal.save_checkpoint(
            ExecutionCheckpoint(
                checkpoint_id=f"cp-{uuid4().hex}",
                scope=scope,
                sequence=seq,
                execution_state_version=1,
                context_manifest_ref=request_ref,
                next_decision=invocation_ref,
                is_safe=False,
                pending_effect_id=None,
                snapshot={"kind": kind},
                created_at=datetime.now(UTC),
            )
        )
        return f"eff-{invocation_ref}"

    store.effect_started = effect_started  # type: ignore[attr-defined]

    active_count = 0
    max_parallel = 0

    def tool_handler(name: str, args: dict[str, Any]) -> ToolOutcome:
        nonlocal active_count, max_parallel
        active_count += 1
        max_parallel = max(max_parallel, active_count)
        time.sleep(0.08)
        active_count -= 1
        return ToolOutcome(
            "succeeded",
            f"Subagent {args.get('description')}",
            f"subagent done: {args.get('description')}",
            {},
        )

    def stream_fn(request: dict[str, Any], step: int):
        if step == 1:
            return iter(
                [
                    NormalizedStreamItem(
                        StreamKind.TOOL_CALL,
                        1,
                        call_id="sub-1",
                        tool_name="run_subagent",
                        arguments_delta=json.dumps({"description": "Sub 1", "prompt": "P1"}),
                    ),
                    NormalizedStreamItem(
                        StreamKind.TOOL_CALL,
                        2,
                        call_id="sub-2",
                        tool_name="run_subagent",
                        arguments_delta=json.dumps({"description": "Sub 2", "prompt": "P2"}),
                    ),
                    NormalizedStreamItem(
                        StreamKind.TOOL_CALL,
                        3,
                        call_id="sub-3",
                        tool_name="run_subagent",
                        arguments_delta=json.dumps({"description": "Sub 3", "prompt": "P3"}),
                    ),
                    NormalizedStreamItem(StreamKind.FINISH, 4, finish_reason="tool_calls"),
                ]
            )
        for msg in request.get("messages") or []:
            if msg.get("role") == "tool":
                assert "IntegrityError" not in str(msg.get("content", ""))
                assert "UNIQUE constraint failed" not in str(msg.get("content", ""))
        return iter(
            [
                NormalizedStreamItem(StreamKind.TEXT, 1, text="Todos os subagentes concluíram."),
                NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
            ]
        )

    toolset = _FakeToolset(tool_handler, read_only_names={"read_file"})
    runtime = AgenticTurnRuntime(
        store=store,
        provider=_FuncProvider(stream_fn),
        toolset=toolset,
    )
    result = runtime.run("turn-v076")

    assert result.state == "completed"
    assert max_parallel >= 2, f"Expected parallel execution of run_subagent, got max_parallel={max_parallel}"


def test_transient_db_exception_is_sanitized_and_does_not_poison_failed_signatures() -> None:
    store = _RecordingStore()
    attempts = 0

    def tool_handler(name: str, args: dict[str, Any]) -> ToolOutcome:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError(
                "(sqlite3.IntegrityError) UNIQUE constraint failed: execution_checkpoints.execution_id, execution_checkpoints.sequence [SQL: INSERT INTO execution_checkpoints ...]"
            )
        return ToolOutcome("succeeded", "Read ok", "recovered ok", {})

    def stream_fn(request: dict[str, Any], step: int):
        messages = list(request.get("messages") or [])
        if step in (1, 2):
            if step == 2:
                tool_msgs = [m for m in messages if m.get("role") == "tool"]
                assert len(tool_msgs) == 1
                assert "[SQL:" not in str(tool_msgs[0]["content"])
                assert "sqlite3.IntegrityError" not in str(tool_msgs[0]["content"])
            return iter(
                [
                    NormalizedStreamItem(
                        StreamKind.TOOL_CALL,
                        1,
                        call_id=f"call-{step}",
                        tool_name="read_file",
                        arguments_delta=json.dumps({"path": "app.py"}),
                    ),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
                ]
            )
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert any("recovered ok" in str(m.get("content")) for m in tool_msgs)
        return iter(
            [
                NormalizedStreamItem(StreamKind.TEXT, 1, text="Sucesso após retry."),
                NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
            ]
        )

    toolset = _FakeToolset(tool_handler, read_only_names={"read_file"})
    runtime = AgenticTurnRuntime(store=store, provider=_FuncProvider(stream_fn), toolset=toolset)
    result = runtime.run("turn-v076")
    assert result.state == "completed"
    assert attempts == 2


def test_validation_gate_does_not_reset_assistant_message_and_adds_paragraph_break() -> None:
    store = _RecordingStore(_turn(code_mode="code"))
    gate_checks = 0

    def stream_fn(request: dict[str, Any], step: int):
        if step == 1:
            return iter(
                [
                    NormalizedStreamItem(
                        StreamKind.TOOL_CALL,
                        1,
                        call_id="w-1",
                        tool_name="write_file",
                        arguments_delta=json.dumps({"path": "index.html", "content": "<h1>Hi</h1>"}),
                    ),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
                ]
            )
        if step == 2:
            # Finishes after write without running verification/validation yet
            return iter(
                [
                    NormalizedStreamItem(StreamKind.TEXT, 1, text="Primeira entrega detalhada sem quebra final."),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
                ]
            )
        if step == 3:
            # Runs verification read_file
            return iter(
                [
                    NormalizedStreamItem(
                        StreamKind.TOOL_CALL,
                        1,
                        call_id="r-1",
                        tool_name="read_file",
                        arguments_delta=json.dumps({"path": "index.html"}),
                    ),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
                ]
            )
        return iter(
            [
                NormalizedStreamItem(StreamKind.TEXT, 1, text="Segunda entrega complementar verificada."),
                NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
            ]
        )

    toolset = _FakeToolset(
        lambda name, args: ToolOutcome("succeeded", "ok", "ok", {"path": args.get("path", "index.html")}),
        read_only_names={"read_file"},
    )

    def code_completion_gate() -> tuple[bool, str | None]:
        nonlocal gate_checks
        gate_checks += 1
        if gate_checks == 1:
            return False, "Execute a verificação antes de concluir."
        return True, None

    toolset.code_completion_gate = code_completion_gate  # type: ignore[attr-defined]
    runtime = AgenticTurnRuntime(store=store, provider=_FuncProvider(stream_fn), toolset=toolset)
    result = runtime.run("turn-v076")

    assert result.state == "completed"
    assert store.reset_called == 0
    combined_streamed = "".join(store.deltas)
    assert "Primeira entrega detalhada sem quebra final." in combined_streamed
    assert "Segunda entrega complementar verificada." in combined_streamed
    assert "Primeira entrega detalhada sem quebra final.\n\nSegunda entrega complementar verificada." in combined_streamed


def test_estimated_tokens_for_base64_image_is_realistic_and_aged() -> None:
    huge_b64 = "A" * 260_000
    image_msg: dict[str, Any] = {
        "role": "user",
        "_tool_image_result": True,
        "content": [
            {"type": "text", "text": "[Screenshot from browser_take_screenshot]"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{huge_b64}"}},
        ],
    }

    tokens = AgenticTurnRuntime._estimated_tokens(image_msg)
    assert tokens < 2000, f"Expected ~1200 tokens for base64 image block, got {tokens}"

    # Verify aging of older _tool_image_result messages
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "Analise a página"},
        image_msg.copy(),
    ]
    for idx in range(5):
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"call-{idx}",
                "name": "read_file",
                "content": "x" * 600,
            }
        )
    aged = AgenticTurnRuntime._age_tool_results(messages, keep_recent=3)
    assert aged[1]["content"] == "[earlier tool image omitted]"


def test_compaction_instructions_preserve_user_language() -> None:
    assert "language" in COMPACTION_INSTRUCTION.lower()
    assert "language" in CODE_COMPACTION_INSTRUCTION.lower()


def test_preview_server_detects_actual_port_from_stdout_logs(tmp_path: Path) -> None:
    probe_cfg = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe_cfg.bind(("127.0.0.1", 0))
    configured_port = probe_cfg.getsockname()[1]
    probe_cfg.close()

    probe_actual = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe_actual.bind(("127.0.0.1", 0))
    actual_port = probe_actual.getsockname()[1]
    probe_actual.close()

    server_script = tmp_path / "fake_vite.py"
    server_script.write_text(
        f"""import http.server, socketserver, sys
port = {actual_port}
print(f"Port {configured_port} is in use, trying another one...", flush=True)
print(f"\\x1b[32m  ➜  Local:   \\x1b[36mhttp://localhost:{{port}}/\\x1b[0m", flush=True)
handler = http.server.SimpleHTTPRequestHandler
with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
    httpd.serve_forever()
""",
        encoding="utf-8",
    )

    orin_dir = tmp_path / ".orin"
    orin_dir.mkdir(parents=True, exist_ok=True)
    launch_cfg = {
        "version": "0.0.1",
        "configurations": [
            {
                "name": "Vite Frontend",
                "runtimeExecutable": sys.executable,
                "runtimeArgs": [str(server_script)],
                "port": configured_port,
            }
        ],
    }
    (orin_dir / "launch.json").write_text(json.dumps(launch_cfg), encoding="utf-8")

    manager = PreviewServerManager(tmp_path)
    try:
        record = manager.start_server("Vite Frontend", wait_timeout=3.0, navigate_browser=False)
        assert record["port"] == actual_port
        assert record["url"] == f"http://127.0.0.1:{actual_port}"
        assert record["ready"] is True
    finally:
        manager.stop_server("Vite Frontend")


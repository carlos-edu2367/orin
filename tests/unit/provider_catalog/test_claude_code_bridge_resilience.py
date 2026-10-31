from __future__ import annotations

from datetime import UTC, datetime, timedelta
import io
import json
import threading
import time
from typing import Any

import pytest

from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.provider_catalog.claude_code import (
    DEFAULT_CLAUDE_CLI_BRIDGE_URL,
    ProviderTransportError,
    _resolve_native_tool_call,
    stream_claude_code_bridge,
)
from agentos.provider_catalog.opencode import (
    _build_bridge_system_prompt,
    _find_orin_tool_call_close,
    _parse_orin_tool_calls,
    _resolve_tool_name,
)
from agentos.providers.models import FinishReason


def test_parse_orin_tool_calls_with_inner_closing_tag_in_json_string() -> None:
    text = (
        "Vou editar o arquivo.\n"
        '<orin_tool_call>{"name": "Edit", "arguments": {"file_path": "src/opencode.py", '
        '"old_string": "foo", "new_string": "tag = \'</orin_tool_call>\'\\nbar"}}</orin_tool_call>'
    )
    cleaned, calls = _parse_orin_tool_calls(text, allowed_names={"Edit", "Read", "Bash"})
    assert len(calls) == 1
    assert calls[0]["name"] == "Edit"
    assert "</orin_tool_call>" in calls[0]["arguments"]["new_string"]
    assert "Vou editar o arquivo." in cleaned


def test_resolve_tool_name_bidirectional_chat_and_code_modes() -> None:
    # Chat mode allowed_names (snake_case)
    chat_allowed = {"read_file", "write_file", "edit_file", "run_command", "search_files", "list_files"}
    assert _resolve_tool_name("Read", chat_allowed) == "read_file"
    assert _resolve_tool_name("Edit", chat_allowed) == "edit_file"
    assert _resolve_tool_name("Write", chat_allowed) == "write_file"
    assert _resolve_tool_name("Bash", chat_allowed) == "run_command"

    # Code mode allowed_names (PascalCase)
    code_allowed = {"Read", "Write", "Edit", "Bash", "Glob", "Grep"}
    assert _resolve_tool_name("read_file", code_allowed) == "Read"
    assert _resolve_tool_name("edit_file", code_allowed) == "Edit"
    assert _resolve_tool_name("write_file", code_allowed) == "Write"
    assert _resolve_tool_name("bash", code_allowed) == "Bash"
    assert _resolve_tool_name("run_command", code_allowed) == "Bash"
    assert _resolve_tool_name("search_files", code_allowed) == "Grep"
    assert _resolve_tool_name("list_files", code_allowed) == "Glob"


def test_parse_orin_tool_calls_bidirectional_argument_normalization() -> None:
    # 1. Code Mode (PascalCase allowed_names): path -> file_path, old_text -> old_string, new_text -> new_string
    code_text = (
        '<orin_tool_call>{"id": "c1", "name": "read_file", "arguments": {"path": "src/a.py"}}</orin_tool_call>\n'
        '<orin_tool_call>{"id": "c2", "name": "edit_file", "arguments": {"path": "src/a.py", "old_text": "old", "new_text": "new"}}</orin_tool_call>\n'
        '<orin_tool_call>{"id": "c3", "name": "write_file", "arguments": {"path": "src/b.py", "content": "hello"}}</orin_tool_call>'
    )
    _, code_calls = _parse_orin_tool_calls(code_text, allowed_names={"Read", "Edit", "Write"})
    assert len(code_calls) == 3
    assert code_calls[0]["name"] == "Read"
    assert code_calls[0]["arguments"] == {"file_path": "src/a.py"}
    assert json.loads(code_calls[0][2]) == {"file_path": "src/a.py"}

    assert code_calls[1]["name"] == "Edit"
    assert code_calls[1]["arguments"] == {
        "file_path": "src/a.py",
        "old_string": "old",
        "new_string": "new",
    }
    assert json.loads(code_calls[1][2]) == {
        "file_path": "src/a.py",
        "old_string": "old",
        "new_string": "new",
    }

    assert code_calls[2]["name"] == "Write"
    assert code_calls[2]["arguments"] == {"file_path": "src/b.py", "content": "hello"}

    # 2. Chat Mode (snake_case allowed_names): file_path -> path, old_string -> old_text, new_string -> new_text
    chat_text = (
        '<orin_tool_call>{"id": "m1", "name": "Read", "arguments": {"file_path": "src/a.py"}}</orin_tool_call>\n'
        '<orin_tool_call>{"id": "m2", "name": "Edit", "arguments": {"file_path": "src/a.py", "old_string": "old", "new_string": "new"}}</orin_tool_call>\n'
        '<orin_tool_call>{"id": "m3", "name": "Write", "arguments": {"file_path": "src/b.py", "content": "hello"}}</orin_tool_call>'
    )
    _, chat_calls = _parse_orin_tool_calls(
        chat_text, allowed_names={"read_file", "edit_file", "write_file"}
    )
    assert len(chat_calls) == 3
    assert chat_calls[0]["name"] == "read_file"
    assert chat_calls[0]["arguments"] == {"path": "src/a.py"}
    assert json.loads(chat_calls[0][2]) == {"path": "src/a.py"}

    assert chat_calls[1]["name"] == "edit_file"
    assert chat_calls[1]["arguments"] == {
        "path": "src/a.py",
        "old_text": "old",
        "new_text": "new",
    }
    assert json.loads(chat_calls[1][2]) == {
        "path": "src/a.py",
        "old_text": "old",
        "new_text": "new",
    }

    assert chat_calls[2]["name"] == "write_file"
    assert chat_calls[2]["arguments"] == {"path": "src/b.py", "content": "hello"}


def test_find_orin_tool_call_close_handles_escaped_quotes_and_braces_in_strings() -> None:
    payload = (
        '<orin_tool_call>{"name": "Write", "arguments": {"file_path": "x.py", '
        '"content": "def f():\\n    return \\"}</orin_tool_call>\\" + \'</orin_tool_call>\'"}}</orin_tool_call>tail'
    )
    close_idx = _find_orin_tool_call_close(payload, 0)
    assert close_idx != -1
    assert payload[close_idx:] == "</orin_tool_call>tail"


def test_build_bridge_system_prompt_identifies_orin_bridge_and_allows_multiple_calls() -> None:
    prompt = _build_bridge_system_prompt(
        ["Base system."],
        [{"name": "read_file", "description": "Read", "parameters": {"type": "object"}}],
    )
    assert "multiple `<orin_tool_call>`" in prompt
    assert "OpenCode Bridge" not in prompt


def test_stream_claude_code_bridge_incremental_streaming_with_inner_closing_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code._resolve_claude_binary",
        lambda env=None: r"C:\fake\claude.exe",
    )

    chunks = [
        "Vou atualizar o parser agora.\n<orin_tool_call>{\"name\": \"Edit\", \"arguments\": {",
        "\"file_path\": \"src/opencode.py\", \"old_string\": \"old\", ",
        "\"new_string\": \"x = '</orin_tool_call>'\\ny = \\\"</orin_tool_call>\\\"\"}}",
        "</orin_tool_call>\nPronto!",
    ]
    ndjson_lines = [
        json.dumps({"type": "system", "subtype": "init", "apiKeySource": "none"}),
        *[
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": piece},
                    },
                }
            )
            for piece in chunks
        ],
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "usage": {"input_tokens": 10, "output_tokens": 20},
            }
        ),
    ]

    class _SimplePopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            self.pid = 1001
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("\n".join(ndjson_lines) + "\n")
            self.stderr = io.StringIO("")
            self.returncode = 0

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            pass

        def terminate(self) -> None:
            pass

    items = list(
        stream_claude_code_bridge(
            base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "Atualize o parser"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "Edit",
                        "description": "Edit file",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            popen_factory=_SimplePopen,
        )
    )

    tool_items = [i for i in items if i.kind is StreamKind.TOOL_CALL]
    text_items = [i for i in items if i.kind is StreamKind.TEXT]
    assert len(tool_items) == 1
    assert tool_items[0].tool_name == "Edit"
    parsed_args = json.loads(tool_items[0].arguments_delta or "{}")
    assert "</orin_tool_call>" in parsed_args["new_string"]
    visible_text = "".join(i.text or "" for i in text_items)
    assert "Vou atualizar o parser agora." in visible_text
    assert "Pronto!" in visible_text
    assert "<orin_tool_call>" not in visible_text


def test_stream_claude_code_bridge_emits_finish_reason_length_on_max_tokens_and_unclosed_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code._resolve_claude_binary",
        lambda env=None: r"C:\fake\claude.exe",
    )

    class _SimplePopen:
        def __init__(self, lines: list[str]) -> None:
            self.pid = 1002
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("\n".join(lines) + "\n")
            self.stderr = io.StringIO("")
            self.returncode = 0

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            pass

        def terminate(self) -> None:
            pass

    # 1. message_delta stop_reason == "max_tokens"
    max_tokens_lines = [
        json.dumps({"type": "system", "subtype": "init", "apiKeySource": "none"}),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Texto parcial cortado"},
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "message_delta",
                    "delta": {"stop_reason": "max_tokens"},
                    "usage": {"output_tokens": 8192},
                },
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "usage": {"input_tokens": 100, "output_tokens": 8192},
            }
        ),
    ]
    items_max_tokens = list(
        stream_claude_code_bridge(
            base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "Escreva muito"}],
            popen_factory=lambda cmd, **kw: _SimplePopen(max_tokens_lines),
        )
    )
    finish_items_1 = [i for i in items_max_tokens if i.kind is StreamKind.FINISH]
    assert len(finish_items_1) == 1
    assert finish_items_1[0].finish_reason == FinishReason.LENGTH

    # 2. Unclosed <orin_tool_call> truncated at EOF
    unclosed_tag_lines = [
        json.dumps({"type": "system", "subtype": "init", "apiKeySource": "none"}),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "text_delta",
                        "text": (
                            "Vou gravar o arquivo.\n"
                            '<orin_tool_call>{"name": "write_file", "arguments": {"path": "a.py", "content": "def incomplete():'
                        ),
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "usage": {"input_tokens": 50, "output_tokens": 4096},
            }
        ),
    ]
    items_unclosed = list(
        stream_claude_code_bridge(
            base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "Grave a.py"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "description": "Write file",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            popen_factory=lambda cmd, **kw: _SimplePopen(unclosed_tag_lines),
        )
    )
    finish_items_2 = [i for i in items_unclosed if i.kind is StreamKind.FINISH]
    assert len(finish_items_2) == 1
    assert finish_items_2[0].finish_reason == FinishReason.LENGTH


def test_stream_claude_code_bridge_starts_reader_threads_before_stdin_write_and_renews_idle_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code._resolve_claude_binary",
        lambda env=None: r"C:\fake\claude.exe",
    )

    state = {
        "stdout_iter_started": threading.Event(),
        "stderr_read_started": threading.Event(),
        "stdin_written_after_readers": False,
    }

    class _PipeOrderAndSlowStreamPopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            self.pid = 1003
            self.returncode: int | None = None

            class _CheckingStdin:
                def write(self_in, data: str) -> int:
                    # Wait briefly for daemon reader threads that MUST have been started before stdin.write
                    out_ready = state["stdout_iter_started"].wait(timeout=0.5)
                    err_ready = state["stderr_read_started"].wait(timeout=0.5)
                    state["stdin_written_after_readers"] = bool(out_ready and err_ready)
                    return len(data)

                def flush(self_in) -> None:
                    pass

                def close(self_in) -> None:
                    pass

            class _SlowStdout:
                def __iter__(self_out) -> Any:
                    state["stdout_iter_started"].set()
                    packets = [
                        {"type": "system", "subtype": "init", "apiKeySource": "none"},
                        {
                            "type": "stream_event",
                            "event": {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {"type": "text_delta", "text": "parte 1 "},
                            },
                        },
                        {
                            "type": "stream_event",
                            "event": {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {"type": "text_delta", "text": "parte 2 "},
                            },
                        },
                        {
                            "type": "stream_event",
                            "event": {
                                "type": "content_block_delta",
                                "index": 0,
                                "delta": {"type": "text_delta", "text": "parte 3"},
                            },
                        },
                        {
                            "type": "result",
                            "subtype": "success",
                            "is_error": False,
                            "usage": {"input_tokens": 10, "output_tokens": 10},
                        },
                    ]
                    for pkt in packets:
                        time.sleep(0.08)
                        yield json.dumps(pkt) + "\n"
                    self.returncode = 0

            class _CheckingStderr:
                def read(self_err) -> str:
                    state["stderr_read_started"].set()
                    return ""

            self.stdin = _CheckingStdin()
            self.stdout = _SlowStdout()
            self.stderr = _CheckingStderr()

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

        def terminate(self) -> None:
            self.returncode = -15

    # Total stream time is ~0.40s (5 packets * 0.08s), which exceeds timeout_seconds=0.22s,
    # but each packet arrives within 0.08s < 0.22s, so the renewable idle timeout MUST NOT fire.
    items = list(
        stream_claude_code_bridge(
            base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            model="claude-sonnet-4-6",
            messages=[{"role": "user", "content": "Teste de idle timeout e ordem de pipes"}],
            timeout_seconds=0.22,
            popen_factory=_PipeOrderAndSlowStreamPopen,
        )
    )

    assert state["stdin_written_after_readers"] is True
    texts = [i.text or "" for i in items if i.kind is StreamKind.TEXT]
    assert "".join(texts) == "parte 1 parte 2 parte 3"


def test_runtime_renews_deadline_on_deltas_and_retries_transport_error_after_thinking() -> None:
    now_ref = [datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC)]

    class _ThinkingStallThenSuccessProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request: dict[str, object]) -> Any:
            self.calls += 1
            if self.calls == 1:
                def _stalling_stream() -> Any:
                    # Emit thinking deltas first, then fail with ProviderTransportError (no text or tool calls)
                    yield NormalizedStreamItem(
                        StreamKind.THINKING, 1, thinking="Analisando arquitetura..."
                    )
                    raise ProviderTransportError("Claude Code CLI timed out after 180.0s")

                return _stalling_stream()

            def _active_long_stream() -> Any:
                # Advance clock in 60s steps across deltas (total 120s > initial 90s deadline)
                now_ref[0] = now_ref[0] + timedelta(seconds=60)
                yield NormalizedStreamItem(StreamKind.THINKING, 1, thinking="Continuando...")
                now_ref[0] = now_ref[0] + timedelta(seconds=60)
                yield NormalizedStreamItem(StreamKind.TEXT, 2, text="Resposta concluída com sucesso.")
                yield NormalizedStreamItem(StreamKind.FINISH, 3, finish_reason=FinishReason.STOP)

            return _active_long_stream()

    class _Store:
        def __init__(self) -> None:
            self.turn = {
                "turn_id": "t-1",
                "conversation_id": "c-1",
                "user_id": "u-1",
                "execution_id": "e-1",
                "provider": "anthropic",
                "model_id": "claude-sonnet-4-6",
                "user_message_id": "um-1",
                "assistant_message_id": "am-1",
            }
            self.deltas: list[str] = []
            self.events: list[tuple[str, dict[str, object]]] = []

        def load(self, turn_id: str) -> dict[str, object]:
            return self.turn

        def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, object]]:
            return [{"role": "user", "content": "Execute tarefa longa"}]

        def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
            self.events.append((state, payload))

        def delta(self, turn: dict[str, object], text: str) -> None:
            self.deltas.append(text)

        def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
            pass

    store = _Store()
    provider = _ThinkingStallThenSuccessProvider()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        clock=lambda: now_ref[0],
        limits=AgenticLimits(
            deadline=timedelta(seconds=90),
            max_provider_retries=2,
            retry_base_delay_seconds=0.0,
        ),
    )

    result = runtime.run("t-1")
    assert result.state == "completed"
    assert provider.calls == 2
    assert store.deltas == ["Resposta concluída com sucesso."]


def test_adversarial_multiple_tool_calls_with_nested_closing_tags_and_max_wall_clock_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Multiple tool calls where call #1 has nested </orin_tool_call> and escaped quotes/braces,
    # followed by call #2, plus an unclosed <orin_tool_call> tail that must not leak into visible text.
    text = (
        "Iniciando lote.\n"
        '<orin_tool_call>{"id": "t1", "name": "edit_file", "arguments": '
        '{"path": "src/p.py", "old_text": "a", "new_text": "b = \\"}</orin_tool_call>\\""}}</orin_tool_call>\n'
        '<orin_tool_call>{"id": "t2", "name": "run_command", "arguments": {"command": "pytest"}}</orin_tool_call>\n'
        '<orin_tool_call>{"id": "t3", "name": "write_file", "arguments": {"path": "cut.py", "content": "unclosed'
    )
    cleaned, calls = _parse_orin_tool_calls(text, allowed_names={"Edit", "Bash", "Write"})
    assert cleaned == "Iniciando lote."
    assert len(calls) == 2
    assert calls[0]["name"] == "Edit"
    assert calls[0]["arguments"] == {
        "file_path": "src/p.py",
        "old_string": "a",
        "new_string": 'b = "}</orin_tool_call>"',
    }
    assert calls[1]["name"] == "Bash"
    assert calls[1]["arguments"] == {"command": "pytest"}

    # 2. Native tool call resolution also normalizes bidirectionally
    native_code = _resolve_native_tool_call(
        "mcp__orin__edit_file",
        {"path": "src/x.py", "old_text": "1", "new_text": "2"},
        {"Edit"},
    )
    assert native_code is not None
    assert native_code[0] == "Edit"
    assert json.loads(native_code[1]) == {
        "file_path": "src/x.py",
        "old_string": "1",
        "new_string": "2",
    }

    # 3. Hard wall-clock ceiling stops a process that trickles packets forever
    monkeypatch.setattr(
        "agentos.provider_catalog.claude_code._resolve_claude_binary",
        lambda env=None: r"C:\fake\claude.exe",
    )

    class _InfiniteTricklePopen:
        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            self.pid = 1009
            self.returncode: int | None = None
            self.stdin = io.StringIO()
            self.stderr = io.StringIO("")

            class _TrickleStdout:
                def __iter__(self_out) -> Any:
                    yield json.dumps({"type": "system", "subtype": "init", "apiKeySource": "none"}) + "\n"
                    for idx in range(50):
                        time.sleep(0.04)
                        yield json.dumps(
                            {
                                "type": "stream_event",
                                "event": {
                                    "type": "content_block_delta",
                                    "index": 0,
                                    "delta": {"type": "text_delta", "text": f"chunk-{idx} "},
                                },
                            }
                        ) + "\n"

            self.stdout = _TrickleStdout()

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.returncode = -9

        def terminate(self) -> None:
            self.returncode = -15

    with pytest.raises(ProviderTransportError, match="timed out"):
        list(
            stream_claude_code_bridge(
                base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
                model="claude-sonnet-4-6",
                messages=[{"role": "user", "content": "Trickle forever"}],
                idle_timeout_seconds=0.5,
                max_wall_clock_seconds=0.15,
                popen_factory=_InfiniteTricklePopen,
            )
        )

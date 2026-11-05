from __future__ import annotations

from pathlib import Path

import json
import logging
from typing import Any

import pytest

from agentos.agentic.provider_stream import HTTPProviderStreamTransport, StreamKind
from agentos.providers.claude_cli.errors import ProviderTransportError
from agentos.providers.claude_cli.provider import (
    DEFAULT_CLAUDE_CLI_BRIDGE_URL,
    ClaudeCliProvider,
    CliSlots,
)

from .fakes import FakeClaudePopen


def _init_line(model: str = "claude-opus-5-5", *, api_key_source: str = "none") -> str:
    return json.dumps(
        {
            "type": "system",
            "subtype": "init",
            "session_id": "s1",
            "model": model,
            "apiKeySource": api_key_source,
        }
    )


def _text_delta_line(text: str) -> str:
    return json.dumps(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        }
    )


def _thinking_delta_line(text: str) -> str:
    return json.dumps(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": text},
            },
        }
    )


def _result_line(text: str = "OK") -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": text,
            "stop_reason": "end_turn",
            "num_turns": 1,
            "duration_ms": 100,
            "session_id": "s1",
            "usage": {
                "input_tokens": 50,
                "output_tokens": 10,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 30,
            },
        }
    )


def test_text_stream_sends_multiblock_content_and_untranslated_model() -> None:
    spawned: list[FakeClaudePopen] = []

    def factory(cmd: list[str], **kw: Any) -> FakeClaudePopen:
        p = FakeClaudePopen(
            cmd,
            ndjson_lines=[_init_line(), _text_delta_line("Resposta"), _result_line("Resposta")],
            env=kw.get("env"),
            cwd=kw.get("cwd"),
        )
        spawned.append(p)
        return p

    provider = ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model="claude-opus-5-5",
        binary="C:/fake/claude.exe",
        popen_factory=factory,
    )
    try:
        items = list(
            provider.stream(
                {
                    "messages": [
                        {"role": "system", "content": "Stable system"},
                        {"role": "user", "content": "Primeira pergunta"},
                        {"role": "assistant", "content": "Primeira resposta"},
                        {"role": "user", "content": "Segunda pergunta"},
                    ],
                }
            )
        )
        assert any(i.kind == StreamKind.TEXT and i.text == "Resposta" for i in items)
        assert len(spawned) == 1
        fake = spawned[0]
        assert fake.argv[fake.argv.index("--model") + 1] == "claude-opus-5-5"
        assert "--restricted" in fake.argv
        assert "--safe-mode" not in fake.argv
        envelope = json.loads(fake.stdin_writes[0].strip())
        content = envelope["message"]["content"]
        assert isinstance(content, list) and len(content) > 1
    finally:
        provider.close()


def test_text_stream_emits_tool_call_before_process_ends() -> None:
    import threading

    gate = threading.Event()
    emitted: list[StreamKind] = []
    saw_tool_before_gate_released = False
    spawned: list[FakeClaudePopen] = []

    lines = [
        _init_line(),
        _text_delta_line(
            'Vou ler <orin_tool_call>{"name":"read_file","arguments":{"path":"a.py"}}</orin_tool_call>'
        ),
        _text_delta_line(" texto final"),
        _result_line(""),
    ]

    def factory(cmd: list[str], **kw: Any) -> FakeClaudePopen:
        p = FakeClaudePopen(
            cmd,
            ndjson_lines=lines,
            gate_before_line={2: gate},
            env=kw.get("env"),
        )
        spawned.append(p)
        return p

    provider = ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model="claude-opus-5-5",
        binary="C:/fake/claude.exe",
        popen_factory=factory,
    )
    try:
        for item in provider.stream(
            {
                "messages": [{"role": "user", "content": "Leia a.py"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "description": "Read",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
            }
        ):
            emitted.append(item.kind)
            if item.kind == StreamKind.TOOL_CALL and item.arguments_delta:
                if not gate.is_set() and spawned[0].poll() is None:
                    saw_tool_before_gate_released = True
                gate.set()

        assert saw_tool_before_gate_released is True
        tool_idx = emitted.index(StreamKind.TOOL_CALL)
        usage_idx = emitted.index(StreamKind.USAGE)
        finish_idx = emitted.index(StreamKind.FINISH)
        assert tool_idx < usage_idx < finish_idx
    finally:
        gate.set()
        provider.close()


def test_text_stream_kills_process_on_hallucinated_tool_result() -> None:
    spawned: list[FakeClaudePopen] = []
    lines = [
        _init_line(),
        _text_delta_line(
            '<orin_tool_call>{"name":"read_file","arguments":{"path":"a.py"}}</orin_tool_call>\n'
            "[Tool Result id=call_1]inventado[/Tool Result]\n"
            '<orin_tool_call>{"name":"edit_file","arguments":{"path":"a.py"}}</orin_tool_call>'
        ),
        _result_line(""),
    ]

    def factory(cmd: list[str], **kw: Any) -> FakeClaudePopen:
        p = FakeClaudePopen(cmd, ndjson_lines=lines, env=kw.get("env"))
        spawned.append(p)
        return p

    provider = ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model="claude-opus-5-5",
        binary="C:/fake/claude.exe",
        popen_factory=factory,
    )
    try:
        items = list(
            provider.stream(
                {
                    "messages": [{"role": "user", "content": "Leia"}],
                    "tools": [
                        {"type": "function", "function": {"name": "read_file", "parameters": {}}},
                        {"type": "function", "function": {"name": "edit_file", "parameters": {}}},
                    ],
                }
            )
        )
        tool_calls = [i for i in items if i.kind == StreamKind.TOOL_CALL]
        assert [t.tool_name for t in tool_calls] == ["read_file"]
        assert spawned[0].killed is True
    finally:
        provider.close()


def test_text_stream_second_call_reuses_prefix_blocks() -> None:
    spawned: list[FakeClaudePopen] = []

    def factory(cmd: list[str], **kw: Any) -> FakeClaudePopen:
        p = FakeClaudePopen(
            cmd,
            ndjson_lines=[_init_line(), _text_delta_line("ok"), _result_line("ok")],
            env=kw.get("env"),
        )
        spawned.append(p)
        return p

    provider = ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model="claude-opus-5-5",
        binary="C:/fake/claude.exe",
        popen_factory=factory,
    )
    try:
        m1 = [
            {"role": "system", "content": "Sys"},
            {"role": "user", "content": "Passo 1"},
        ]
        list(provider.stream({"messages": m1}))
        m2 = [
            *m1,
            {"role": "assistant", "content": "Vou ler", "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "name": "read_file", "content": "res"},
        ]
        list(provider.stream({"messages": m2}))

        c1 = json.loads(spawned[0].stdin_writes[0])["message"]["content"]
        c2 = json.loads(spawned[1].stdin_writes[0])["message"]["content"]
        assert c2[: len(c1)] == c1
        # System prompt file path is reused across iterations of the same provider instance
        sys_idx_1 = spawned[0].argv.index("--system-prompt-file") + 1
        sys_idx_2 = spawned[1].argv.index("--system-prompt-file") + 1
        assert spawned[0].argv[sys_idx_1] == spawned[1].argv[sys_idx_2]
    finally:
        provider.close()


def test_max_output_tokens_becomes_env() -> None:
    spawned: list[FakeClaudePopen] = []

    provider = ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model="claude-opus-5-5",
        binary="C:/fake/claude.exe",
        popen_factory=lambda cmd, **kw: (
            spawned.append(
                FakeClaudePopen(
                    cmd,
                    ndjson_lines=[_init_line(), _result_line("ok")],
                    env=kw.get("env"),
                )
            )
            or spawned[-1]
        ),
    )
    try:
        list(
            provider.stream(
                {
                    "messages": [{"role": "user", "content": "Oi"}],
                    "max_output_tokens": 4096,
                }
            )
        )
        assert spawned[0].env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4096"
    finally:
        provider.close()


def test_empty_thinking_yields_throttled_progress() -> None:
    ticks = iter([100.0, 100.0, 100.5, 102.5, 103.0])
    lines = [
        _init_line(),
        _thinking_delta_line(""),
        _thinking_delta_line(""),
        _thinking_delta_line(""),
        _result_line("ok"),
    ]

    provider = ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model="claude-opus-5-5",
        binary="C:/fake/claude.exe",
        clock=lambda: next(ticks, 103.0),
        popen_factory=lambda cmd, **kw: FakeClaudePopen(
            cmd, ndjson_lines=lines, env=kw.get("env")
        ),
    )
    try:
        items = list(provider.stream({"messages": [{"role": "user", "content": "Pense"}]}))
        empty_thinking = [
            i for i in items if i.kind == StreamKind.THINKING and i.thinking == ""
        ]
        assert len(empty_thinking) == 2
    finally:
        provider.close()


def test_cli_run_is_logged_without_secrets(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="agentos.providers.claude_cli")
    provider = ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model="claude-opus-5-5",
        binary="C:/fake/claude.exe",
        popen_factory=lambda cmd, **kw: FakeClaudePopen(
            cmd,
            ndjson_lines=[_init_line("claude-opus-5-5"), _result_line("ok")],
            env=kw.get("env"),
        ),
    )
    try:
        list(provider.stream({"messages": [{"role": "user", "content": "Oi"}]}))
        assert "claude_cli run mode=text" in caplog.text
        assert "<redacted>" in caplog.text
        assert "actual=claude-opus-5-5" in caplog.text
    finally:
        provider.close()


def test_http_transport_dispatches_bridge_to_claude_cli_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_stream(self: ClaudeCliProvider, request: Any, *, cancel_check: Any = None) -> Any:
        calls.append({"base_url": self.base_url, "model": self.model, "request": dict(request)})
        return iter(())

    monkeypatch.setattr(ClaudeCliProvider, "stream", fake_stream)
    transport = HTTPProviderStreamTransport(
        provider="anthropic",
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        api_key="",
        model="claude-opus-5-5",
    )
    list(transport.stream({"messages": [{"role": "user", "content": "Oi"}]}))
    assert len(calls) == 1
    assert calls[0]["model"] == "claude-opus-5-5"


def test_cli_slots_respects_env_limit_or_unlimited() -> None:
    unlimited = CliSlots.from_env({})
    assert unlimited.limit is None
    assert unlimited.acquire() is True
    unlimited.release()

    limited = CliSlots.from_env({"ORIN_CLI_MAX_PROCESSES": "1"})
    assert limited.limit == 1
    assert limited.acquire() is True
    assert limited.acquire(cancel_check=lambda: True) is False
    limited.release()


def test_stream_uses_isolated_cwd_and_requires_init_with_strict_api_key_source_none() -> None:
    captured_cwds: list[str] = []

    def _popen_capture(cmd: list[str], **kw: Any) -> FakeClaudePopen:
        captured_cwds.append(str(kw.get("cwd") or ""))
        return FakeClaudePopen(
            cmd,
            ndjson_lines=[_init_line("claude-opus-5-5", api_key_source="none"), _result_line("ok")],
            env=kw.get("env"),
        )

    provider = ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model="claude-opus-5-5",
        binary="C:/fake/claude.exe",
        popen_factory=_popen_capture,
    )
    try:
        list(provider.stream({"messages": [{"role": "user", "content": "Oi"}]}))
        assert len(captured_cwds) == 1
        from agentos.providers.claude_cli.command import ensure_isolated_cwd

        assert captured_cwds[0] is not None
        assert Path(captured_cwds[0]).resolve() == ensure_isolated_cwd().resolve()
        assert Path(captured_cwds[0]).name.startswith("orin-claude-cli-cwd")
    finally:
        provider.close()

    # Empty apiKeySource ("") must be rejected
    provider_empty_src = ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model="claude-opus-5-5",
        binary="C:/fake/claude.exe",
        popen_factory=lambda cmd, **kw: FakeClaudePopen(
            cmd,
            ndjson_lines=[_init_line("claude-opus-5-5", api_key_source=""), _result_line("ok")],
            env=kw.get("env"),
        ),
    )
    try:
        with pytest.raises(ProviderTransportError, match="apiKeySource"):
            list(provider_empty_src.stream({"messages": [{"role": "user", "content": "Oi"}]}))
    finally:
        provider_empty_src.close()

    # Content emitted before system/init must be rejected
    provider_no_init = ClaudeCliProvider(
        base_url=DEFAULT_CLAUDE_CLI_BRIDGE_URL,
        model="claude-opus-5-5",
        binary="C:/fake/claude.exe",
        popen_factory=lambda cmd, **kw: FakeClaudePopen(
            cmd,
            ndjson_lines=[_text_delta_line("Hello without init"), _result_line("Hello without init")],
            env=kw.get("env"),
        ),
    )
    try:
        with pytest.raises(ProviderTransportError, match="system/init"):
            list(provider_no_init.stream({"messages": [{"role": "user", "content": "Oi"}]}))
    finally:
        provider_no_init.close()


from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.quality import TurnQualityCounters
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
import agentos.providers.claude_cli.provider as provider_mod
from agentos.providers.claude_cli.provider import (
    DEFAULT_CLAUDE_CLI_BRIDGE_URL,
    text_context_budget,
)
from agentos.providers.claude_cli.stream import StreamUsage
from agentos.providers.models import FinishReason, Measurement
from agentos.workers.chat import ChatWorker


def test_window_is_catalog_window_for_cli_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = ChatWorker.__new__(ChatWorker)
    monkeypatch.setattr(
        worker,
        "_catalog_row_for",
        lambda _turn: {
            "base_url": DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            "context_window": 1_000_000,
        },
    )
    monkeypatch.setattr(provider_mod, "TEXT_CACHE_OK", True)
    turn = {
        "user_id": "u1",
        "provider": "anthropic",
        "model_id": "claude-opus-5-5",
    }
    assert worker._context_window_for(turn) == 1_000_000


def test_text_budget_applies_only_when_cache_not_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_mod, "TEXT_CACHE_OK", False)
    assert text_context_budget({}) == 200_000
    assert text_context_budget({"ORIN_CLAUDE_CLI_TEXT_CONTEXT_BUDGET": "300000"}) == 300_000
    assert text_context_budget({"ORIN_CLAUDE_CLI_TEXT_CONTEXT_BUDGET": "10000"}) == 50_000

    monkeypatch.setattr(provider_mod, "TEXT_CACHE_OK", True)
    assert text_context_budget({}) is None
    assert text_context_budget({"ORIN_CLAUDE_CLI_TEXT_CONTEXT_BUDGET": "250000"}) == 250_000


def test_prefix_caching_true_on_cli_bridge_when_cache_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _StubProvider:
        base_url = DEFAULT_CLAUDE_CLI_BRIDGE_URL

    rt = AgenticTurnRuntime(
        store=object(),  # type: ignore[arg-type]
        provider=_StubProvider(),  # type: ignore[arg-type]
        actions=object(),  # type: ignore[arg-type]
        limits=AgenticLimits(
            deadline=timedelta(minutes=5),
            max_iterations=5,
            max_actions=10,
            max_context_tokens=200_000,
        ),
    )
    monkeypatch.setattr(provider_mod, "TEXT_CACHE_OK", True)
    assert rt._prefix_caching({"provider": "anthropic"}) is True

    monkeypatch.setattr(provider_mod, "TEXT_CACHE_OK", False)
    monkeypatch.setattr(provider_mod, "native_tools_enabled", lambda **kw: False)
    assert rt._prefix_caching({"provider": "anthropic"}) is False


def test_no_aging_between_iterations_when_prefix_caching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(provider_mod, "TEXT_CACHE_OK", True)
    monkeypatch.setattr("agentos.agentic.runtime.RECENT_UNAGED_TOOL_RESULTS", 1)
    big_tool_output = "LINE_DATA_" + ("x" * 2000)
    captured_requests: list[dict[str, Any]] = []

    class _ThreeIterProvider:
        base_url = DEFAULT_CLAUDE_CLI_BRIDGE_URL

        def __init__(self) -> None:
            self.call_num = 0

        def stream(self, request: dict[str, Any], **_kw: Any) -> Any:
            self.call_num += 1
            captured_requests.append(
                {
                    "messages": [dict(m) for m in request.get("messages", [])],
                }
            )
            if self.call_num == 1:
                yield NormalizedStreamItem(
                    StreamKind.TOOL_CALL,
                    1,
                    tool_call_id="c1",
                    tool_name="read_file",
                    arguments_delta='{"path": "a.py"}',
                )
                yield NormalizedStreamItem(
                    StreamKind.FINISH, 2, finish_reason=FinishReason.TOOL_CALLS
                )
            elif self.call_num == 2:
                yield NormalizedStreamItem(
                    StreamKind.TOOL_CALL,
                    1,
                    tool_call_id="c2",
                    tool_name="read_file",
                    arguments_delta='{"path": "b.py"}',
                )
                yield NormalizedStreamItem(
                    StreamKind.FINISH, 2, finish_reason=FinishReason.TOOL_CALLS
                )
            else:
                yield NormalizedStreamItem(
                    StreamKind.TEXT, 1, text="Concluído."
                )
                yield NormalizedStreamItem(
                    StreamKind.FINISH, 2, finish_reason=FinishReason.STOP
                )

    class _FakeActions:
        def tool_schemas(self, turn: Any) -> list[dict[str, Any]]:
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]

        def execute(
            self,
            calls: list[dict[str, Any]],
            turn: Any,
            *,
            action_count: int = 0,
            max_actions: int | None = None,
        ) -> Any:
            results = [
                {
                    "id": str(c.get("id") or ""),
                    "name": str(c.get("name") or "read_file"),
                    "status": "succeeded",
                    "content": big_tool_output if c.get("id") == "c1" else "short",
                }
                for c in calls
            ]
            return type(
                "_Batch",
                (),
                {"count": action_count + len(results), "results": results},
            )()

    class _FakeStore:
        def __init__(self) -> None:
            self.turn = {
                "turn_id": "t-age",
                "conversation_id": "c-age",
                "user_id": "u-1",
                "execution_id": "e-1",
                "provider": "anthropic",
                "model_id": "claude-opus-5-5",
                "user_message_id": "um-1",
                "assistant_message_id": "am-1",
            }

        def load(self, turn_id: str) -> dict[str, object]:
            return self.turn

        def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, object]]:
            return [{"role": "user", "content": "Leia a.py e b.py"}]

        def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
            pass

        def delta(self, turn: dict[str, object], text: str) -> None:
            pass

        def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
            pass

    rt = AgenticTurnRuntime(
        store=_FakeStore(),
        provider=_ThreeIterProvider(),
        actions=_FakeActions(),
        clock=lambda: datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC),
        limits=AgenticLimits(
            deadline=timedelta(minutes=5),
            max_iterations=5,
            max_actions=10,
            max_context_tokens=500_000,
        ),
    )
    res = rt.run("t-age")
    assert res.state == "completed"
    assert len(captured_requests) == 3
    third_msgs = captured_requests[2]["messages"]
    c1_contents: list[str] = []
    for m in third_msgs:
        if m.get("role") == "tool" and m.get("tool_call_id") == "c1":
            c1_contents.append(str(m.get("content") or ""))
        elif isinstance(m.get("content"), list):
            for block in m["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") == "c1":
                    c1_contents.append(str(block.get("content") or ""))
    assert len(c1_contents) == 1
    assert c1_contents[0] == big_tool_output


def test_counters_keep_cache_creation() -> None:
    counters = TurnQualityCounters()
    counters.note_usage(
        input_tokens=100,
        output_tokens=20,
        cached_input_tokens=80,
        cache_creation_input_tokens=15,
    )
    counters.settle_provider_call()
    row = counters.as_row()
    assert row["cached_input_tokens"] == 80
    assert row["cache_creation_input_tokens"] == 15


def test_runtime_close_closes_provider_and_unregisters_atexit(
    tmp_path: Path,
) -> None:
    """Red Team #2: AgenticTurnRuntime.close() closes ClaudeCliProvider, unlinks prompt temp file, and unregisters atexit."""
    provider = provider_mod.ClaudeCliProvider()
    prompt_path = provider._ensure_prompt_file("System prompt test")
    assert prompt_path.exists()

    rt = AgenticTurnRuntime(
        store=object(),  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
        actions=object(),  # type: ignore[arg-type]
    )
    rt.close()
    assert not prompt_path.exists()
    assert provider._prompt_file_path is None


def test_window_overrides_stale_200k_db_row_for_1m_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red Team #9: Even if DB catalog row has legacy 200_000 for claude-opus-5-5, bridge uses 1_000_000."""
    worker = ChatWorker.__new__(ChatWorker)
    monkeypatch.setattr(
        worker,
        "_catalog_row_for",
        lambda _turn: {
            "base_url": DEFAULT_CLAUDE_CLI_BRIDGE_URL,
            "context_window": 200_000,
        },
    )
    monkeypatch.setattr(provider_mod, "TEXT_CACHE_OK", True)
    turn = {
        "user_id": "u1",
        "provider": "anthropic",
        "model_id": "claude-opus-5-5",
        "code_mode": "off",
    }
    assert worker._context_window_for(turn) == 1_000_000


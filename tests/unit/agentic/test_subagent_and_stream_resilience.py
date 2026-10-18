from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, update

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind, normalize_sse
from agentos.agentic import runtime as runtime_module
from agentos.agentic.runtime import (
    AGED_TOOL_RESULT_CHARS,
    AgenticLimits,
    AgenticTurnRuntime,
)
from agentos.agentic.session import SUBAGENT_MAX_ACTIONS, TurnSession
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.schema import conversation_dispatches, metadata
from agentos.workers import chat as chat_module
from agentos.workers import publisher as publisher_module
from agentos.workers.chat import ChatWorker


def _turn(provider: str = "openrouter") -> dict[str, object]:
    return {
        "turn_id": "turn-resilience-1",
        "conversation_id": "conv-resilience-1",
        "user_id": "user-1",
        "workspace_id": "ws-1",
        "agent_id": "agent-main",
        "execution_id": "exe-resilience-1",
        "provider": provider,
        "model_id": "model-1",
        "user_message_id": "msg-user-1",
        "assistant_message_id": "msg-assistant-1",
    }


class _SimpleStore:
    def __init__(self, provider: str = "openrouter") -> None:
        self.turn = _turn(provider)
        self.events: list[tuple[str, dict[str, object]]] = []
        self.deltas: list[str] = []
        self.finished: list[tuple[bool, str | None]] = []
        self.steps: list[dict[str, object]] = []

    def load(self, turn_id: str) -> dict[str, object]:
        return self.turn

    def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, str]]:
        return [{"role": "user", "content": "Analise os arquivos do projeto"}]

    def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
        self.events.append((state, payload))

    def delta(self, turn: dict[str, object], text: str) -> None:
        self.deltas.append(text)

    def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
        self.finished.append((failed, code))

    def record_step(self, turn: dict[str, object], **values: object) -> None:
        self.steps.append(dict(values))

    def record(self, *args: object, **kwargs: object) -> None:
        return None

    def main_agent_id(self, turn: dict[str, object]) -> str:
        return "agent-main"


class _MemoryAgentsStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.states: list[tuple[str, str]] = []

    def create(
        self,
        name: str,
        role: str,
        *,
        parent_agent_id: str,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        agent_id = f"agent:{name.lower()}"
        created = agent_id not in self.rows
        self.rows[agent_id] = {
            "agent_id": agent_id,
            "name": name,
            "role": role,
            "parent_agent_id": parent_agent_id,
            "provider": provider,
            "model_id": model_id,
            "state": "idle",
        }
        return {**self.rows[agent_id], "created": created}

    def find(self, name: str) -> dict[str, Any] | None:
        return self.rows.get(f"agent:{name.lower()}")

    def set_state(self, agent_id: str, state: str) -> None:
        self.states.append((agent_id, state))

    def list(self) -> list[dict[str, Any]]:
        return list(self.rows.values())

    def record_usage(self, *args: object, **kwargs: object) -> None:
        return None


def test_fix1_age_tool_results_keeps_at_least_8_recent_reads_and_uses_1600_char_cap() -> None:
    assert AGED_TOOL_RESULT_CHARS == 1600

    class MultiChunkReadProvider:
        def __init__(self) -> None:
            self.requests: list[list[dict[str, object]]] = []

        def stream(self, request: dict[str, object]):
            import json

            self.requests.append(json.loads(json.dumps(request["messages"], default=str)))
            idx = len(self.requests)
            if idx <= 5:
                return [
                    NormalizedStreamItem(
                        StreamKind.TOOL_CALL,
                        1,
                        tool_call_id=f"call-{idx}",
                        tool_name="read_file",
                        arguments_delta=f'{{"path":"large.py","offset":{idx * 100}}}',
                    ),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
                ]
            return [
                NormalizedStreamItem(StreamKind.TEXT, 1, text="Leitura concluída."),
                NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
            ]

    class ChunkToolset:
        def schemas(self, allowed: object = None) -> list[dict[str, object]]:
            return []

        def is_read_only(self, name: str) -> bool:
            return True

        def argument_names(self, name: str) -> None:
            return None

        def invoke(self, name: str, arguments: dict[str, object]) -> ToolOutcome:
            offset = arguments.get("offset", 0)
            body = f"CHUNK_{offset}_START:" + ("X" * 2200) + f":CHUNK_{offset}_END"
            return ToolOutcome("succeeded", "ok", body, {})

    provider = MultiChunkReadProvider()
    store = _SimpleStore(provider="openrouter")
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=ChunkToolset(),
        limits=AgenticLimits(max_iterations=10, max_actions=10),
    )

    result = runtime.run("turn-resilience-1")
    assert result.state == "completed"
    # On the 6th provider call (after 5 sequential read_file iterations), all 5 tool
    # results must remain uncompressed because 5 <= 8 (keep_recent=max(8, len(results))).
    final_messages = provider.requests[5]
    tool_msgs = [m for m in final_messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 5
    for idx, msg in enumerate(tool_msgs, start=1):
        content = str(msg.get("content") or "")
        assert "compressed:" not in content
        assert f"CHUNK_{idx * 100}_END" in content


def test_fix2_subagent_limits_are_configurable_like_parent_agent(tmp_path: Path) -> None:
    from agentos.agentic.settings import AgentRuntimeSettingsStore

    settings_store = AgentRuntimeSettingsStore(tmp_path / "runtime.json")
    assert settings_store.get("user-1") == {"max_iterations": None}
    saved = settings_store.set_max_iterations("user-1", 48, subagent_max_iterations=64)
    assert saved == {"max_iterations": 48, "subagent_max_iterations": 64}
    assert settings_store.get("user-1") == {"max_iterations": 48, "subagent_max_iterations": 64}

    class CountingReadProvider:
        def __init__(self, stop_at: int | None = None) -> None:
            self.calls = 0
            self.stop_at = stop_at

        def stream(self, request: dict[str, object]):
            self.calls += 1
            if self.stop_at is not None and self.calls >= self.stop_at:
                return [
                    NormalizedStreamItem(StreamKind.TEXT, 1, text="Concluído após mais de 20 ações."),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
                ]
            return [
                NormalizedStreamItem(
                    StreamKind.TOOL_CALL,
                    1,
                    tool_call_id=f"sub-call-{self.calls}",
                    tool_name="list_files",
                    arguments_delta='{"path":"."}',
                ),
                NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
            ]

    # 1. When unlimited (None), subagent can exceed 20 actions without an artificial cap
    unlimited_provider = CountingReadProvider(stop_at=26)
    store = _SimpleStore()
    agents = _MemoryAgentsStore()
    agents.create("Explore", "explora", parent_agent_id="agent-main")
    unlimited_session = TurnSession(
        turn=dict(store.turn),
        store=store,
        agents_store=agents,
        memory_store=None,
        provider_factory=lambda: unlimited_provider,
        workspace_root=tmp_path,
        limits=AgenticLimits(max_iterations=None, max_actions=None),
    )
    unlimited_outcome = unlimited_session._ask_agent("Explore", "leia 25+ arquivos sem cap de 20")
    assert unlimited_outcome.status == "succeeded"
    assert unlimited_provider.calls == 26

    # 2. When subagent_max_iterations / subagent_max_actions is configured, subagent enforces it
    bounded_provider = CountingReadProvider(stop_at=None)
    bounded_session = TurnSession(
        turn=dict(store.turn),
        store=store,
        agents_store=agents,
        memory_store=None,
        provider_factory=lambda: bounded_provider,
        workspace_root=tmp_path,
        limits=AgenticLimits(
            max_iterations=None,
            max_actions=None,
            subagent_max_iterations=5,
            subagent_max_actions=5,
        ),
    )
    bounded_outcome = bounded_session._ask_agent("Explore", "respeite o limite configurado")
    assert bounded_outcome.status == "failed"
    assert bounded_provider.calls <= 7



def test_fix3_subagent_discards_intermediate_narration_and_retries_on_empty_post_tool_response(
    tmp_path: Path,
) -> None:
    (tmp_path / "sample.txt").write_text("conteúdo importante\n", encoding="utf-8")

    batches = [
        # Iteration 1: intermediate pre-tool narration + tool call
        [
            NormalizedStreamItem(StreamKind.TEXT, 1, text="Vou ler as partes restantes do arquivo agora..."),
            NormalizedStreamItem(
                StreamKind.TOOL_CALL,
                2,
                tool_call_id="call-read-1",
                tool_name="read_file",
                arguments_delta='{"path":"sample.txt"}',
            ),
            NormalizedStreamItem(StreamKind.FINISH, 3, finish_reason="tool_calls"),
        ],
        # Iteration 2: provider returns empty stop without text or tool calls after tool execution
        [
            NormalizedStreamItem(StreamKind.FINISH, 1, finish_reason="stop"),
        ],
        # Iteration 3: after runtime retry prompt, subagent produces its actual final report
        [
            NormalizedStreamItem(StreamKind.TEXT, 1, text="Relatório final consolidado sem narração antiga."),
            NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
        ],
    ]

    class ScriptedSubProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request: dict[str, object]):
            idx = self.calls
            self.calls += 1
            return iter(batches[idx])

    sub_provider = ScriptedSubProvider()
    store = _SimpleStore()
    agents = _MemoryAgentsStore()
    agents.create("Reviewer", "revisor", parent_agent_id="agent-main")

    session = TurnSession(
        turn=dict(store.turn),
        store=store,
        agents_store=agents,
        memory_store=None,
        provider_factory=lambda: sub_provider,
        workspace_root=tmp_path,
    )

    outcome = session._ask_agent("Reviewer", "analise sample.txt")
    assert outcome.status == "succeeded"
    assert sub_provider.calls == 3
    assert "Relatório final consolidado sem narração antiga." in outcome.content
    assert "Vou ler as partes restantes do arquivo agora..." not in outcome.content


def test_fix4_sse_keepalive_comments_check_cancellation_and_stall_timeout() -> None:
    assert getattr(runtime_module, "MAX_STREAM_KEEPALIVE_STALL_SECONDS", None) == 180.0

    # Part A: normalize_sse yields keepalive items on SSE comments
    items = list(
        normalize_sse(
            [
                ": OPENROUTER PROCESSING",
                'data: {"choices":[{"delta":{"content":"oi"},"finish_reason":"stop"}]}',
                "data: [DONE]",
            ],
            provider="openrouter",
        )
    )
    assert items[0].kind is StreamKind.KEEPALIVE
    assert items[0].get("type") == "keepalive"
    assert items[1].kind is StreamKind.TEXT

    # Part B: cancellation is observed immediately during keepalive comments
    cancel_flag = {"cancelled": False}

    class KeepaliveCancelProvider:
        def stream(self, request: dict[str, object]):
            def gen():
                yield ": OPENROUTER PROCESSING"
                cancel_flag["cancelled"] = True
                yield ": OPENROUTER PROCESSING"
                yield 'data: {"choices":[{"delta":{"content":"never-reached"},"finish_reason":"stop"}]}'
                yield "data: [DONE]"

            return normalize_sse(gen(), provider="openrouter")

    store_cancel = _SimpleStore()
    runtime_cancel = AgenticTurnRuntime(
        store=store_cancel,
        provider=KeepaliveCancelProvider(),
        cancelled=lambda _turn: cancel_flag["cancelled"],
    )
    res_cancel = runtime_cancel.run("turn-resilience-1")
    assert res_cancel.state == "cancelled"
    assert res_cancel.error_code == "TURN_CANCELLED"
    assert store_cancel.deltas == []

    # Part C: 180s of keepalive comments without token progress triggers retry/timeout
    now_ref = [datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)]

    class StallingKeepaliveProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request: dict[str, object]):
            self.calls += 1
            if self.calls == 1:
                def stalled_gen():
                    yield ": OPENROUTER PROCESSING"
                    now_ref[0] = now_ref[0] + timedelta(seconds=181)
                    yield ": OPENROUTER PROCESSING"

                return normalize_sse(stalled_gen(), provider="openrouter")
            return normalize_sse(
                [
                    'data: {"choices":[{"delta":{"content":"recuperado após stall"},"finish_reason":"stop"}]}',
                    "data: [DONE]",
                ],
                provider="openrouter",
            )

    stalling_provider = StallingKeepaliveProvider()
    store_stall = _SimpleStore()
    runtime_stall = AgenticTurnRuntime(
        store=store_stall,
        provider=stalling_provider,
        clock=lambda: now_ref[0],
        limits=AgenticLimits(deadline=timedelta(seconds=600), max_provider_retries=1),
    )
    res_stall = runtime_stall.run("turn-resilience-1")
    assert res_stall.state == "completed"
    assert stalling_provider.calls == 2
    assert store_stall.deltas == ["recuperado após stall"]


def test_fix5_publisher_runs_pending_before_recover_once_and_worker_refreshes_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite://", future=True)
    metadata.create_all(engine)
    store = PostgresChatStore(engine)

    receipt = store.create(
        user_id="user-1",
        message="queued while previous turn ran",
        provider="openrouter",
        model_id="model-1",
        idempotency_key="idem-queued-1",
    )
    # Simulate that this turn was queued 60 seconds ago (> UNCLAIMED_AFTER=45s) while an earlier turn ran
    past = datetime.now(UTC) - timedelta(seconds=60)
    with engine.begin() as conn:
        conn.execute(
            update(conversation_dispatches)
            .where(conversation_dispatches.c.turn_id == receipt.turn_id)
            .values(queued_at=past)
        )

    claimed_turns: list[str] = []
    heartbeats: list[str] = []
    orig_heartbeat = store.heartbeat

    def tracking_heartbeat(component: str) -> None:
        heartbeats.append(component)
        orig_heartbeat(component)

    monkeypatch.setattr(store, "heartbeat", tracking_heartbeat)

    class DummyRuntime:
        def __init__(self, turn: dict[str, object]) -> None:
            self._turn = turn

        def run(self, turn_id: str) -> Any:
            from agentos.agentic.runtime import AgenticRunResult

            claimed_turns.append(turn_id)
            store.delta(self._turn, " executou com sucesso")
            store.finish(self._turn)
            return AgenticRunResult("completed", 1, 0)

    worker = ChatWorker(store, runtime_factory=lambda turn: DummyRuntime(turn))
    monkeypatch.setattr(worker, "_project", lambda *args, **kwargs: None)

    # Execute one tick of publisher via poll_once when next_sweep is already due (0.0)
    ran_work, _next_sweep = publisher_module.poll_once(worker, 0.0)
    assert ran_work is True
    assert claimed_turns == [receipt.turn_id]
    conv = store.get(receipt.conversation_id, "user-1")
    assert conv["state"] == "completed"

    # Also verify periodic heartbeat refresh on ChatWorker._refresh_heartbeat_if_due
    before_count = len(heartbeats)
    worker._last_heartbeat_mono = 0.0
    worker._refresh_heartbeat_if_due()
    assert len(heartbeats) == before_count + 1


def test_fix6_subagent_runs_in_code_mode_loop_and_recovers_partial_output_like_claude_code(
    tmp_path: Path,
) -> None:
    captured_requests: list[dict[str, object]] = []

    class CodeSubagentProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request: dict[str, object]):
            self.calls += 1
            captured_requests.append(request)
            if self.calls == 1:
                return [
                    NormalizedStreamItem(
                        StreamKind.TEXT,
                        1,
                        text="Achado parcial importante em src/portal.ts:42.",
                    ),
                    NormalizedStreamItem(
                        StreamKind.TOOL_CALL,
                        2,
                        tool_call_id="call-glob-1",
                        tool_name="glob_files",
                        arguments_delta='{"path":"."}',
                    ),
                    NormalizedStreamItem(StreamKind.FINISH, 3, finish_reason="tool_calls"),
                ]
            # 2nd call hits the action limit with another tool call
            return [
                NormalizedStreamItem(
                    StreamKind.TOOL_CALL,
                    1,
                    tool_call_id="call-glob-2",
                    tool_name="glob_files",
                    arguments_delta='{"path":"."}',
                ),
                NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="tool_calls"),
            ]

    store = _SimpleStore()
    store.turn["code_mode"] = "code"
    store.turn["permission_mode"] = "accept_edits"
    agents = _MemoryAgentsStore()
    agents.create("general", "general-purpose", parent_agent_id="agent-main")

    session = TurnSession(
        turn=dict(store.turn),
        store=store,
        agents_store=agents,
        memory_store=None,
        provider_factory=lambda: CodeSubagentProvider(),
        workspace_root=tmp_path,
        limits=AgenticLimits(
            max_iterations=None,
            max_actions=None,
            subagent_max_iterations=3,
            subagent_max_actions=1,
        ),
    )
    (session.workspace.root / "CLAUDE.md").write_text("REGRA_CLAUDE_MD_PROJETO_123\n", encoding="utf-8")

    outcome = session._ask_agent("general", "Inspecione o repositório")
    # 1. Subagent in Code Mode receives CODE_MODE_CORE_TOOLS (including glob_files, without propose_code_session)
    tools_sent = [
        str((t.get("function") or {}).get("name"))
        for t in (captured_requests[0].get("tools") or [])  # type: ignore[union-attr]
    ]
    assert "Glob" in tools_sent
    assert "ProposeCodeSession" not in tools_sent

    # 2. Subagent prompt includes workspace guidance (CLAUDE.md) and Claude Code operational rules
    sys_msgs = [
        str(m.get("content") or "")
        for m in (captured_requests[0].get("messages") or [])  # type: ignore[union-attr]
        if m.get("role") == "system"
    ]
    combined_sys = "\n".join(sys_msgs)
    assert "REGRA_CLAUDE_MD_PROJETO_123" in combined_sys

    # 3. Even though subagent hit subagent_max_actions=1 on the 2nd tool call, it recovers partial output
    # with a NOTE banner and usage metadata instead of failing with SUBAGENT_FAILED!
    assert outcome.status == "succeeded"
    assert "PARTIAL" in outcome.content
    assert "Achado parcial importante em src/portal.ts:42." in outcome.content
    assert outcome.payload.get("partial") is True
    assert isinstance(outcome.payload.get("usage"), dict)


from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import Any

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.session import TurnSession
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine


TURN = {
    "turn_id": "turn-wave-promo",
    "conversation_id": "conv-wave-promo",
    "user_id": "user-1",
    "execution_id": "exe-wave-promo",
    "provider": "litellm",
    "model_id": "~anthropic/claude-opus-4-6",
    "user_message_id": "msg-user",
    "assistant_message_id": "msg-assistant",
    "code_mode": "code",
}


class _QueueStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queued: list[str] = []

    def enqueue(self, text: str) -> None:
        with self._lock:
            self._queued.append(text)

    def has_queued_inputs(self, _turn: object) -> bool:
        with self._lock:
            return bool(self._queued)

    def claim_queued_inputs(self, _turn: object) -> list[dict[str, str]]:
        with self._lock:
            items = [{"content": text, "queue_id": f"q-{i}"} for i, text in enumerate(self._queued)]
            self._queued.clear()
            return items

    def load(self, _turn_id: str) -> dict[str, Any]:
        return dict(TURN)

    def history_for_turn(self, _turn: object) -> list[dict[str, str]]:
        return [{"role": "user", "content": "Run parallel subagents"}]

    def delta(self, _turn: object, text: str) -> None:
        pass

    def finish(self, _turn: object, *, failed: bool = False, code: str | None = None) -> None:
        pass

    def record(self, *args: Any, **kwargs: Any) -> None:
        pass

    def main_agent_id(self, turn: dict[str, Any]) -> str:
        return f"agent:{turn['conversation_id']}:main"


def test_foreground_wave_promotes_all_running_subagents_when_user_sends_message(tmp_path: Path) -> None:
    db_file = tmp_path / "wave_promo.db"
    engine = create_local_engine(f"sqlite:///{db_file.as_posix()}")
    metadata.create_all(engine)

    store = _QueueStore()
    agents_store = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])

    both_started = threading.Barrier(3, timeout=5.0)
    release_wave = threading.Event()
    release_third = threading.Event()
    third_started = threading.Event()

    class _BlockingChildProvider:
        def __init__(self, tag: str) -> None:
            self.tag = tag

        def stream(self, _request: dict[str, Any]):
            if self.tag in {"sub-1", "sub-2"}:
                both_started.wait()
                release_wave.wait(timeout=5.0)
            else:
                third_started.set()
                release_third.wait(timeout=5.0)
            return iter(
                [
                    NormalizedStreamItem(StreamKind.TEXT, 1, text=f"Result from {self.tag}"),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
                ]
            )

    counter = 0
    counter_lock = threading.Lock()

    def _child_factory(_model: str):
        nonlocal counter
        with counter_lock:
            counter += 1
            return _BlockingChildProvider(f"sub-{counter}")

    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=agents_store,
        memory_store=None,
        provider_factory=lambda: _BlockingChildProvider("main"),
        workspace_root=tmp_path,
        child_provider_factory=_child_factory,
    )

    outcomes: dict[int, ToolOutcome] = {}

    def _run_fg(idx: int) -> None:
        rec = agents_store.create(
            f"explore-{idx}",
            "Explorer",
            parent_agent_id=session.main_agent_id,
            provider="litellm",
            model_id="~anthropic/claude-opus-4-6",
            force_new=True,
        )
        outcomes[idx] = session._run_foreground_subagent_watching_queue(
            record=rec,
            agent_id=str(rec["agent_id"]),
            clean=f"explore-{idx}",
            builtin_key="explore",
            builtin_spec=session._subagent_spec("explore"),
            child_model_id="~anthropic/claude-opus-4-6",
            current_model_id="~anthropic/claude-opus-4-6",
            task_text=f"Task {idx}",
            description=f"Desc {idx}",
            resumed_agent_id=None,
            caller=session._main_caller(),
            max_depth=2,
            routing={},
        )

    t1 = threading.Thread(target=_run_fg, args=(1,), daemon=True)
    t2 = threading.Thread(target=_run_fg, args=(2,), daemon=True)
    t1.start()
    t2.start()

    # Wait until both foreground subagents are actively running inside their worker threads
    both_started.wait()

    t0 = time.monotonic()
    store.enqueue("Pare e considere esta nova instrução do usuário!")
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)
    elapsed = time.monotonic() - t0

    try:
        assert not t1.is_alive() and not t2.is_alive()
        assert elapsed < 1.0, f"Wave promotion took too long: {elapsed:.2f}s"

        assert outcomes[1].payload.get("promoted_to_background") is True
        assert outcomes[2].payload.get("promoted_to_background") is True

        contents = [outcomes[1].content, outcomes[2].content]
        with_user_msg = [c for c in contents if "Pare e considere esta nova instrução do usuário!" in c]
        with_sibling_note = [
            c for c in contents if "The user sent a message; it was delivered with another subagent's result." in c
        ]
        assert len(with_user_msg) == 1, contents
        assert len(with_sibling_note) == 1, contents

        # A third foreground subagent started AFTER the promotion has the new sequence and is NOT promoted
        t3 = threading.Thread(target=_run_fg, args=(3,), daemon=True)
        t3.start()
        assert third_started.wait(timeout=2.0)
        time.sleep(0.12)
        assert t3.is_alive(), "Third subagent should not have been promoted by the earlier wave"
        release_third.set()
        t3.join(timeout=2.0)
        assert not t3.is_alive()
        assert outcomes[3].payload.get("promoted_to_background") is not True
        assert "Result from sub-3" in outcomes[3].content
    finally:
        release_wave.set()
        release_third.set()
        session.close_background_subagents()
        engine.dispose()


def test_fast_batch_finishing_does_not_steal_queue_from_slow_foreground_subagent(
    tmp_path: Path,
) -> None:
    """Regression test for Red Team #4:
    When a fast parallel tool batch finishes while a slow foreground subagent is still active
    (_active_batches == 2), _execute_batch must not steal claim_queued_inputs without promoting
    the remaining foreground subagent wave.
    """
    from agentos.agentic.native_turn import RuntimeToolExecutor, TOOL_USE_ID_META_KEY
    from agentos.providers.cli_tools_mcp import ToolCallRequest, ToolCallResult

    db_file = tmp_path / "wave_no_steal.db"
    engine = create_local_engine(f"sqlite:///{db_file.as_posix()}")
    metadata.create_all(engine)

    store = _QueueStore()
    agents_store = ConversationAgentStore(
        engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"]
    )

    slow_started = threading.Event()
    fast_entered = threading.Event()
    release_fast = threading.Event()
    release_slow = threading.Event()

    class _Provider:
        def __init__(self, tag: str) -> None:
            self.tag = tag

        def stream(self, _request: dict[str, Any]):
            if self.tag == "sub-slow":
                slow_started.set()
                release_slow.wait(timeout=5.0)
            elif self.tag == "sub-fast":
                fast_entered.set()
                release_fast.wait(timeout=5.0)
            return iter(
                [
                    NormalizedStreamItem(StreamKind.TEXT, 1, text=f"{self.tag} finished unpromoted!"),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason="stop"),
                ]
            )

    tags = ["sub-slow", "sub-fast"]
    tag_lock = threading.Lock()

    def _child_factory(_model: str):
        with tag_lock:
            tag = tags.pop(0) if tags else "sub-extra"
        return _Provider(tag)

    session = TurnSession(
        turn=dict(TURN),
        store=store,
        agents_store=agents_store,
        memory_store=None,
        provider_factory=lambda: _Provider("main"),
        workspace_root=tmp_path,
        child_provider_factory=_child_factory,
    )
    runtime = session.build_runtime()
    executor = RuntimeToolExecutor(runtime, session.turn, gather_seconds=0.01)

    results: dict[str, ToolCallResult] = {}

    def _call_agent(key: str, call_id: str) -> None:
        results[key] = executor.call_tool(
            ToolCallRequest(
                name="Agent",
                arguments={"description": key, "prompt": f"Run {key}", "subagent_type": "explore"},
                meta={TOOL_USE_ID_META_KEY: call_id, "message_id": "m_wave"},
            )
        )

    t_slow = threading.Thread(target=_call_agent, args=("slow", "tu_slow"), daemon=True)
    t_fast = threading.Thread(target=_call_agent, args=("fast", "tu_fast"), daemon=True)
    t_slow.start()
    assert slow_started.wait(timeout=3.0)
    t_fast.start()
    assert fast_entered.wait(timeout=3.0)

    # Enqueue user message right as fast completes its subagent run
    store.enqueue("Nova mensagem urgente durante a onda!")
    release_fast.set()

    t_fast.join(timeout=2.0)
    t_slow.join(timeout=2.0)

    try:
        assert not t_fast.is_alive()
        assert not t_slow.is_alive(), "slow subagent remained blocked instead of promoting!"
        assert "continues in the background" in results["slow"].text
        assert "slow finished unpromoted!" not in results["slow"].text
    finally:
        release_slow.set()
        session.close_background_subagents()
        engine.dispose()


"""Tests for reentrant AgenticTurnRuntime._run_toolset, ask_agents eligibility, and post-mutation _writes."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from typing import Any, Mapping

from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from tests.unit.agentic.test_native_turn import FakeNativeProvider, FakeStore


def test_overlapping_run_toolset_calls_complete_with_consistent_state(tmp_path: Path) -> None:
    """Two overlapping runtime._run_toolset calls run concurrently without state corruption."""
    both_invoked = threading.Barrier(2, timeout=3.0)
    release = threading.Event()

    def _fake_run_subagent(
        subagent_type: str,
        task: str,
        *,
        description: str = "",
        model_id: str | None = None,
        agent_id: str | None = None,
        run_in_background: bool = False,
        **_: Any,
    ) -> ToolOutcome:
        both_invoked.wait(timeout=3.0)
        assert release.wait(timeout=3.0)
        if task == "fail":
            return ToolOutcome("failed", "subagent failed", f"err:{task}", {}, "SUBAGENT_FAILED")
        return ToolOutcome("succeeded", "subagent ok", f"ok:{task}", {})

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = AgentToolset(
        workspace,
        code_mode_active=True,
        public_tool_names=True,
        run_subagent=_fake_run_subagent,
    )
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=20, context_window_tokens=1_000_000),
    )

    calls_1 = [{"id": "c1", "name": "run_subagent", "arguments": '{"subagent_type": "explore", "task": "t1"}'}]
    calls_2 = [{"id": "c2", "name": "run_subagent", "arguments": '{"subagent_type": "explore", "task": "t2"}'}]

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(runtime._run_toolset, store.turn, calls_1)
        f2 = pool.submit(runtime._run_toolset, store.turn, calls_2)
        # Wait until both are blocked inside _fake_run_subagent (proving _toolset_state_lock is not held during invoke)
        deadline_Wait = threading.Event()
        # The barrier in _fake_run_subagent only passes if both threads entered invoke simultaneously!
        release.set()
        r1 = f1.result(timeout=3.0)
        r2 = f2.result(timeout=3.0)

    assert len(r1) == 1 and r1[0]["status"] == "succeeded" and "ok:t1" in str(r1[0]["content"])
    assert len(r2) == 1 and r2[0]["status"] == "succeeded" and "ok:t2" in str(r2[0]["content"])
    assert runtime.counters.tool_calls == 2
    assert runtime._consecutive_tool_failures == {}


def test_ask_agents_is_eligible_for_parallel_pool_in_run_toolset(tmp_path: Path) -> None:
    """ask_agents is treated as concurrency-safe in _run_toolset and runs in the parallel pool."""
    both_in_pool = threading.Barrier(2, timeout=3.0)

    def _fake_delegate_batch(tasks: list[Mapping[str, Any]]) -> ToolOutcome:
        both_in_pool.wait(timeout=3.0)
        label = ",".join(str(t.get("task") or "") for t in tasks)
        return ToolOutcome("succeeded", "batch ok", f"batch:{label}", {})

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = AgentToolset(
        workspace,
        code_mode_active=False,
        public_tool_names=False,
        delegate_batch=_fake_delegate_batch,
    )
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    calls = [
        {"id": "b1", "name": "ask_agents", "arguments": '{"tasks": [{"name": "explore", "task": "one"}]}'},
        {"id": "b2", "name": "ask_agents", "arguments": '{"tasks": [{"name": "explore", "task": "two"}]}'},
    ]
    results = runtime._run_toolset(store.turn, calls)
    assert len(results) == 2
    assert results[0]["status"] == "succeeded" and "batch:one" in str(results[0]["content"])
    assert results[1]["status"] == "succeeded" and "batch:two" in str(results[1]["content"])


def test_read_during_mutating_subagent_is_not_reused_from_cache_after_subagent_finishes(
    tmp_path: Path,
) -> None:
    """Incrementing _writes at the end of a mutating call invalidates reads taken while the subagent was running."""
    subagent_started = threading.Event()
    read_finished = threading.Event()

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    target = workspace.root / "shared.txt"
    target.write_text("v1\n", encoding="utf-8")

    def _mutating_subagent(
        subagent_type: str,
        task: str,
        **_kw: Any,
    ) -> ToolOutcome:
        subagent_started.set()
        assert read_finished.wait(timeout=3.0)
        # Subagent modifies the file right before finishing
        target.write_text("v2_from_subagent\n", encoding="utf-8")
        return ToolOutcome("succeeded", "subagent edited file", "done", {})

    toolset = AgentToolset(
        workspace,
        code_mode_active=True,
        public_tool_names=True,
        run_subagent=_mutating_subagent,
    )
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_sub = pool.submit(
            runtime._run_toolset,
            store.turn,
            [{"id": "s1", "name": "run_subagent", "arguments": '{"subagent_type": "general", "task": "edit"}'}],
        )
        assert subagent_started.wait(timeout=3.0)

        # While the subagent is still running, perform a read of shared.txt
        r_mid = runtime._run_toolset(
            store.turn,
            [{"id": "r1", "name": "read_file", "arguments": '{"path": "shared.txt"}'}],
        )
        assert r_mid[0]["status"] == "succeeded"
        assert "v1" in str(r_mid[0]["content"])
        read_finished.set()

        res_sub = f_sub.result(timeout=3.0)
        assert res_sub[0]["status"] == "succeeded"

    # Now read shared.txt again with the exact same arguments (and force_reload=True or same signature);
    # cached_read in _run_toolset must NOT serve the stale v1 read from before the subagent finished!
    r_after = runtime._run_toolset(
        store.turn,
        [{"id": "r2", "name": "read_file", "arguments": '{"path": "shared.txt"}'}],
    )
    assert r_after[0]["status"] == "succeeded"
    assert "reaproveitado da chamada anterior" not in str(r_after[0]["content"])
    assert "v2_from_subagent" in str(r_after[0]["content"])

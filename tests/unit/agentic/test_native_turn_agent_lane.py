"""Tests for ToolBatchCollector 3-lane concurrency ('agent', 'read', 'write') and _RUNNING_ENTRIES."""
from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.native_turn import (
    ABANDONED_BEFORE_START,
    RuntimeToolExecutor,
    TOOL_USE_ID_META_KEY,
    ToolBatchCollector,
    _BatchEntry,
)
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.cli_tools_mcp import ToolCallRequest, ToolCallResult


def test_three_agent_lane_calls_across_gather_window_overlap_in_run_batch() -> None:
    """3 submit(..., lane='agent') staggered at 0, 150ms, 400ms (> window) all enter run_batch together."""
    all_three_inside = threading.Barrier(3, timeout=3.0)
    release = threading.Event()
    active_lock = threading.Lock()
    active_count = 0
    max_active = 0

    def _run_batch(entries: list[_BatchEntry], msg_key: str) -> list[ToolCallResult]:
        nonlocal active_count, max_active
        with active_lock:
            active_count += 1
            if active_count > max_active:
                max_active = active_count
        try:
            all_three_inside.wait(timeout=3.0)
            assert release.wait(timeout=3.0)
            return [ToolCallResult(text=f"agent:{e.call_id}") for e in entries]
        finally:
            with active_lock:
                active_count -= 1

    collector = ToolBatchCollector(_run_batch, gather_seconds=0.05)

    def _submit_delayed(cid: str, delay: float) -> ToolCallResult:
        if delay > 0:
            time.sleep(delay)
        return collector.submit(
            cid,
            "run_subagent",
            {"task": cid},
            message_id="m1",
            read_only=False,
            lane="agent",
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        f1 = pool.submit(_submit_delayed, "a1", 0.0)
        f2 = pool.submit(_submit_delayed, "a2", 0.15)
        f3 = pool.submit(_submit_delayed, "a3", 0.40)
        # Wait until all 3 have arrived at the barrier inside _run_batch
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with active_lock:
                if active_count == 3:
                    break
            time.sleep(0.01)
        with active_lock:
            assert active_count == 3
        release.set()
        r1 = f1.result(timeout=3.0)
        r2 = f2.result(timeout=3.0)
        r3 = f3.result(timeout=3.0)

    assert r1.text == "agent:a1"
    assert r2.text == "agent:a2"
    assert r3.text == "agent:a3"
    assert max_active == 3


def test_writer_waits_for_running_agents_and_has_preference_over_later_agent() -> None:
    """Write waits for 2 running agents; a 3rd agent arriving while writer waits runs AFTER the writer."""
    two_agents_in = threading.Barrier(2, timeout=3.0)
    release_first_agents = threading.Event()
    writer_entered = threading.Event()
    release_writer = threading.Event()
    order_lock = threading.Lock()
    events: list[str] = []

    def _run_batch(entries: list[_BatchEntry], msg_key: str) -> list[ToolCallResult]:
        cid = entries[0].call_id
        with order_lock:
            events.append(f"enter:{cid}")
        if cid in {"a1", "a2"}:
            two_agents_in.wait(timeout=3.0)
            assert release_first_agents.wait(timeout=3.0)
        elif cid == "w1":
            writer_entered.set()
            assert release_writer.wait(timeout=3.0)
        with order_lock:
            events.append(f"exit:{cid}")
        return [ToolCallResult(text=f"ok:{cid}") for _ in entries]

    collector = ToolBatchCollector(_run_batch, gather_seconds=0.02)

    with ThreadPoolExecutor(max_workers=4) as pool:
        fa1 = pool.submit(collector.submit, "a1", "run_subagent", {}, message_id="m1", lane="agent")
        fa2 = pool.submit(collector.submit, "a2", "run_subagent", {}, message_id="m1", lane="agent")

        # Wait until both a1 and a2 are inside _run_batch
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with order_lock:
                if "enter:a1" in events and "enter:a2" in events:
                    break
            time.sleep(0.01)

        # Submit writer w1; it must wait for a1 and a2
        fw1 = pool.submit(collector.submit, "w1", "edit_file", {}, message_id="m2", read_only=False)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if getattr(collector._gate, "_exclusive_waiting", 0) > 0:
                break
            time.sleep(0.01)
        assert getattr(collector._gate, "_exclusive_waiting", 0) == 1
        assert not writer_entered.is_set()

        # Submit 3rd agent a3 while writer w1 is waiting; writer preference must hold a3 back
        fa3 = pool.submit(collector.submit, "a3", "run_subagent", {}, message_id="m3", lane="agent")
        time.sleep(0.25)  # spans across an EXEC_WAIT_POLL_SECONDS slice
        with order_lock:
            assert "enter:w1" not in events
            assert "enter:a3" not in events

        # Release a1 and a2 -> w1 must enter before a3
        release_first_agents.set()
        assert fa1.result(timeout=3.0).text == "ok:a1"
        assert fa2.result(timeout=3.0).text == "ok:a2"
        assert writer_entered.wait(timeout=3.0)
        with order_lock:
            assert "enter:a3" not in events

        release_writer.set()
        assert fw1.result(timeout=3.0).text == "ok:w1"
        assert fa3.result(timeout=3.0).text == "ok:a3"

    assert events.index("exit:a1") < events.index("enter:w1")
    assert events.index("exit:a2") < events.index("enter:w1")
    assert events.index("exit:w1") < events.index("enter:a3")


def test_read_batch_runs_concurrently_with_agent_but_two_read_batches_serialize() -> None:
    """Read batch runs alongside an in-flight agent call, while two read batches stay serialized."""
    agent_entered = threading.Event()
    release_agent = threading.Event()
    r1_entered = threading.Event()
    release_r1 = threading.Event()
    r2_entered = threading.Event()

    def _run_batch(entries: list[_BatchEntry], msg_key: str) -> list[ToolCallResult]:
        cid = entries[0].call_id
        if cid == "a1":
            agent_entered.set()
            assert release_agent.wait(timeout=3.0)
        elif cid == "r1":
            r1_entered.set()
            assert release_r1.wait(timeout=3.0)
        elif cid == "r2":
            r2_entered.set()
        return [ToolCallResult(text=f"ok:{e.call_id}") for e in entries]

    collector = ToolBatchCollector(_run_batch, gather_seconds=0.01)

    with ThreadPoolExecutor(max_workers=3) as pool:
        fa1 = pool.submit(collector.submit, "a1", "run_subagent", {}, message_id="m_a", lane="agent")
        assert agent_entered.wait(timeout=3.0)

        # Submit read batch 1 while agent is running -> enters immediately after gather window
        fr1 = pool.submit(collector.submit, "r1", "read_file", {}, message_id="m_r1", read_only=True)
        assert r1_entered.wait(timeout=3.0)

        # Submit read batch 2 while read batch 1 is running -> blocked by _read_mutex
        fr2 = pool.submit(collector.submit, "r2", "read_file", {}, message_id="m_r2", read_only=True)
        time.sleep(0.1)
        assert not r2_entered.is_set()

        # Release r1 -> r2 runs even while agent a1 is still running
        release_r1.set()
        assert fr1.result(timeout=3.0).text == "ok:r1"
        assert r2_entered.wait(timeout=3.0)
        assert fr2.result(timeout=3.0).text == "ok:r2"

        release_agent.set()
        assert fa1.result(timeout=3.0).text == "ok:a1"


def test_abandoned_agent_call_waiting_behind_writer_returns_abandoned_without_running() -> None:
    writer_entered = threading.Event()
    release_writer = threading.Event()
    ran: list[str] = []

    def _run_batch(entries: list[_BatchEntry], msg_key: str) -> list[ToolCallResult]:
        ran.extend(e.call_id for e in entries)
        if entries[0].call_id == "w1":
            writer_entered.set()
            assert release_writer.wait(timeout=3.0)
        return [ToolCallResult(text=f"ok:{e.call_id}") for e in entries]

    collector = ToolBatchCollector(_run_batch, gather_seconds=0.01)
    with ThreadPoolExecutor(max_workers=2) as pool:
        fw = pool.submit(collector.submit, "w1", "write_file", {}, message_id="m1", read_only=False)
        assert writer_entered.wait(timeout=3.0)

        cancel_ev = threading.Event()
        fa = pool.submit(
            collector.submit,
            "a_abandoned",
            "run_subagent",
            {},
            message_id="m2",
            lane="agent",
            cancelled=cancel_ev,
        )
        time.sleep(0.05)
        cancel_ev.set()
        res_a = fa.result(timeout=2.0)
        assert res_a.is_error is True
        assert res_a.text == ABANDONED_BEFORE_START

        release_writer.set()
        assert fw.result(timeout=2.0).text == "ok:w1"

    assert "a_abandoned" not in ran


def test_running_call_abandoned_is_scoped_per_batch_via_contextvar() -> None:
    both_entered = threading.Barrier(2, timeout=3.0)
    cancel_checked = threading.Barrier(2, timeout=3.0)
    cancel_a1 = threading.Event()
    observed: dict[str, tuple[bool, bool]] = {}

    def _run_batch(entries: list[_BatchEntry], msg_key: str) -> list[ToolCallResult]:
        cid = entries[0].call_id
        both_entered.wait(timeout=3.0)
        if cid == "a1":
            cancel_a1.set()
        cancel_checked.wait(timeout=3.0)
        direct = collector.running_call_abandoned()
        # Also verify in a child thread spawned with contextvars.copy_context().run (as _run_toolset pool does)
        with ThreadPoolExecutor(max_workers=1) as inner_pool:
            inherited = inner_pool.submit(
                contextvars.copy_context().run, collector.running_call_abandoned
            ).result(timeout=2.0)
        observed[cid] = (direct, inherited)
        return [ToolCallResult(text=f"ok:{cid}") for _ in entries]

    collector = ToolBatchCollector(_run_batch, gather_seconds=0.01)
    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(
            collector.submit,
            "a1",
            "run_subagent",
            {},
            message_id="m1",
            lane="agent",
            cancelled=cancel_a1,
        )
        f2 = pool.submit(
            collector.submit,
            "a2",
            "run_subagent",
            {},
            message_id="m1",
            lane="agent",
            cancelled=threading.Event(),
        )
        assert f1.result(timeout=3.0).text == "ok:a1"
        assert f2.result(timeout=3.0).text == "ok:a2"

    assert observed["a1"] == (True, True)
    assert observed["a2"] == (False, False)


def test_two_staggered_check_subagent_calls_overlap_instead_of_adding_waits(tmp_path: Path) -> None:
    """Regression test for P5: two check_subagent(wait_seconds=1) separated by 300ms finish in ~1s (< 1.8s)."""
    from tests.unit.agentic.test_native_turn import FakeNativeProvider, FakeStore

    def _fake_check_subagent(agent_id: str, wait_seconds: int = 0) -> ToolOutcome:
        time.sleep(min(1.0, float(wait_seconds)))
        return ToolOutcome("succeeded", f"checked {agent_id}", f"done:{agent_id}", {})

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    toolset = AgentToolset(
        workspace,
        code_mode_active=True,
        public_tool_names=True,
        check_subagent=_fake_check_subagent,
        run_subagent=lambda *_a, **_kw: ToolOutcome("succeeded", "ok", "ok", {}),
    )
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    executor = RuntimeToolExecutor(runtime, store.turn, gather_seconds=0.1)

    def _call_check(call_id: str, agent_id: str, delay: float) -> ToolCallResult:
        if delay > 0:
            time.sleep(delay)
        return executor.call_tool(
            ToolCallRequest(
                name="CheckSubagent",
                arguments={"agent_id": agent_id, "wait_seconds": 1},
                meta={TOOL_USE_ID_META_KEY: call_id, "message_id": "msg_1"},
            )
        )

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_call_check, "tu_chk_1", "sub_1", 0.0)
        f2 = pool.submit(_call_check, "tu_chk_2", "sub_2", 0.3)
        res1 = f1.result(timeout=4.0)
        res2 = f2.result(timeout=4.0)
    elapsed = time.monotonic() - t0

    assert res1.is_error is False and "done:sub_1" in res1.text
    assert res2.is_error is False and "done:sub_2" in res2.text
    assert elapsed < 1.8, f"Expected parallel overlap (< 1.8s), took {elapsed:.2f}s"


def test_read_and_agent_in_same_message_orders_read_first_and_prevents_read_cache_poisoning(
    tmp_path: Path,
) -> None:
    """Regression test for Red Team #1:
    1) [Read('t.txt'), Agent('edit t.txt')] in the same message runs Read before Agent.
    2) If a concurrent read overlaps with a mutating subagent, _succeeded_reads is not poisoned.
    """
    from tests.unit.agentic.test_native_turn import FakeNativeProvider, FakeStore

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    target = workspace.root / "t.txt"
    target.write_text("OLD_CONTENT\n", encoding="utf-8")

    order: list[str] = []
    order_lock = threading.Lock()

    def _fake_run_subagent(*_a: object, **_kw: object) -> ToolOutcome:
        with order_lock:
            order.append("agent_start")
        target.write_text("NEW_SUBAGENT_CONTENT\n", encoding="utf-8")
        with order_lock:
            order.append("agent_wrote")
        return ToolOutcome("succeeded", "subagent ok", "edited t.txt", {})

    toolset = AgentToolset(
        workspace,
        code_mode_active=True,
        public_tool_names=True,
        run_subagent=_fake_run_subagent,
        check_subagent=lambda *_a, **_kw: ToolOutcome("succeeded", "ok", "ok", {}),
    )
    orig_read_file = toolset.read_file

    def _tracked_read_file(*args: object, **kwargs: object) -> dict[str, object]:
        with order_lock:
            order.append("read_start")
        res = orig_read_file(*args, **kwargs)
        with order_lock:
            order.append("read_end")
        return res

    toolset.read_file = _tracked_read_file  # type: ignore[method-assign]

    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    executor = RuntimeToolExecutor(runtime, store.turn, gather_seconds=0.1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_read = pool.submit(
            executor.call_tool,
            ToolCallRequest(
                name="Read",
                arguments={"file_path": "t.txt"},
                meta={TOOL_USE_ID_META_KEY: "tu_r1", "message_id": "m1"},
            ),
        )
        time.sleep(0.01)
        f_agent = pool.submit(
            executor.call_tool,
            ToolCallRequest(
                name="Agent",
                arguments={"description": "edit t", "prompt": "edit t.txt", "subagent_type": "general-purpose"},
                meta={TOOL_USE_ID_META_KEY: "tu_a1", "message_id": "m1"},
            ),
        )
        res_r1 = f_read.result(timeout=3.0)
        res_a1 = f_agent.result(timeout=3.0)

    assert not res_r1.is_error
    assert not res_a1.is_error
    assert order == ["read_start", "read_end", "agent_start", "agent_wrote"]

    # Round 2 Read must see NEW_SUBAGENT_CONTENT, never a stale cached OLD_CONTENT
    res_r2 = executor.call_tool(
        ToolCallRequest(
            name="Read",
            arguments={"file_path": "t.txt"},
            meta={TOOL_USE_ID_META_KEY: "tu_r2", "message_id": "m2"},
        )
    )
    assert "NEW_SUBAGENT_CONTENT" in res_r2.text
    assert "OLD_CONTENT" not in res_r2.text


def test_concurrent_read_overlapping_with_subagent_write_does_not_poison_succeeded_reads(
    tmp_path: Path,
) -> None:
    """Direct test of runtime._run_toolset when a slow Read finishes after a concurrent Agent write."""
    from tests.unit.agentic.test_native_turn import FakeNativeProvider, FakeStore

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    target = workspace.root / "race.txt"
    target.write_text("INITIAL_V1\n", encoding="utf-8")

    read_captured_old = threading.Event()
    agent_finished = threading.Event()

    def _subagent_writer(*_a: object, **_kw: object) -> ToolOutcome:
        assert read_captured_old.wait(timeout=3.0)
        target.write_text("UPDATED_BY_AGENT_V2\n", encoding="utf-8")
        return ToolOutcome("succeeded", "ok", "updated", {})

    toolset = AgentToolset(
        workspace,
        code_mode_active=True,
        public_tool_names=True,
        run_subagent=_subagent_writer,
    )
    orig_read = toolset.read_file

    def _slow_first_read(*args: object, **kwargs: object) -> dict[str, object]:
        res = orig_read(*args, **kwargs)
        if not read_captured_old.is_set():
            read_captured_old.set()
            assert agent_finished.wait(timeout=3.0)
        return res

    toolset.read_file = _slow_first_read  # type: ignore[method-assign]
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )

    def _run_read() -> list[dict[str, object]]:
        return runtime._run_toolset(
            store.turn,
            [{"id": "c_r1", "name": "read_file", "arguments": '{"path": "race.txt"}'}],
        )

    def _run_agent() -> list[dict[str, object]]:
        try:
            return runtime._run_toolset(
                store.turn,
                [
                    {
                        "id": "c_a1",
                        "name": "run_subagent",
                        "arguments": '{"subagent_type": "general", "task": "write"}',
                    }
                ],
            )
        finally:
            agent_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        fr = pool.submit(_run_read)
        fa = pool.submit(_run_agent)
        fr.result(timeout=3.0)
        fa.result(timeout=3.0)

    # Next read must NOT hit _succeeded_reads with stale INITIAL_V1
    second_read = runtime._run_toolset(
        store.turn,
        [{"id": "c_r2", "name": "read_file", "arguments": '{"path": "race.txt"}'}],
    )
    assert "UPDATED_BY_AGENT_V2" in str(second_read[0]["content"])
    assert "INITIAL_V1" not in str(second_read[0]["content"])


def test_action_limit_reserves_in_flight_actions_and_codex_parallel_wave_counts_one_tool_round(
    tmp_path: Path,
) -> None:
    """Regression test for Red Team #7:
    1) max_actions=1 reserves in-flight actions so 4 parallel Agent calls only run 1 subagent.
    2) When _adapter_message_ids is False (Codex), 4 parallel Agent calls in one wave count as 1 tool_round.
    """
    from agentos.agentic.native_turn import NativeTurnRunner, _RunnerSink
    from tests.unit.agentic.test_native_turn import FakeNativeProvider, FakeStore

    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    ran_count = 0
    ran_lock = threading.Lock()

    def _slow_subagent(*_a: object, **_kw: object) -> ToolOutcome:
        nonlocal ran_count
        with ran_lock:
            ran_count += 1
        time.sleep(0.08)
        return ToolOutcome("succeeded", "ok", "done", {})

    toolset = AgentToolset(
        workspace,
        code_mode_active=True,
        public_tool_names=True,
        run_subagent=_slow_subagent,
    )
    runtime_limit_1 = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=1, context_window_tokens=1_000_000),
    )
    ex_limit_1 = RuntimeToolExecutor(runtime_limit_1, store.turn, gather_seconds=0.01)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [
            pool.submit(
                ex_limit_1.call_tool,
                ToolCallRequest(
                    name="Agent",
                    arguments={"description": f"sub {i}", "prompt": f"task {i}", "subagent_type": "explore"},
                    meta={TOOL_USE_ID_META_KEY: f"tu_{i}"},
                ),
            )
            for i in range(4)
        ]
        results = [f.result(timeout=3.0) for f in futs]

    assert ran_count == 1
    assert ex_limit_1.action_count == 1
    assert sum(1 for r in results if not r.is_error) == 1
    assert sum(1 for r in results if r.is_error) == 3

    # Now verify Codex parallel wave (no adapter message IDs) counts 1 tool_round and 1 iteration increment
    barrier = threading.Barrier(4, timeout=3.0)

    def _wave_subagent(*_a: object, **_kw: object) -> ToolOutcome:
        barrier.wait(timeout=3.0)
        return ToolOutcome("succeeded", "ok", "done", {})

    toolset_wave = AgentToolset(
        workspace,
        code_mode_active=True,
        public_tool_names=True,
        run_subagent=_wave_subagent,
    )
    runtime_wave = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset_wave,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    runner = NativeTurnRunner(
        runtime_wave,
        FakeNativeProvider(),
        store.turn,
        turn_id="t1",
        messages=[],
        deadline=time.monotonic() + 60.0,
    )
    ex_wave = RuntimeToolExecutor(runtime_wave, store.turn, gather_seconds=0.01)
    sink = _RunnerSink(runner, ex_wave)
    assert ex_wave._adapter_message_ids is False
    runner.iteration = 1

    with ThreadPoolExecutor(max_workers=4) as pool:
        wave_futs = [
            pool.submit(
                ex_wave.call_tool,
                ToolCallRequest(
                    name="Agent",
                    arguments={"description": f"w {i}", "prompt": f"w {i}", "subagent_type": "explore"},
                    meta={TOOL_USE_ID_META_KEY: f"w_{i}"},
                ),
            )
            for i in range(4)
        ]
        wave_res = [f.result(timeout=3.0) for f in wave_futs]

    assert all(not r.is_error for r in wave_res)
    assert ex_wave.action_count == 4
    assert ex_wave.tool_rounds == 1
    assert runtime_wave._silent_tool_rounds == 1
    assert runner.iteration == 2
    assert sink is not None


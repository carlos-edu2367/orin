"""A tool call the CLI gave up on must not hold the rest of the turn's tools behind it.

Incident: a run_command that never returned held the collector's exec lock; every later
read_file sat in "Preparando" until the CLI's 300 s MCP timeout, one after another.
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.native_turn import ABANDONED_BEFORE_START, ToolBatchCollector, _BatchEntry
from agentos.agentic.tools._shared import AgentToolError
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.cli_tools_mcp import ToolCallResult


def _slow_collector(release: threading.Event, started: threading.Event, ran: list[str]):
    def _run_batch(entries: list[_BatchEntry], msg_key: str) -> list[ToolCallResult]:
        ran.extend(e.call_id for e in entries)
        if any(e.call_id == "slow" for e in entries):
            started.set()
            release.wait(10)
        return [ToolCallResult(text=f"ok:{e.call_id}") for e in entries]

    return ToolBatchCollector(_run_batch, gather_seconds=0.01)


def test_queued_call_the_client_abandoned_returns_without_running() -> None:
    release, started, ran = threading.Event(), threading.Event(), []
    collector = _slow_collector(release, started, ran)
    slow = threading.Thread(target=collector.submit, args=("slow", "run_command", {}), kwargs={"message_id": "m1"})
    slow.start()
    assert started.wait(2)

    cancelled = threading.Event()
    out: list[ToolCallResult] = []
    queued = threading.Thread(
        target=lambda: out.append(
            collector.submit("late", "read_file", {}, message_id="m2", read_only=True, cancelled=cancelled)
        )
    )
    queued.start()
    time.sleep(0.3)
    assert not out  # still queued behind the slow call
    t0 = time.monotonic()
    cancelled.set()
    queued.join(2)
    assert out and out[0].is_error and out[0].text == ABANDONED_BEFORE_START
    assert time.monotonic() - t0 < 1.5
    release.set()
    slow.join(2)
    assert "late" not in ran  # never run late, after the model stopped waiting for it


def test_running_call_reports_when_its_client_gave_up() -> None:
    release, started, ran = threading.Event(), threading.Event(), []
    collector = _slow_collector(release, started, ran)
    cancelled = threading.Event()
    th = threading.Thread(
        target=collector.submit, args=("slow", "run_command", {}), kwargs={"message_id": "m1", "cancelled": cancelled}
    )
    th.start()
    assert started.wait(2)
    assert collector.running_call_abandoned() is False
    cancelled.set()
    assert collector.running_call_abandoned() is True
    release.set()
    th.join(2)
    assert collector.running_call_abandoned() is False


def test_call_without_cancel_signal_still_runs_after_a_slow_one() -> None:
    release, started, ran = threading.Event(), threading.Event(), []
    collector = _slow_collector(release, started, ran)
    slow = threading.Thread(target=collector.submit, args=("slow", "run_command", {}), kwargs={"message_id": "m1"})
    slow.start()
    assert started.wait(2)
    out: list[ToolCallResult] = []
    nxt = threading.Thread(target=lambda: out.append(collector.submit("next", "edit_file", {}, message_id="m1")))
    nxt.start()
    time.sleep(0.3)
    release.set()
    slow.join(2)
    nxt.join(2)
    assert out and out[0].text == "ok:next"


def _toolset(tmp_path: Path) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path / "w", "c1"), public_tool_names=True)


needs_sleep = pytest.mark.skipif(shutil.which("sleep") is None or shutil.which("head") is None, reason="needs sleep/head")


@needs_sleep
def test_abandoned_run_command_stops_itself(tmp_path: Path) -> None:
    ts = _toolset(tmp_path)
    abandoned = threading.Event()
    ts.set_call_abandoned_check(abandoned.is_set)
    threading.Timer(0.5, abandoned.set).start()
    t0 = time.monotonic()
    with pytest.raises(AgentToolError) as err:
        ts.run_command("sleep 30", timeout_seconds=60)
    assert time.monotonic() - t0 < 8
    assert getattr(err.value, "error_code", None) == "TOOL_CALL_ABANDONED"


@needs_sleep
def test_timeout_returns_when_a_grandchild_holds_the_pipe(tmp_path: Path) -> None:
    ts = _toolset(tmp_path)
    t0 = time.monotonic()
    with pytest.raises(AgentToolError, match="timed out"):
        ts.run_command("sleep 60 | head; echo after", timeout_seconds=2)
    assert time.monotonic() - t0 < 10


@needs_sleep
def test_normal_exit_with_a_leftover_grandchild_returns_quickly(tmp_path: Path) -> None:
    ts = _toolset(tmp_path)
    t0 = time.monotonic()
    result = ts.run_command("(sleep 20 &) ; echo quick", timeout_seconds=30)
    assert time.monotonic() - t0 < 8
    assert "quick" in str(result.get("stdout") if isinstance(result, dict) else result)

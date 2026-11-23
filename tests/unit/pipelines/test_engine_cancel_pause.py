"""Cancel (stop everything) and pause (drain) of a running pipeline (WS-C6)."""

from __future__ import annotations

import threading

import pytest

from agentos.pipelines.fakes import Reply
from agentos.pipelines.ports import CommandResult

from .engine_support import make_harness
from .helpers import pipeline

HEADER = """\
version: 1
id: teste
name: Teste
budget: {max_tokens: 1000000}
inputs:
  testes: {type: string, default: "pytest -q"}
"""

MAP = """
- id: itens
  agent: general
  map: {over: [a, b, c, d, e, f], as: x, max_parallel: 3}
  prompt: faz ${{ x }}
"""


def _in_thread(engine):
    box: dict = {}
    thread = threading.Thread(target=lambda: box.setdefault("outcome", engine.execute()), name="engine-under-test")
    thread.start()
    return thread, box


def _started(count: int):
    return lambda events: sum(1 for e in events if e.type.value == "pipeline.step_started") >= count


def test_cancel_stops_every_child_and_closes_the_journal(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(MAP, HEADER))
    h.launcher.script("itens", lambda call, control: control.hang())
    engine = h.engine()
    thread, box = _in_thread(engine)
    assert h.sink.wait_for(_started(3))
    engine.cancel()
    thread.join(timeout=20)
    assert not thread.is_alive()
    outcome = box["outcome"]
    assert outcome.state == "cancelled" and outcome.stop_reason == "cancelled"
    assert h.launcher.launch_count_by_step["itens"] == 3  # no new child after the cancel
    assert len({agent for agent, _ in h.launcher.cancelled}) == 3
    assert h.launcher.all_finished()  # no orphan child
    assert {row.state for row in h.rows()} == {"interrupted"}
    assert h.state() == "cancelled"
    assert h.sink.types()[-1] == "pipeline.finished"


@pytest.mark.parametrize("on_fail", ["", "on_fail: continue"])
def test_cancel_wins_over_a_failed_item_of_the_same_map(tmp_path, on_fail) -> None:
    """F4: one item failed, the user cancelled while another was still running: the run is `cancelled`, not `succeeded`."""
    text = pipeline(
        f"""
        - id: itens
          agent: general
          map: {{over: [a, b], as: x, max_parallel: 2, fail_fast: false}}
          prompt: faz ${{{{ x }}}}
          {on_fail}
        """,
        HEADER,
    )  # the map is the last step: nothing after it would notice the cancel by itself
    h = make_harness(tmp_path, text)
    h.launcher.script("itens", Reply(status="failed", text="", error_code="BOOM"), index=0)
    h.launcher.script("itens", lambda call, control: control.hang(), index=1)
    engine = h.engine()
    thread, box = _in_thread(engine)
    assert h.sink.wait_for(
        lambda events: any(
            e.type.value == "pipeline.step_finished" and e.payload["state"] == "failed" for e in events
        )
    )
    engine.cancel()
    thread.join(timeout=20)
    assert not thread.is_alive()
    outcome = box["outcome"]
    assert (outcome.state, outcome.stop_reason) == ("cancelled", "cancelled")
    assert h.state() == "cancelled"


def test_cancel_wins_over_a_gate_that_fails_with_on_fail_continue(tmp_path) -> None:
    text = pipeline(
        """
        - id: g
          gate:
            all:
              - command: ${{ inputs.testes }}
          on_fail: continue
        """,
        HEADER,
    )
    h = make_harness(tmp_path, text)
    engine = h.engine()

    def cancels_then_fails(command, timeout, cancel):
        engine.cancel()
        return CommandResult(1, "FAILED depois do cancelamento")

    h.gates.script(cancels_then_fails)
    outcome = engine.execute()
    assert outcome.state == "cancelled"


def test_cancel_before_start_launches_nothing(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(MAP, HEADER))
    engine = h.engine()
    engine.cancel()
    outcome = engine.execute()
    assert outcome.state == "cancelled"
    assert h.launcher.launches == 0


def test_cancel_during_a_gate_command(tmp_path) -> None:
    text = pipeline(
        """
        - id: g
          gate:
            all:
              - command: ${{ inputs.testes }}
        - id: depois
          agent: general
          prompt: nunca
        """,
        HEADER,
    )
    h = make_harness(tmp_path, text)
    engine = h.engine()

    def long_command(command, timeout, cancel):
        engine.cancel()  # the user pressed cancel while the command ran
        assert cancel.is_set()  # the runner is told through the token
        return CommandResult(None, "", cancelled=True)

    h.gates.script(long_command)
    outcome = engine.execute()
    assert outcome.state == "cancelled"
    assert h.launcher.launches == 0
    assert h.rows()[0].state == "interrupted"


def test_pause_drains_and_resume_continues_without_redoing_work(tmp_path) -> None:
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: um
        - id: b
          agent: general
          prompt: dois
        """,
        HEADER,
    )
    h = make_harness(tmp_path, text)
    release = threading.Event()

    def slow(call, control):
        assert release.wait(10)
        return Reply(text="a terminou")

    h.launcher.script("a", slow)
    engine = h.engine()
    thread, box = _in_thread(engine)
    assert h.sink.wait_for(_started(1))
    engine.pause()
    release.set()
    thread.join(timeout=20)
    outcome = box["outcome"]
    assert outcome.state == "interrupted" and outcome.stop_reason == "paused"
    assert h.launcher.cancelled == []  # draining never cancels
    assert [(row.step_id, row.state) for row in h.rows()] == [("a", "succeeded")]
    assert h.launcher.launch_count_by_step["b"] == 0
    stored = h.store.get_run("run-1")
    assert stored.state == "interrupted" and stored.stop_reason == "paused"
    resumed = h.resume()
    assert resumed.state == "succeeded"
    assert h.launcher.launch_count_by_step["a"] == 1
    assert h.launcher.launch_count_by_step["b"] == 1


def test_pause_inside_a_map_does_not_start_the_remaining_items(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(MAP, HEADER))
    gate = threading.Barrier(4, timeout=10)  # three children and the test thread

    def parked(call, control):
        gate.wait()
        return Reply(text="ok")

    h.launcher.script("itens", parked)
    engine = h.engine()
    thread, box = _in_thread(engine)
    assert h.sink.wait_for(_started(3))
    engine.pause()
    gate.wait()  # now the three in-flight children may finish
    thread.join(timeout=20)
    outcome = box["outcome"]
    assert outcome.state == "interrupted" and outcome.stop_reason == "paused"
    assert h.launcher.launch_count_by_step["itens"] == 3
    assert {row.state for row in h.rows()} == {"succeeded"}
    h.launcher.script("itens", Reply(text="resto"))
    assert h.resume().state == "succeeded"
    assert h.launcher.launch_count_by_step["itens"] == 6  # only the three that were left
    assert len(h.sink.of("pipeline.step_reused")) == 3

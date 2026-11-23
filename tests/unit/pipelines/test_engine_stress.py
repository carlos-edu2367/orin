"""Stress: eight children in parallel, thirty repetitions, random cancels from a fixed seed (WS-C6).

No ``sleep`` anywhere: runs end by events and conditions, and every wait has a 10 s ceiling so a deadlock fails the
test instead of hanging the suite.
"""

from __future__ import annotations

import random
import threading

import pytest

from agentos.pipelines.fakes import Reply

from .engine_support import make_harness
from .helpers import pipeline

HEADER = """\
version: 1
id: teste
name: Teste
budget: {max_tokens: 10000000}
inputs:
  objetivo: {type: string, default: "x"}
"""

MAP8 = """
- id: itens
  agent: general
  map: {over: [a, b, c, d, e, f, g, h], as: x, max_parallel: 8, fail_fast: false}
  prompt: faz ${{ x }}
- id: depois
  agent: general
  prompt: junta ${{ steps.itens.output }}
"""

REPETITIONS = 30
SEED = 20261123
JOIN_SECONDS = 10


def _started(count: int):
    return lambda events: sum(1 for e in events if e.type.value == "pipeline.step_started") >= count


@pytest.mark.parametrize("repetition", range(REPETITIONS))
def test_eight_children_with_random_cancel_never_deadlock_or_leak(tmp_path, repetition: int) -> None:
    rng = random.Random(SEED + repetition)
    cancel_after = rng.choice([None, 1, 2, 3, 5, 8])
    h = make_harness(tmp_path, pipeline(MAP8, HEADER))
    if cancel_after is None:
        h.launcher.script("itens", Reply(text="ok"))
    else:
        # F10: a mix, so the cancel races with children that finish, fail and hang: ``finish_step`` and ``add_usage``
        # run in the pool threads at the very moment ``cancel`` closes the others. Items 2 and 5 only end by the cancel.
        for index in range(8):
            behavior = (
                Reply(text="ok") if index % 3 == 0 else Reply(status="failed", text="", error_code="BOOM")
                if index % 3 == 1 else (lambda call, control: control.hang())
            )
            h.launcher.script("itens", behavior, index=index)
    engine = h.engine()
    box: dict = {}
    thread = threading.Thread(target=lambda: box.setdefault("outcome", engine.execute()), name="stress-engine")
    thread.start()
    if cancel_after is not None:
        assert h.sink.wait_for(_started(cancel_after), JOIN_SECONDS), "children never started"
        engine.cancel()
    thread.join(timeout=JOIN_SECONDS)
    assert not thread.is_alive(), "the engine deadlocked"
    outcome = box["outcome"]
    assert h.launcher.running == 0
    assert h.launcher.all_finished(), h.launcher.unfinished()  # no forgotten agent
    launched = {agent for agent in h.launcher._entries}
    if cancel_after is None:
        assert outcome.state == "succeeded"
        assert h.launcher.launch_count_by_step["itens"] == 8
        assert h.launcher.max_running <= 8
    else:
        assert outcome.state == "cancelled"
        assert h.launcher.launch_count_by_step["depois"] == 0
        assert {agent for agent, _ in h.launcher.cancelled} <= launched
        still_open = [row for row in h.rows() if row.state in {"running", "pending"}]
        assert still_open == []  # the journal has no open step after a cancel
        assert all(row.state in {"succeeded", "failed", "interrupted"} for row in h.rows())
    assert h.state() == outcome.state
    run = h.store.get_run(h.run_id)
    assert run.used_tokens == sum(row.input_tokens + row.output_tokens for row in h.rows())  # no usage lost in the race

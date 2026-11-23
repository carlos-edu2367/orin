"""Exact event sequences of the engine and the contract every event must keep (WS-C6)."""

from __future__ import annotations

from datetime import UTC, datetime

from agentos.agentic.events import AgentActivityEvent
from agentos.pipelines.fakes import RecordingSink, Reply, SimulatedCrash
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

AGENT_THEN_GATE = """
- id: a
  agent: general
  prompt: faz
  retries: {max: 1, on: [failed]}
- id: g
  gate:
    all:
      - command: ${{ inputs.testes }}
"""

LOOP = """
- id: fazer
  agent: general
  prompt: faz
- id: verificar
  gate:
    all:
      - command: ${{ inputs.testes }}
  on_fail: {goto: corrigir, max_loops: 2}
- id: corrigir
  agent: general
  when: ${{ steps.verificar.failed }}
  prompt: corrige
  next: verificar
"""


def _contract_ok(h) -> None:
    for number, event in enumerate(h.sink.events, start=1):
        assert event.payload.get("run_id") == h.run_id, event.type  # no event without its run
        AgentActivityEvent(
            event_id=f"activity:{number}",
            conversation_id="conversation:1",
            turn_id="turn:1",
            execution_id="execution:1",
            user_id="user:1",
            agent_id="agent:1",
            event_type=event.type,
            sequence=number,
            summary=event.summary,
            payload=event.payload,
            created_at=datetime.now(UTC),
        )


def test_events_of_a_complete_run(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(AGENT_THEN_GATE, HEADER))
    assert h.run().state == "succeeded"
    assert h.sink.types() == [
        "pipeline.started",
        "pipeline.step_started",
        "pipeline.step_finished",
        "pipeline.step_started",
        "pipeline.gate_evaluated",
        "pipeline.step_finished",
        "pipeline.finished",
    ]
    _contract_ok(h)
    finished = h.sink.of("pipeline.finished")[0].payload
    assert finished["state"] == "succeeded" and finished["steps_succeeded"] == 2


def test_events_of_a_failed_step_with_retry(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(AGENT_THEN_GATE, HEADER))
    h.launcher.script("a", Reply(status="failed", error_code="BOOM", text=""), Reply())
    assert h.run().state == "succeeded"
    assert h.sink.types()[:7] == [
        "pipeline.started",
        "pipeline.step_started",
        "pipeline.step_finished",
        "pipeline.step_retrying",
        "pipeline.step_started",
        "pipeline.step_finished",
        "pipeline.step_started",
    ]
    states = [e.payload["state"] for e in h.sink.of("pipeline.step_finished")]
    assert states[:2] == ["failed", "succeeded"]
    _contract_ok(h)


def test_events_of_a_run_that_fails(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(AGENT_THEN_GATE, HEADER))
    h.gates.script(CommandResult(1, "quebrou"))
    assert h.run().state == "failed"
    assert h.sink.types()[-3:] == ["pipeline.gate_evaluated", "pipeline.step_finished", "pipeline.finished"]
    gate = h.sink.of("pipeline.gate_evaluated")[0].payload
    assert gate["passed"] is False and gate["exit_code"] == 1
    assert h.sink.of("pipeline.finished")[0].payload["stop_reason"] == "step_failed"
    _contract_ok(h)


def test_events_of_a_loop(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(LOOP, HEADER))
    h.gates.script(CommandResult(1, "erro"), CommandResult(0, "ok"))
    assert h.run().state == "succeeded"
    assert h.sink.types() == [
        "pipeline.started",
        "pipeline.step_started", "pipeline.step_finished",           # fazer
        "pipeline.step_started", "pipeline.gate_evaluated", "pipeline.step_finished",  # verificar: fails
        "pipeline.loop",
        "pipeline.step_started", "pipeline.step_finished",           # corrigir
        "pipeline.step_started", "pipeline.gate_evaluated", "pipeline.step_finished",  # verificar: passes
        "pipeline.step_finished",                                    # corrigir skipped
        "pipeline.finished",
    ]
    loop = h.sink.of("pipeline.loop")[0].payload
    assert loop["loop_count"] == 1 and loop["max_loops"] == 2
    _contract_ok(h)


def test_events_of_a_resume(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(AGENT_THEN_GATE, HEADER))

    def crash(command, timeout, cancel):
        raise SimulatedCrash()

    h.gates.script(crash)
    try:
        h.run()
    except SimulatedCrash:
        pass
    second = RecordingSink()
    h.store.interrupt_orphans(h.run_id)
    outcome = h.engine(resume=True, sink=second).execute()
    assert outcome.state == "succeeded"
    assert second.types() == [
        "pipeline.resumed",
        "pipeline.step_reused",
        "pipeline.step_started",
        "pipeline.gate_evaluated",
        "pipeline.step_finished",
        "pipeline.finished",
    ]
    assert second.of("pipeline.step_reused")[0].payload["step_id"] == "a"


def test_events_of_a_budget_overrun(tmp_path) -> None:
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: um
        - id: b
          agent: general
          prompt: dois
        """,
        HEADER.replace("max_tokens: 1000000", "max_tokens: 10"),
    )
    h = make_harness(tmp_path, text)
    h.launcher.script("a", Reply(text="gastou", input_tokens=50, output_tokens=0))
    assert h.run().state == "budget_exceeded"
    types = h.sink.types()
    assert types.count("pipeline.budget_exceeded") == 1
    assert types[-1] == "pipeline.finished"
    assert types.index("pipeline.budget_exceeded") < types.index("pipeline.finished")
    assert h.sink.of("pipeline.finished")[0].payload["state"] == "budget_exceeded"
    _contract_ok(h)

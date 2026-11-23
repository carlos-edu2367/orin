"""Stall detection, replan note and "no progress" loop fingerprints (WS-C6, decision G9)."""

from __future__ import annotations

from datetime import timedelta

from agentos.pipelines.fakes import FakeLauncher, Reply
from agentos.pipelines.ports import AgentProgress, CommandResult, VerifierResult
from agentos.pipelines.stall import command_fingerprint

from .engine_support import make_harness
from .helpers import pipeline

HEADER = """\
version: 1
id: teste
name: Teste
budget: {max_tokens: 1000000}
inputs:
  testes: {type: string, default: "pytest -q"}
defaults: {stall_minutes: 1}
"""


def _single(retries: str = "") -> str:
    return pipeline(
        f"""
        - id: a
          agent: general
          prompt: faz
          {retries}
        """,
        HEADER,
    )


def _hang_after_work(call, control):
    control.report(current_tool="Bash", tool_calls=7)
    return control.hang()


def test_a_child_without_activity_is_cancelled_and_the_step_fails(tmp_path) -> None:
    h = make_harness(tmp_path, _single(), advance_on_wait=True)
    h.launcher.script("a", _hang_after_work)
    outcome = h.run(poll_seconds=30.0)
    assert outcome.state == "failed" and outcome.error_code == "STEP_STALLED"
    assert h.launcher.launch_count_by_step["a"] == 1  # no `on: [stalled]`, no second try
    assert h.launcher.cancelled and h.launcher.cancelled[0][1] == "stalled"
    assert h.rows()[0].state == "stalled"
    assert len(h.sink.of("pipeline.stalled")) == 1
    assert h.launcher.all_finished()


def test_stalled_retry_carries_a_replan_note(tmp_path) -> None:
    h = make_harness(tmp_path, _single("retries: {max: 1, on: [stalled]}"), advance_on_wait=True)
    h.launcher.script("a", _hang_after_work, Reply(text="agora foi"))
    outcome = h.run(poll_seconds=30.0)
    assert outcome.state == "succeeded"
    first, second = h.launcher.calls
    assert first.replan_note is None
    assert "`Bash`" in second.replan_note and "7 calls" in second.replan_note
    assert "change your approach" in second.replan_note
    assert second.prompt == first.prompt  # the note travels beside the prompt; the adapter appends it to the task
    assert [row.state for row in h.rows()] == ["stalled", "succeeded"]


def test_activity_keeps_the_child_alive(tmp_path) -> None:
    h = make_harness(tmp_path, _single(), advance_on_wait=True)

    def busy(call, control):
        for _ in range(8):
            h.clock.advance(20)  # 160 virtual seconds in total, but the child keeps reporting activity
            control.report(last_activity_at=h.clock.now(), tool_calls=1)
        return Reply(text="ok")

    h.launcher.script("a", busy)
    assert h.run(poll_seconds=30.0).state == "succeeded"


class _LateLauncher(FakeLauncher):
    """First ``wait`` times out, the second (the engine's last look before cancelling) finds the result."""

    def __init__(self, clock) -> None:
        super().__init__(clock)
        self._calls = 0

    def wait(self, handle, timeout):
        self._calls += 1
        if self._calls == 1:
            self._entries[handle.agent_id].done.wait(1.0)  # let the scripted child finish
            return None
        return super().wait(handle, timeout)

    def progress(self, handle):
        return AgentProgress(last_activity_at=self._clock.now() - timedelta(hours=3))


def test_stale_progress_does_not_kill_a_step_that_just_finished(tmp_path) -> None:
    h = make_harness(tmp_path, _single())
    h.launcher = _LateLauncher(h.clock)
    h.launcher.script("a", Reply(text="terminou"))
    outcome = h.run()
    assert outcome.state == "succeeded"
    assert h.launcher.cancelled == []
    assert h.sink.of("pipeline.stalled") == []


LOOP = """
- id: fazer
  agent: general
  prompt: faz
- id: verificar
  gate:
    all:
      - command: ${{ inputs.testes }}
  on_fail: {goto: corrigir, max_loops: 4}
- id: corrigir
  agent: general
  when: ${{ steps.verificar.failed }}
  prompt: corrige
  next: verificar
"""


def test_same_gate_failure_twice_stops_the_loop_with_no_progress(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(LOOP, HEADER))
    # only counters and timings change between the two failures: same fingerprint
    h.gates.script(CommandResult(1, "FAILED test_a - 3 passed in 0.31s"), CommandResult(1, "FAILED test_a - 5 passed in 0.45s"))
    outcome = h.run()
    assert outcome.state == "failed" and outcome.stop_reason == "no_progress"
    assert outcome.loops == 1  # one lap was taken, the second failure ended it with max_loops to spare
    assert h.launcher.launch_count_by_step["corrigir"] == 1
    stalled = h.sink.of("pipeline.stalled")
    assert len(stalled) == 1 and stalled[0].payload["action"] == "no_progress"
    assert h.store.get_run("run-1").stop_reason == "no_progress"


def test_a_different_failure_is_progress(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(LOOP, HEADER))
    h.gates.script(CommandResult(1, "FAILED test_a"), CommandResult(1, "FAILED test_b"), CommandResult(0, "ok"))
    assert h.run().state == "succeeded"


def test_same_verifier_findings_twice_is_no_progress(tmp_path) -> None:
    text = pipeline(
        LOOP.replace("      - command: ${{ inputs.testes }}", "      - verifier: {}"),
        HEADER,
    )
    h = make_harness(tmp_path, text)
    rows = ({"id": "F1", "severity": "blocker", "claim": "vaza memoria"},)
    h.verifier._queue.extend([VerifierResult("fail", findings=rows), VerifierResult("fail", findings=rows)])
    outcome = h.run()
    assert outcome.stop_reason == "no_progress"


def test_fingerprint_folds_digits_and_spacing() -> None:
    assert command_fingerprint(1, "a  1 b\n") == command_fingerprint(1, "a 22   b")
    assert command_fingerprint(1, "a b") != command_fingerprint(2, "a b")


# ------------------------------------------------------------------ review fixes: agent loops (F8)

AGENT_LOOP = """
- id: a
  agent: general
  prompt: faz
  output_schema: {type: object, properties: {n: {type: integer}}}
  on_fail: {goto: a, max_loops: 3}
"""
GOOD = Reply(text="x", structured_output={"n": 1})


def _failing(text: str) -> Reply:
    return Reply(status="failed", text=text, error_code="AGENT_FAILED")


def _bad_schema(value: object) -> Reply:
    return Reply(text="x", structured_output={"n": value})


def test_agent_failures_with_different_texts_are_progress_and_use_the_loops(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(AGENT_LOOP, HEADER))
    h.launcher.script("a", _failing("erro um completamente diferente"), _failing("outro erro sem relacao nenhuma"), GOOD)
    outcome = h.run()
    assert outcome.state == "succeeded", outcome
    assert outcome.loops == 2 and h.launcher.launch_count_by_step["a"] == 3


def test_the_same_agent_failure_twice_is_still_no_progress(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(AGENT_LOOP, HEADER))
    h.launcher.script("a", _failing("falhou no teste 3 em 0.31s"), _failing("falhou no teste 5 em 0.45s"))
    outcome = h.run()
    assert (outcome.state, outcome.stop_reason) == ("failed", "no_progress")
    assert h.launcher.launch_count_by_step["a"] == 2


def test_agent_failures_with_different_error_messages_are_progress(tmp_path) -> None:
    from dataclasses import replace

    messages = iter(["a ferramenta travou", "a rede caiu"])

    class _Launcher(FakeLauncher):
        def wait(self, handle, timeout):
            result = super().wait(handle, timeout)
            if result is not None and result.status == "failed":
                return replace(result, error_message=next(messages))
            return result

    h = make_harness(tmp_path, pipeline(AGENT_LOOP, HEADER))
    h.launcher = _Launcher(h.clock)
    h.launcher.script("a", _failing(""), _failing(""), GOOD)
    assert h.run().state == "succeeded"


def test_schema_failures_with_different_errors_are_progress_and_equal_ones_are_not(tmp_path) -> None:
    different = make_harness(tmp_path / "different", pipeline(AGENT_LOOP, HEADER))
    different.launcher.script("a", _bad_schema("um"), _bad_schema([]), GOOD)
    assert different.run().state == "succeeded"
    equal = make_harness(tmp_path / "equal", pipeline(AGENT_LOOP, HEADER))
    equal.launcher.script("a", _bad_schema("um"), _bad_schema("um"))
    assert equal.run().stop_reason == "no_progress"


def test_map_failures_with_different_item_outputs_are_progress(tmp_path) -> None:
    text = pipeline(
        """
        - id: itens
          agent: general
          map: {over: [a, b], as: x, max_parallel: 2, fail_fast: false}
          prompt: faz ${{ x }}
          on_fail: {goto: itens, max_loops: 3}
        """,
        HEADER,
    )
    h = make_harness(tmp_path, text)
    h.launcher.script(
        "itens", _failing("primeira rodada quebrou assim"), _failing("segunda rodada quebrou de outro jeito"), Reply(text="ok"), index=0
    )
    h.launcher.script("itens", Reply(text="ok"), index=1)
    outcome = h.run()
    assert outcome.state == "succeeded", outcome
    assert outcome.loops == 2


def test_a_step_that_passed_forgets_its_earlier_failure(tmp_path) -> None:
    """Fail E, pass, then fail E again later is a new problem, not "the same failure twice in a row"."""
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: faz
          on_fail: {goto: a, max_loops: 3}
        - id: g
          gate:
            all:
              - command: ${{ inputs.testes }}
          on_fail: {goto: a, max_loops: 2}
        """,
        HEADER,
    )
    h = make_harness(tmp_path, text)
    h.launcher.script("a", _failing("E"), Reply(text="ok"), _failing("E"), Reply(text="ok"))
    h.gates.script(CommandResult(1, "FAILED x"), CommandResult(0, "ok"))
    outcome = h.run()
    assert outcome.state == "succeeded", outcome
    assert h.launcher.launch_count_by_step["a"] == 4


def test_loop_progress_forget_clears_one_step() -> None:
    from agentos.pipelines.stall import LoopProgress

    progress = LoopProgress()
    assert progress.repeats("a", "x") is False
    progress.forget("a")
    assert progress.repeats("a", "x") is False
    assert progress.repeats("a", "x") is True

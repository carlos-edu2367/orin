"""Resume from the journal: nothing finished is redone (AC3), hash checks, conflicts, gates, worktrees (WS-C6)."""

from __future__ import annotations

import pytest

from agentos.pipelines.fakes import Reply, SimulatedCrash
from agentos.pipelines.model import PipelineDefinition
from agentos.pipelines.ports import CommandResult
from agentos.pipelines.resume import DefinitionChanged

from .engine_support import PLAN_OUTPUT, definition_of, make_harness
from .helpers import pipeline

HEADER = """\
version: 1
id: teste
name: Teste
budget: {max_tokens: 1000000}
inputs:
  objetivo: {type: string, default: "x"}
  testes: {type: string, default: "pytest -q"}
"""


def _crash(command, timeout, cancel):
    raise SimulatedCrash()


def _spec_harness(tmp_path):
    h = make_harness(tmp_path)
    h.launcher.script("plano", Reply(text="plano", structured_output=PLAN_OUTPUT))
    return h


def test_crash_in_the_gate_resumes_only_the_gate(tmp_path) -> None:
    h = _spec_harness(tmp_path)
    h.gates.script(_crash)
    with pytest.raises(SimulatedCrash):
        h.run()
    assert h.state() == "running"  # the process "died": nothing closed the run
    before = dict(h.launcher.launch_count_by_step)
    assert before == {"plano": 1, "implementar": 2}
    outcome = h.resume()
    assert outcome.state == "succeeded", outcome
    assert dict(h.launcher.launch_count_by_step) == before  # plano and implementar were not launched again
    assert len(h.gates.commands) == 2  # the crashed attempt and the resumed one
    reused = h.sink.of("pipeline.step_reused")
    assert {(e.payload["step_id"], e.payload["item_key"][:3]) for e in reused} == {
        ("plano", ""), ("implementar", "000"), ("implementar", "001"),
    }
    assert h.sink.of("pipeline.resumed")[0].payload["reused_steps"] == 2
    verificar = [row for row in h.rows() if row.step_id == "verificar"]
    assert [row.state for row in verificar] == ["interrupted", "succeeded"]


def test_reused_work_is_not_counted_twice(tmp_path) -> None:
    h = _spec_harness(tmp_path)
    h.gates.script(_crash)
    with pytest.raises(SimulatedCrash):
        h.run()
    h.resume()
    run = h.store.get_run("run-1")
    assert run.used_tokens == sum(row.input_tokens + row.output_tokens for row in h.rows())


def test_crash_inside_a_child_resumes_that_child_only(tmp_path) -> None:
    h = make_harness(
        tmp_path,
        pipeline(
            """
            - id: a
              agent: general
              prompt: um
            - id: b
              agent: general
              prompt: dois
            """,
            HEADER,
        ),
    )

    def dies(call, control):
        raise SimulatedCrash()

    h.launcher.script("b", dies, Reply(text="depois"))
    with pytest.raises(SimulatedCrash):
        h.run()
    assert h.launcher.launch_count_by_step["a"] == 1
    outcome = h.resume()
    assert outcome.state == "succeeded"
    assert h.launcher.launch_count_by_step["a"] == 1
    assert h.launcher.launch_count_by_step["b"] == 2


CHAIN = """
- id: a
  agent: general
  prompt: parte ${{ inputs.objetivo }}
- id: b
  agent: general
  prompt: usa ${{ steps.a.output }}
- id: c
  agent: general
  prompt: independente
- id: d
  gate:
    all:
      - command: ${{ inputs.testes }}
"""


def _echo(call, control):
    return Reply(text=call.prompt)


def test_changed_input_redoes_the_step_and_the_ones_that_read_it(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(CHAIN, HEADER))
    h.launcher.script("a", _echo)
    h.launcher.script("b", _echo)
    h.gates.script(_crash)
    with pytest.raises(SimulatedCrash):
        h.run()
    h.inputs["objetivo"] = "outro"
    outcome = h.resume()
    assert outcome.state == "succeeded"
    assert h.launcher.launch_count_by_step["a"] == 2
    assert h.launcher.launch_count_by_step["b"] == 2  # its prompt embeds the new output of `a`
    assert h.launcher.launch_count_by_step["c"] == 1  # does not read `a`: reused
    reused = {e.payload["step_id"] for e in h.sink.of("pipeline.step_reused")}
    assert reused == {"c"}


def test_changed_definition_is_refused_with_both_hashes(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(CHAIN, HEADER))
    other = definition_of(pipeline(CHAIN, HEADER).replace("independente", "mudou"))
    assert isinstance(other, PipelineDefinition)
    h.definition = other
    with pytest.raises(DefinitionChanged) as caught:
        h.engine(resume=True).execute()
    assert caught.value.stored_hash != caught.value.current_hash


WORKTREE = """
- id: impl
  agent: general
  isolation: worktree
  prompt: edita ${{ inputs.objetivo }}
- id: depois
  agent: general
  prompt: fim
"""


def test_a_step_already_merged_is_never_redone_even_with_another_hash(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(WORKTREE, HEADER))
    h.launcher.script("impl", Reply(text="feito", worktree={"merge_state": "applied", "merge_detail": "2 arquivos"}))

    def dies(call, control):
        raise SimulatedCrash()

    h.launcher.script("depois", dies)
    with pytest.raises(SimulatedCrash):
        h.run()
    h.inputs["objetivo"] = "outra coisa"  # the hash of `impl` no longer matches
    outcome = h.resume()
    assert outcome.state == "failed" and outcome.stop_reason == "resume_conflict"
    assert outcome.error_code == "RESUME_CONFLICT"
    assert h.launcher.launch_count_by_step["impl"] == 1


def test_a_merged_step_with_the_same_hash_is_simply_reused(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(WORKTREE, HEADER))
    h.launcher.script("impl", Reply(text="feito", worktree={"merge_state": "applied"}))

    def dies(call, control):
        raise SimulatedCrash()

    h.launcher.script("depois", dies, Reply(text="ok"))
    with pytest.raises(SimulatedCrash):
        h.run()
    assert h.resume().state == "succeeded"
    assert h.launcher.launch_count_by_step["impl"] == 1


def test_interrupted_child_with_a_worktree_is_discarded_before_it_is_redone(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(WORKTREE, HEADER))

    def dies(call, control):
        raise SimulatedCrash()

    h.launcher.script("impl", dies, Reply(text="refeito"))
    with pytest.raises(SimulatedCrash):
        h.run()
    assert h.launcher.discarded == []
    assert h.resume().state == "succeeded"
    assert len(h.launcher.discarded) == 1  # K19: dropped once, before the second launch of `impl`
    assert h.launcher.launch_count_by_step["impl"] == 2


GATE_THEN_AGENT = """
- id: g
  gate:
    all:
      - command: ${{ inputs.testes }}
- id: depois
  agent: general
  prompt: fim
"""


def test_finished_gates_are_reused_unless_rerun_is_asked(tmp_path) -> None:
    def build(path):
        h = make_harness(path, pipeline(GATE_THEN_AGENT, HEADER))

        def dies(call, control):
            raise SimulatedCrash()

        h.launcher.script("depois", dies, Reply(text="ok"))
        with pytest.raises(SimulatedCrash):
            h.run()
        return h

    kept = build(tmp_path / "keep")
    assert kept.resume().state == "succeeded"
    assert len(kept.gates.commands) == 1  # the passed gate was not run again
    redone = build(tmp_path / "redo")
    assert redone.resume(rerun_gates=True).state == "succeeded"
    assert len(redone.gates.commands) == 2


# ------------------------------------------------------------------ review fixes (F5, F6)

FIX_LOOP = """
- id: verificar
  gate:
    all:
      - command: ${{ inputs.testes }}
  on_fail: {goto: corrigir, max_loops: 2}
- id: corrigir
  agent: general
  when: ${{ steps.verificar.failed }}
  prompt: corrige ${{ steps.verificar.findings }}
  next: verificar
"""


def test_resuming_a_run_that_failed_on_a_gate_runs_that_gate_again(tmp_path) -> None:
    """F5: the failure that ended the run is not a result to reuse: the user resumes it to try again."""
    h = make_harness(tmp_path, pipeline(GATE_THEN_AGENT, HEADER))
    h.gates.script(CommandResult(1, "quebrou"), CommandResult(0, "agora passa"))
    first = h.run()
    assert (first.state, first.failed_step) == ("failed", "g")
    resumed = h.resume()
    assert resumed.state == "succeeded", resumed
    assert len(h.gates.commands) == 2  # the gate really ran again
    assert [row.state for row in h.rows() if row.step_id == "g"] == ["gate_failed", "succeeded"]
    assert h.launcher.launch_count_by_step["depois"] == 1


def test_a_failed_gate_that_the_run_already_moved_past_is_still_reused(tmp_path) -> None:
    """The loop `verificar -> corrigir -> verificar` crashed in `corrigir`: the first failure is history, not redone."""
    h = make_harness(tmp_path, pipeline(FIX_LOOP, HEADER))

    def dies(call, control):
        raise SimulatedCrash()

    h.launcher.script("corrigir", dies, Reply(text="corrigido"))
    h.gates.script(CommandResult(1, "FAILED a"), CommandResult(0, "ok"))
    with pytest.raises(SimulatedCrash):
        h.run()
    assert len(h.gates.commands) == 1
    outcome = h.resume()
    assert outcome.state == "succeeded", outcome
    assert len(h.gates.commands) == 2  # loop 0 was reused; only loop 1 ran
    assert h.launcher.launch_count_by_step["corrigir"] == 2
    assert [e.payload["step_id"] for e in h.sink.of("pipeline.step_reused")] == ["verificar"]


def test_resuming_a_failed_gate_with_continue_does_not_run_it_again(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(
        """
        - id: g
          gate:
            all:
              - command: ${{ inputs.testes }}
          on_fail: continue
        - id: depois
          agent: general
          prompt: fim
        """,
        HEADER,
    ))

    def dies(call, control):
        raise SimulatedCrash()

    h.gates.script(CommandResult(1, "quebrou"), CommandResult(0, "nao deveria rodar"))
    h.launcher.script("depois", dies, Reply(text="ok"))
    with pytest.raises(SimulatedCrash):
        h.run()
    outcome = h.resume()
    assert outcome.state == "succeeded"
    assert len(h.gates.commands) == 1  # the run moved on past the failed gate (on_fail: continue): nothing to redo


BIG_THEN_USE = """
- id: a
  agent: general
  prompt: gera um relatorio grande
- id: b
  agent: general
  prompt: usa ${{ steps.a.output }}
"""


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_an_unreadable_artifact_makes_the_step_run_again_with_an_event(tmp_path, damage) -> None:
    """F6: a journaled output that cannot be read is not `null`: the step is redone and the user is told."""
    from agentos.pipelines.store import artifact_dir

    h = make_harness(tmp_path, pipeline(BIG_THEN_USE, HEADER))
    big = "linha longa de relatorio " * 2000  # about 50 KB: it goes to an artifact file
    h.launcher.script("a", Reply(text=big), Reply(text="relatorio refeito"))

    def dies(call, control):
        raise SimulatedCrash()

    h.launcher.script("b", dies, Reply(text="ok"))
    with pytest.raises(SimulatedCrash):
        h.run()
    files = list(artifact_dir(h.workspace, h.run_id).glob("a-*.json"))
    assert len(files) == 1
    if damage == "missing":
        files[0].unlink()
    else:
        files[0].write_text("{nao e json", encoding="utf-8")
    outcome = h.resume()
    assert outcome.state == "succeeded", outcome
    assert h.launcher.launch_count_by_step["a"] == 2  # redone, not reused as `null`
    reused = {e.payload["step_id"] for e in h.sink.of("pipeline.step_reused")}
    assert "a" not in reused
    degraded = [e for e in h.sink.of("pipeline.degraded") if e.payload["reason"] == "artifact_unreadable"]
    assert len(degraded) == 1 and degraded[0].payload["step_id"] == "a"
    prompt_b = [call.prompt for call in h.launcher.calls if call.step_id == "b"][-1]
    assert "relatorio refeito" in prompt_b and ">null<" not in prompt_b


class _StoreWithAnUnreadableOutput:
    """Just enough of ``JournalStore`` for the planner: one finished record whose artifact cannot be read."""

    def __init__(self, state: str) -> None:
        from types import SimpleNamespace

        self.record = SimpleNamespace(
            step_id="s", item_key="", attempt=1, input_hash="h", state=state, agent_id="a1", output={"ref": "gone.json"},
            loop_count=0, started_at=None,
        )

    def find_reusable(self, run_id, step_id, item_key, loop_count):
        return self.record

    def load_output(self, run_id, output):
        raise OSError("artefato ilegivel")

    def steps_of(self, run_id):
        return [self.record]


def test_the_planner_runs_agent_and_gate_again_when_the_output_cannot_be_read() -> None:
    from agentos.pipelines.resume import ResumePlanner

    agent = ResumePlanner(_StoreWithAnUnreadableOutput("succeeded"), "run-1", enabled=True)
    decision = agent.decide_agent("s", "", 0, "h", worktree=False)
    assert decision.action == "run" and decision.reason == "artifact_unreadable"
    gate = ResumePlanner(_StoreWithAnUnreadableOutput("succeeded"), "run-1", enabled=True)
    decision = gate.decide_gate("s", 0, "h", dirty=False)
    assert decision.action == "run" and decision.reason == "artifact_unreadable"


def test_a_gate_is_rerun_when_an_agent_before_it_ran_again(tmp_path) -> None:
    text = pipeline(
        """
        - id: a
          agent: general
          prompt: faz
        - id: g
          gate:
            all:
              - command: ${{ inputs.testes }}
        - id: depois
          agent: general
          prompt: fim
        """,
        HEADER,
    )
    h = make_harness(tmp_path, text)

    def dies(call, control):
        raise SimulatedCrash()

    h.launcher.script("a", Reply(text="primeira"))
    h.launcher.script("depois", dies, Reply(text="ok"))
    with pytest.raises(SimulatedCrash):
        h.run()
    h.inputs["objetivo"] = "mudou"  # does not reach any prompt: everything is reused, the gate too
    assert h.resume().state == "succeeded"
    assert len(h.gates.commands) == 1

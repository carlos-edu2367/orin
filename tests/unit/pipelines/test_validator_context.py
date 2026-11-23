from __future__ import annotations

from agentos.pipelines.validator import ValidationContext, validate_context, validate_structure

from .helpers import fixture_text, pipeline

MODELS = {
    "anthropic": frozenset({"claude-sonnet-5-5", "claude-haiku-5-5"}),
    "openai-codex": frozenset({"gpt-6-luna"}),
}
AGENTS = frozenset({"plan", "general", "explore", "verifier"})


def efforts(provider: str, model: str):
    return ("low", "medium", "high") if model == "claude-haiku-5-5" else ("low", "medium", "high", "xhigh", "max")


def ctx(**kw) -> ValidationContext:
    base = dict(known_agents=AGENTS, models=MODELS, supported_efforts=efforts)
    base.update(kw)
    return ValidationContext(**base)  # type: ignore[arg-type]


def definition(steps: str, header_extra: str = ""):
    from .helpers import HEADER

    parsed, report = validate_structure(pipeline(steps, header=HEADER + header_extra))
    assert parsed is not None, report.errors
    return parsed


def run(steps: str, context: ValidationContext, header_extra: str = ""):
    return validate_context(definition(steps, header_extra), context)


def test_spec_example_passes_with_everything_enabled() -> None:
    parsed, _ = validate_structure(fixture_text("implementar-verificar.yaml"))
    assert parsed is not None
    result = validate_context(parsed, ctx())
    assert result.errors == ()


def test_unknown_agent_points_at_the_line() -> None:
    result = run("- id: a\n  agent: fantasma\n  prompt: x\n", ctx())
    assert result.codes() == ("E_AGENT_UNKNOWN",)
    assert result.errors[0].line is not None
    assert "fantasma" in result.errors[0].message


def test_unknown_provider_and_model() -> None:
    assert run("- id: a\n  agent: plan\n  prompt: x\n  provider: nuvem\n", ctx()).codes() == ("E_MODEL_UNKNOWN",)
    assert run("- id: a\n  agent: plan\n  prompt: x\n  provider: anthropic\n  model: opus-9\n", ctx()).codes() == ("E_MODEL_UNKNOWN",)
    assert run("- id: a\n  agent: plan\n  prompt: x\n  model: opus-9\n", ctx()).codes() == ("E_MODEL_UNKNOWN",)
    assert run("- id: a\n  agent: plan\n  prompt: x\n  provider: anthropic\n  model: claude-haiku-5-5\n", ctx()).errors == ()


def test_model_in_defaults_is_checked() -> None:
    header = "defaults: {provider: anthropic, model: nao-existe}\n"
    assert run("- id: a\n  agent: plan\n  prompt: x\n", ctx(), header).codes() == ("E_MODEL_UNKNOWN",)


def test_effort_the_model_does_not_accept_is_only_a_warning() -> None:
    steps = "- id: a\n  agent: plan\n  prompt: x\n  provider: anthropic\n  model: claude-haiku-5-5\n  effort: max\n"
    result = run(steps, ctx())
    assert result.errors == ()
    assert result.warning_codes() == ("W_EFFORT_ADJUSTED",)


def test_worktree_isolation_disabled_points_at_the_line() -> None:
    steps = "- id: a\n  agent: general\n  prompt: x\n  isolation: worktree\n"
    result = run(steps, ctx(worktree_enabled=False))
    assert result.codes() == ("E_ISOLATION_DISABLED",)
    assert result.errors[0].line is not None
    assert run(steps, ctx(worktree_enabled=True)).errors == ()


def test_parallel_worktree_outside_git_is_refused_but_single_is_degraded_later() -> None:
    parallel = "- id: a\n  agent: general\n  prompt: x\n  isolation: worktree\n  map: {over: [1, 2], as: i, max_parallel: 2}\n"
    single = parallel.replace("max_parallel: 2", "max_parallel: 1")
    assert run(parallel, ctx(is_git=False)).codes() == ("E_ISOLATION_UNAVAILABLE",)
    assert run(single, ctx(is_git=False)).errors == ()
    assert run(parallel, ctx(is_git=True)).errors == ()


def test_verifier_gate_without_a_verifier() -> None:
    steps = "- id: g\n  gate: {all: [{command: pytest}, {verifier: {policy: cross_family}}]}\n"
    assert run(steps, ctx(verifier_available=False)).codes() == ("E_MODEL_UNKNOWN",)
    assert run(steps, ctx(verifier_available=True)).errors == ()


def test_pipelines_disabled() -> None:
    result = run("- id: a\n  agent: plan\n  prompt: x\n", ctx(pipelines_enabled=False))
    assert result.codes() == ("E_DISABLED",)


def test_permissive_context_checks_nothing() -> None:
    steps = "- id: a\n  agent: fantasma\n  prompt: x\n  provider: nuvem\n  isolation: worktree\n"
    assert run(steps, ValidationContext()).errors == ()


def test_templated_model_is_not_checked_here() -> None:
    steps = "- id: a\n  agent: plan\n  prompt: x\n  model: ${{ inputs.arquivo }}\n"
    assert run(steps, ctx()).errors == ()

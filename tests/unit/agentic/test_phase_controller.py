from __future__ import annotations

from agentos.agentic.contract import TOOLKITS
from agentos.agentic.phases import (
    DEFAULT_PHASE_BUDGETS,
    PHASE_INSTRUCTIONS,
    Phase,
    PhaseBudget,
    PhaseController,
    kinds_for,
    tools_for,
)


def _controller(**overrides: object) -> PhaseController:
    return PhaseController(**overrides)  # type: ignore[arg-type]


# -- transitions ------------------------------------------------------------


def test_a_turn_starts_by_orienting() -> None:
    assert _controller().current is Phase.ORIENT


def test_a_model_that_cannot_call_tools_only_responds() -> None:
    """The phase machinery would be pure overhead for a model with no tools."""
    assert _controller(model_calls_tools=False).current is Phase.RESPOND


def test_a_follow_up_on_a_contracted_task_resumes_the_work() -> None:
    """Re-orienting would rediscover what the transcript already carries."""
    assert _controller(resumed_contract=True).current is Phase.EXECUTE


def test_writing_a_contract_while_orienting_skips_the_planning_stage() -> None:
    """The agent already planned; making it plan again would be ceremony."""
    controller = _controller()
    controller.note_iteration(1)
    controller.observe(wrote_contract=True)
    assert controller.current is Phase.EXECUTE


def test_orienting_past_its_budget_forces_planning() -> None:
    """This is the intervention: six fruitless iterations means stop and commit."""
    controller = _controller()
    for _ in range(DEFAULT_PHASE_BUDGETS[Phase.ORIENT].iterations):
        controller.note_iteration(1)
        controller.observe(wrote_contract=False)
    assert controller.current is Phase.PLAN


def test_spending_the_action_budget_also_forces_the_next_phase() -> None:
    controller = _controller(budgets={**DEFAULT_PHASE_BUDGETS, Phase.ORIENT: PhaseBudget(iterations=99, actions=3)})
    controller.note_iteration(4)
    controller.observe(wrote_contract=False)
    assert controller.current is Phase.PLAN


def test_planning_without_a_contract_still_reaches_execution() -> None:
    """A weak model that cannot fill the schema must not stall the turn."""
    controller = _controller()
    controller._enter(Phase.PLAN)
    for _ in range(DEFAULT_PHASE_BUDGETS[Phase.PLAN].iterations):
        controller.note_iteration(1)
        controller.observe(wrote_contract=False)
    assert controller.current is Phase.EXECUTE


def test_execution_running_out_hands_the_work_to_verification() -> None:
    controller = _controller()
    controller._enter(Phase.EXECUTE)
    for _ in range(DEFAULT_PHASE_BUDGETS[Phase.EXECUTE].iterations):
        controller.note_iteration(1)
        controller.observe(wrote_contract=False)
    assert controller.current is Phase.VERIFY


def test_verification_running_out_leads_to_the_answer() -> None:
    controller = _controller()
    controller._enter(Phase.VERIFY)
    for _ in range(DEFAULT_PHASE_BUDGETS[Phase.VERIFY].iterations):
        controller.note_iteration(1)
        controller.observe(wrote_contract=False)
    assert controller.current is Phase.RESPOND
    assert controller.is_final


def test_the_final_phase_does_not_advance_past_itself() -> None:
    controller = _controller()
    controller._enter(Phase.RESPOND)
    for _ in range(5):
        controller.note_iteration(1)
        controller.observe(wrote_contract=False)
    assert controller.current is Phase.RESPOND


def test_entering_a_phase_resets_its_budget() -> None:
    controller = _controller()
    controller.note_iteration(5)
    controller.observe(wrote_contract=True)
    assert controller.exhausted is False


def test_a_productive_iteration_does_not_spend_the_phase_budget() -> None:
    """The budget exists to catch flailing, not to cap real progress."""
    controller = _controller()
    controller._enter(Phase.EXECUTE)
    for _ in range(DEFAULT_PHASE_BUDGETS[Phase.EXECUTE].iterations * 2):
        controller.note_iteration(1, productive=True)
        controller.observe(wrote_contract=False)
    assert controller.current is Phase.EXECUTE
    assert controller.exhausted is False


def test_force_verify_enters_verification_regardless_of_budget() -> None:
    controller = _controller()
    controller._enter(Phase.EXECUTE)
    controller.note_iteration(1)

    controller.force_verify()

    assert controller.current is Phase.VERIFY
    assert controller.exhausted is False


def test_force_respond_ends_verification_early() -> None:
    controller = _controller()
    controller._enter(Phase.VERIFY)

    controller.force_respond()

    assert controller.current is Phase.RESPOND
    assert controller.is_final


# -- published tools --------------------------------------------------------


def test_orienting_carries_the_working_tools_so_a_simple_task_is_not_slower() -> None:
    """Sequencing that made "create a file" cost extra round trips would be a
    regression dressed as an improvement."""
    names = tools_for(Phase.ORIENT)
    assert {"read_file", "write_file", "edit_file", "run_command"} <= names


def test_orienting_withholds_the_families_that_caused_the_bloat() -> None:
    names = tools_for(Phase.ORIENT)
    assert not any(name.startswith("browser") for name in names)
    assert "ask_agents" not in names
    assert "browse_page" not in names


def test_a_declared_toolkit_opens_its_family() -> None:
    names = tools_for(Phase.EXECUTE, frozenset({"browser"}))
    assert "browse_page" in names
    assert "browser_click" in names


def test_an_undeclared_family_stays_closed() -> None:
    assert "browse_page" not in tools_for(Phase.EXECUTE, frozenset({"files"}))


def test_verification_can_look_but_never_change() -> None:
    names = tools_for(Phase.VERIFY)
    assert "read_file" in names
    assert "write_file" not in names
    assert "edit_file" not in names


def test_verification_carries_its_own_mechanical_checks_and_a_way_to_conclude() -> None:
    names = tools_for(Phase.VERIFY)
    assert {"verify_project", "verify_frontend", "report_verification"} <= names


def test_execute_publishes_project_verification_without_exceeding_the_tool_cap() -> None:
    names = tools_for(Phase.EXECUTE, frozenset({"files", "terminal"}))

    assert "verify_project" in names
    assert len(names) <= 16


def test_a_declared_toolkit_does_not_reopen_writing_during_verification() -> None:
    """Verification is read-only regardless of what the contract declared."""
    assert "write_file" not in tools_for(Phase.VERIFY, frozenset(TOOLKITS))


def test_the_answering_phase_publishes_nothing() -> None:
    assert tools_for(Phase.RESPOND) == frozenset()


def test_runtime_discovered_families_are_opened_by_kind() -> None:
    """MCP and plugin tool names are not known when the phase sets are written."""
    assert kinds_for(frozenset({"mcp"})) == frozenset({"mcp"})
    assert kinds_for(frozenset({"files"})) == frozenset()


def test_every_phase_publishes_a_set_a_small_model_can_navigate() -> None:
    for phase in Phase:
        assert len(tools_for(phase, frozenset({"files", "terminal"}))) <= 16


def test_every_phase_says_what_it_expects() -> None:
    for phase in Phase:
        assert PHASE_INSTRUCTIONS[phase].startswith("## Agora")

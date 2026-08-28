from __future__ import annotations

import pytest

from agentos.agentic.contract import TOOLKITS, ContractError, TaskContract, parse, synthesize


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "objective": "Reformular o orçamento com margem de 18%.",
        "deliverables": [{"path": "orcamento-v2.xlsx", "description": "planilha revisada"}],
        "constraints": ["não alterar os itens já aprovados"],
        "acceptance": [{"id": "total", "check": "o total reflete a margem de 18%", "how": "inspection"}],
        "toolkits": ["files"],
        "steps": ["ler a planilha", "recalcular", "gravar a versão nova"],
    }
    payload.update(overrides)
    return payload


# -- validation -------------------------------------------------------------


def test_a_complete_contract_parses() -> None:
    contract = parse(_payload())
    assert contract.objective.startswith("Reformular")
    assert contract.toolkits == frozenset({"files"})
    assert contract.acceptance[0].id == "total"


@pytest.mark.parametrize("field", ["objective", "acceptance", "toolkits"])
def test_a_missing_required_field_names_itself(field: str) -> None:
    payload = _payload()
    payload.pop(field)
    with pytest.raises(ContractError) as error:
        parse(payload)
    assert field in str(error.value)


def test_a_blank_objective_is_not_an_objective() -> None:
    with pytest.raises(ContractError):
        parse(_payload(objective="   "))


def test_an_unknown_toolkit_is_refused_with_the_valid_names() -> None:
    with pytest.raises(ContractError) as error:
        parse(_payload(toolkits=["telepathy"]))
    assert "telepathy" in str(error.value)
    assert "files" in str(error.value)


def test_an_acceptance_item_needs_a_check() -> None:
    with pytest.raises(ContractError):
        parse(_payload(acceptance=[{"id": "total", "how": "inspection"}]))


def test_an_unknown_verification_mode_is_refused() -> None:
    with pytest.raises(ContractError):
        parse(_payload(acceptance=[{"id": "t", "check": "algo", "how": "vibes"}]))


def test_deliverables_and_constraints_are_optional() -> None:
    payload = _payload()
    payload.pop("deliverables")
    payload.pop("constraints")
    contract = parse(payload)
    assert contract.deliverables == ()
    assert contract.constraints == ()


# -- rendering --------------------------------------------------------------


def test_the_rendered_block_carries_every_part_the_agent_must_respect() -> None:
    rendered = parse(_payload()).render()
    assert "Reformular o orçamento" in rendered
    assert "orcamento-v2.xlsx" in rendered
    assert "não alterar os itens já aprovados" in rendered
    assert "o total reflete a margem de 18%" in rendered


# -- synthesis --------------------------------------------------------------


def test_synthesis_produces_a_valid_contract_from_the_request_alone() -> None:
    """A model too weak to fill the schema must not be able to stall the turn."""
    contract = synthesize("Reformule o orçamento com 18% de margem.")
    assert isinstance(contract, TaskContract)
    assert "Reformule o orçamento" in contract.objective
    assert contract.toolkits == frozenset({"files"})
    assert contract.acceptance


def test_synthesis_bounds_a_very_long_request() -> None:
    contract = synthesize("x" * 5_000)
    assert len(contract.objective) <= 500


def test_every_declared_toolkit_name_is_known() -> None:
    assert TOOLKITS == frozenset({"files", "terminal", "web", "browser", "delegation", "mcp", "plugins"})


# -- runtime pinning --------------------------------------------------------


class _Store:
    def __init__(self) -> None:
        self.turn = {
            "turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1",
            "provider": "openrouter", "model_id": "local-model",
            "user_message_id": "user-message-1", "assistant_message_id": "assistant-message-1",
        }

    def load(self, turn_id: str): return self.turn
    def history_for_turn(self, turn): return [{"role": "user", "content": "reformule"}]
    def lifecycle(self, turn, state, **payload) -> None: ...
    def delta(self, turn, text) -> None: ...
    def finish(self, turn, *, failed: bool = False, code: str | None = None) -> None: ...


def _runtime_with_contract():
    from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime

    runtime = AgenticTurnRuntime(
        store=_Store(), provider=object(), system_prompt="prompt",
        limits=AgenticLimits(max_context_tokens=2_000),
    )
    runtime.contract = parse(_payload())
    return runtime


def test_the_contract_survives_a_window_too_small_for_the_history() -> None:
    """Trimming may drop everything else; it may never drop the task."""
    runtime = _runtime_with_contract()
    messages = [{"role": "user", "content": "reformule o orçamento"}]
    messages += [{"role": "assistant", "content": "x" * 4_000} for _ in range(60)]

    window = runtime._request_messages(messages)

    assert any("Contrato desta tarefa" in str(item.get("content", "")) for item in window)


def test_compaction_cannot_fold_the_contract_away() -> None:
    runtime = _runtime_with_contract()
    messages = [{"role": "user", "content": f"passo {index} " + "x" * 600} for index in range(10)]
    messages.append({"role": "user", "content": "reformule"})
    runtime._pinned_index = len(messages) - 1

    runtime._maybe_compact(messages, {"turn_id": "turn-1"}, [])
    window = runtime._request_messages(messages)

    assert any("Contrato desta tarefa" in str(item.get("content", "")) for item in window)


def test_no_contract_leaves_the_request_exactly_as_before() -> None:
    from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime

    runtime = AgenticTurnRuntime(
        store=_Store(), provider=object(), system_prompt="prompt",
        limits=AgenticLimits(max_context_tokens=60_000),
    )
    messages = [{"role": "user", "content": "oi"}]

    assert runtime._request_messages(messages) == [{"role": "system", "content": "prompt"}, *messages]


def test_the_runtime_adopts_a_contract_returned_by_the_planning_tool() -> None:
    from agentos.agentic.agent_tools import ToolOutcome
    from agentos.agentic.runtime import AgenticTurnRuntime

    runtime = AgenticTurnRuntime(store=_Store(), provider=object())
    runtime._absorb_contract(ToolOutcome("succeeded", "ok", "ok", {"contract": _payload()}))

    assert runtime.contract is not None
    assert runtime.contract.objective.startswith("Reformular")


def test_a_rejected_contract_is_counted_and_does_not_become_the_contract() -> None:
    from agentos.agentic.agent_tools import ToolOutcome
    from agentos.agentic.runtime import AgenticTurnRuntime

    runtime = AgenticTurnRuntime(store=_Store(), provider=object())
    runtime._absorb_contract(ToolOutcome("failed", "no", "falta objective", {}, "INVALID_CONTRACT"))

    assert runtime.contract is None
    assert runtime._rejected_contracts == 1


def test_the_planning_tool_rejects_an_incomplete_contract_without_raising() -> None:
    from agentos.agentic.agent_tools import AgentToolset

    outcome = AgentToolset.write_contract(object.__new__(AgentToolset), objective="fazer algo")

    assert outcome.status == "failed"
    assert outcome.error_code == "INVALID_CONTRACT"
    assert "acceptance" in outcome.content

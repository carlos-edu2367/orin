from __future__ import annotations

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.phases import DEFAULT_PHASE_BUDGETS, Phase, PhaseBudget, PhaseController
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime


def _turn() -> dict[str, object]:
    return {
        "turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1",
        "provider": "openrouter", "model_id": "local-model",
        "user_message_id": "user-message-1", "assistant_message_id": "assistant-message-1",
    }


class _Store:
    def __init__(self) -> None:
        self.turn = _turn()
        self.events: list[tuple[str, dict[str, object]]] = []
        self.text: list[str] = []

    def load(self, turn_id: str): return self.turn
    def history_for_turn(self, turn): return [{"role": "user", "content": "reformule o orçamento"}]
    def lifecycle(self, turn, state, **payload) -> None: self.events.append((state, payload))
    def delta(self, turn, text) -> None: self.text.append(text)
    def finish(self, turn, *, failed: bool = False, code: str | None = None) -> None: ...


def _tool_call(call_id: str, name: str, arguments: str) -> str:
    return (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"%s","function":'
        '{"name":"%s","arguments":"%s"}}]},"finish_reason":"tool_calls"}]}' % (call_id, name, arguments)
    )


_ANSWER = 'data: {"choices":[{"delta":{"content":"pronto"},"finish_reason":"stop"}]}'


class _ScriptedProvider:
    """Replays a fixed script of provider responses and records every request."""

    def __init__(self, script: list[str]) -> None:
        self.script = script
        self.requests: list[dict[str, object]] = []

    def stream(self, request):
        self.requests.append(request)
        line = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        return normalize_sse([line, "data: [DONE]"], provider="openrouter")


class _Toolset:
    """Publishes a realistic set and answers by name."""

    NAMES = (
        "write_contract", "ask_user", "read_file", "view_file", "transcribe_pdf", "list_files",
        "search_files", "search_code", "project_map", "write_file", "edit_file", "run_command",
        "recall", "remember", "browse_page", "browser_click", "browser_observe",
        "create_agent", "ask_agent", "ask_agents", "fetch_url", "web_search",
    )
    KINDS = {"browse_page": "browser", "browser_click": "browser", "browser_observe": "browser"}

    def schemas(self, allowed=None, kinds=None):
        names = self.NAMES if allowed is None else [n for n in self.NAMES if n in set(allowed)]
        return [{"type": "function", "function": {"name": name, "parameters": {}}} for name in names]

    def is_read_only(self, name): return name in {"read_file", "list_files", "search_files"}
    def argument_names(self, name): return None

    def invoke(self, name, arguments):
        if name == "write_contract":
            from agentos.agentic.contract import parse

            contract = parse({
                "objective": "Reformular o orçamento.",
                "acceptance": [{"id": "t", "check": "o total confere", "how": "inspection"}],
                "toolkits": ["files", "browser"],
            })
            return ToolOutcome("succeeded", "contrato", "ok", {"contract": contract.as_payload()})
        return ToolOutcome("succeeded", name, "resultado", {})


def _published(request: dict[str, object]) -> set[str]:
    return {str(item["function"]["name"]) for item in request["tools"]}


def _runtime(provider, controller, **limits) -> AgenticTurnRuntime:
    return AgenticTurnRuntime(
        store=_Store(), provider=provider, toolset=_Toolset(), system_prompt="prompt",
        limits=AgenticLimits(max_iterations=None, max_actions=None, **limits),
        phase_controller=controller,
    )


def test_without_a_controller_every_tool_is_published_as_before() -> None:
    provider = _ScriptedProvider([_ANSWER])
    AgenticTurnRuntime(store=_Store(), provider=provider, toolset=_Toolset(), system_prompt="p").run("turn-1")
    assert _published(provider.requests[0]) == set(_Toolset.NAMES)


def test_orienting_publishes_a_working_set_not_the_whole_registry() -> None:
    provider = _ScriptedProvider([_ANSWER])
    _runtime(provider, PhaseController()).run("turn-1")

    published = _published(provider.requests[0])
    assert "write_file" in published and "read_file" in published
    assert "browse_page" not in published and "ask_agents" not in published
    assert len(published) < len(_Toolset.NAMES)


def test_a_conversational_turn_still_finishes_in_one_provider_call() -> None:
    """Phases must not make an ordinary question slower than it was."""
    provider = _ScriptedProvider([_ANSWER])
    result = _runtime(provider, PhaseController()).run("turn-1")

    assert result.state == "completed"
    assert len(provider.requests) == 1


def test_declaring_a_toolkit_opens_it_on_the_next_request() -> None:
    provider = _ScriptedProvider([_tool_call("c1", "write_contract", "{}"), _ANSWER])
    runtime = _runtime(provider, PhaseController())
    runtime.run("turn-1")

    assert "browse_page" not in _published(provider.requests[0])
    assert "browse_page" in _published(provider.requests[1])
    assert runtime.phases.current is Phase.EXECUTE


def test_the_contract_appears_in_the_request_once_it_is_written() -> None:
    provider = _ScriptedProvider([_tool_call("c1", "write_contract", "{}"), _ANSWER])
    _runtime(provider, PhaseController()).run("turn-1")

    assert not any("Contrato desta tarefa" in str(m.get("content", "")) for m in provider.requests[0]["messages"])
    assert any("Contrato desta tarefa" in str(m.get("content", "")) for m in provider.requests[1]["messages"])


def test_each_request_carries_the_instructions_of_its_own_phase() -> None:
    provider = _ScriptedProvider([_tool_call("c1", "read_file", "{}"), _ANSWER])
    _runtime(provider, PhaseController()).run("turn-1")

    assert any("## Agora" in str(m.get("content", "")) for m in provider.requests[0]["messages"])


def test_flailing_in_orientation_forces_a_planning_stage() -> None:
    """Six iterations of tool use without finishing is the signal to stop and commit."""
    provider = _ScriptedProvider([_tool_call("c1", "read_file", "{}")] * 6 + [_ANSWER])
    controller = PhaseController()
    runtime = _runtime(provider, controller)
    runtime.run("turn-1")

    planning = [r for r in provider.requests if "declare o plano" in str(r["messages"])]
    assert planning
    assert "write_contract" in _published(planning[0])
    assert "write_file" not in _published(planning[0])


def test_a_model_that_never_writes_a_contract_still_gets_one_and_keeps_working() -> None:
    provider = _ScriptedProvider([_tool_call("c1", "read_file", "{}")] * 12 + [_ANSWER])
    controller = PhaseController(budgets={
        **DEFAULT_PHASE_BUDGETS,
        Phase.ORIENT: PhaseBudget(iterations=2, actions=99),
        Phase.PLAN: PhaseBudget(iterations=2, actions=99),
    })
    runtime = _runtime(provider, controller)
    runtime.run("turn-1")

    assert runtime.contract is not None
    assert "reformule o orçamento" in runtime.contract.objective
    assert runtime.phases.current in (Phase.EXECUTE, Phase.VERIFY, Phase.RESPOND)


def test_a_spent_execution_budget_moves_to_verification_instead_of_failing() -> None:
    """Today this ends as ITERATION_LIMIT with nothing to show."""
    provider = _ScriptedProvider([_tool_call("c1", "read_file", "{}")] * 30 + [_ANSWER])
    controller = PhaseController(budgets={
        **DEFAULT_PHASE_BUDGETS,
        Phase.ORIENT: PhaseBudget(iterations=1, actions=99),
        Phase.PLAN: PhaseBudget(iterations=1, actions=99),
        Phase.EXECUTE: PhaseBudget(iterations=2, actions=99),
    })
    runtime = _runtime(provider, controller)
    result = runtime.run("turn-1")

    verifying = [r for r in provider.requests if "critério por critério" in str(r["messages"])]
    assert verifying
    assert "write_file" not in _published(verifying[0])
    assert result.state == "completed"


def test_the_answering_phase_stops_asking_for_tools() -> None:
    provider = _ScriptedProvider([_tool_call("c1", "read_file", "{}")] * 30 + [_ANSWER])
    controller = PhaseController(budgets={
        phase: PhaseBudget(iterations=1, actions=99) for phase in Phase
    })
    _runtime(provider, controller).run("turn-1")

    closing = [r for r in provider.requests if r.get("tool_choice") == "none"]
    assert closing
    assert closing[0]["tools"] == []


def test_a_phase_change_is_reported_so_the_interface_can_show_it() -> None:
    provider = _ScriptedProvider([_tool_call("c1", "write_contract", "{}"), _ANSWER])
    store = _Store()
    AgenticTurnRuntime(
        store=store, provider=provider, toolset=_Toolset(), system_prompt="p",
        limits=AgenticLimits(max_iterations=None, max_actions=None),
        phase_controller=PhaseController(),
    ).run("turn-1")

    changes = [payload for state, payload in store.events if state == "phase_changed"]
    assert changes
    assert changes[0]["phase"] == "execute"

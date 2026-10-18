from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.providers.models import ProviderUsage


class Store:
    def __init__(self) -> None:
        self.turn = {
            "turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e",
            "provider": "openrouter", "model_id": "m", "user_message_id": "um", "assistant_message_id": "am",
        }
        self.deltas: list[str] = []
        self.finished: list[tuple[str | None, bool]] = []

    def load(self, turn_id):
        return self.turn

    def history_for_turn(self, turn):
        return [{"role": "user", "content": "keep going"}]

    def lifecycle(self, turn, state, **payload):
        pass

    def delta(self, turn, text):
        self.deltas.append(text)

    def finish(self, turn, *, failed=False, code=None):
        self.finished.append((code, failed))


class AlwaysTool:
    """Every response asks for one more tool call, with tiny usage and an optional reported cost."""

    def __init__(self, cost: Decimal | None = None) -> None:
        self.calls = 0
        self.cost = cost

    def stream(self, request):
        self.calls += 1
        return iter([
            NormalizedStreamItem(StreamKind.TOOL_CALL, 1, tool_call_id=f"call-{self.calls}", tool_name="read_file", arguments_delta=f'{{"path": "f{self.calls}.txt"}}'),
            NormalizedStreamItem(StreamKind.USAGE, 2, usage=ProviderUsage(10, 5, 15, None, measurement="CONFIRMED"), cost=self.cost),
            NormalizedStreamItem(StreamKind.FINISH, 3, finish_reason="tool_calls"),
        ])


class Toolset:
    def schemas(self, allowed=None):
        return []

    def is_read_only(self, name):
        return True

    def argument_names(self, name):
        return frozenset({"path"})

    def invoke(self, name, arguments):
        return ToolOutcome("succeeded", "ok", "content")


def _limits(**values) -> AgenticLimits:
    return AgenticLimits(max_iterations=None, max_actions=None, deadline=timedelta(hours=1), **values)


def test_max_turns_stops_the_turn() -> None:
    provider = AlwaysTool()
    result = AgenticTurnRuntime(store=Store(), provider=provider, toolset=Toolset(), limits=_limits(max_turns=3)).run("t")
    assert (result.state, result.error_code, provider.calls) == ("failed", "MAX_TURNS", 3)


def test_the_budget_uses_the_cost_estimator() -> None:
    provider = AlwaysTool()
    runtime = AgenticTurnRuntime(
        store=Store(), provider=provider, toolset=Toolset(), limits=_limits(max_budget_usd=Decimal("1.0")),
        cost_estimator=lambda input_tokens, output_tokens: Decimal("0.4"),
    )
    result = runtime.run("t")
    assert (result.error_code, provider.calls) == ("MAX_BUDGET_USD", 3)


def test_a_reported_cost_is_not_estimated_again() -> None:
    estimates: list[tuple] = []
    provider = AlwaysTool(cost=Decimal("0.6"))
    runtime = AgenticTurnRuntime(
        store=Store(), provider=provider, toolset=Toolset(), limits=_limits(max_budget_usd=Decimal("1.0")),
        cost_estimator=lambda i, o: estimates.append((i, o)) or Decimal("5"),
    )
    result = runtime.run("t")
    assert (result.error_code, provider.calls, estimates) == ("MAX_BUDGET_USD", 2, [])


def test_the_safety_cap_pauses_and_asks_to_continue() -> None:
    store = Store()
    provider = AlwaysTool()
    result = AgenticTurnRuntime(store=store, provider=provider, toolset=Toolset(), limits=_limits(safety_max_tool_rounds=4)).run("t")
    assert (result.state, result.error_code, provider.calls) == ("waiting_user", "SAFETY_TOOL_ROUNDS", 4)
    assert any("safetyMaxToolRounds" in text for text in store.deltas)
    assert store.finished == [("SAFETY_TOOL_ROUNDS", False)]


@pytest.mark.parametrize("values", [
    {"max_turns": 0}, {"safety_max_tool_rounds": 0}, {"max_budget_usd": Decimal("-1")},
    {"retry_base_delay_seconds": -0.1}, {"retry_max_delay_seconds": -1.0}, {"max_retry_after_seconds": -1.0},
])
def test_invalid_limits_are_rejected(values) -> None:
    with pytest.raises(ValueError):
        AgenticLimits(**values)


def test_code_mode_session_removes_the_caps(tmp_path) -> None:
    from agentos.agentic.session import CODE_TURN_DEADLINE, TurnSession

    class _SessionStore(Store):
        def record(self, *args, **kwargs):
            pass

        def main_agent_id(self, turn):
            return "agent:c:main"

    class _Agents:
        def list(self):
            return []

    store = _SessionStore()
    session = TurnSession(
        turn={**store.turn, "code_mode": "code"}, store=store, agents_store=_Agents(), memory_store=None,
        provider_factory=lambda: AlwaysTool(), workspace_root=tmp_path, enable_subagents=False,
    )
    runtime = session.build_runtime()
    assert runtime.limits.max_iterations is None and runtime.limits.max_actions is None
    assert runtime.limits.deadline == CODE_TURN_DEADLINE == timedelta(hours=24)
    assert runtime.limits.safety_max_tool_rounds == 500
    assert runtime.limits.max_provider_retries == session.limits.max_provider_retries  # the worker decides retries

    chat = TurnSession(
        turn=dict(store.turn), store=store, agents_store=_Agents(), memory_store=None,
        provider_factory=lambda: AlwaysTool(), workspace_root=tmp_path, enable_subagents=False,
    )
    assert chat.build_runtime().limits is chat.limits

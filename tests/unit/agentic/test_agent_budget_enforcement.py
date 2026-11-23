"""WS-C4: ``Agent(budget=...)`` and the user's default ceilings, enforced from ``TurnSession.record_usage``."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from agentos.agentic.background_agents import BACKGROUND_AGENTS
from agentos.agentic.events import AgentActivityEventType
from agentos.agentic.provider_stream import NormalizedStreamItem, ProviderUsage, StreamKind
from agentos.agentic.session import SubagentCaller, TurnSession
from agentos.agentic.usage_budget import BudgetLimits, UsageBudget
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import metadata
from agentos.persistence.sqlite import create_local_engine
from agentos.settings.loader import EffectiveSettings, SettingsLayer
from tests.unit.agentic.test_nested_subagents import _Scripted, _TreeStore
from tests.unit.agentic.test_parallel_and_background_subagents import (
    TURN,
    _parallel_tool_calls_batch,
    _text_batch,
)


def _usage(batch: list[NormalizedStreamItem], tokens: int) -> list[NormalizedStreamItem]:
    usage = NormalizedStreamItem(StreamKind.USAGE, 1, usage=ProviderUsage(tokens // 2, tokens - tokens // 2, tokens, None, measurement="CONFIRMED"))
    return [usage, *batch]


def _step(call_id: str, tokens: int = 600) -> list[NormalizedStreamItem]:
    """A model call that reports ``tokens`` and asks for one more tool call (so the child keeps going)."""
    return _usage(_parallel_tool_calls_batch([(call_id, "list_files", {"path": "."})]), tokens)


def _settings(data: dict[str, Any]) -> EffectiveSettings:
    return EffectiveSettings(data=data, provenance={}, layers=(SettingsLayer("user", None, data),), errors=(), trusted=True)


@pytest.fixture()
def agents_store(tmp_path: Path):
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'budget.db').as_posix()}")
    metadata.create_all(engine)
    yield ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
    engine.dispose()


def _session(tmp_path: Path, agents_store, child: _Scripted, store=None, **kwargs: Any) -> TurnSession:
    return TurnSession(
        turn=dict(TURN), store=store or _TreeStore(), agents_store=agents_store, memory_store=None,
        provider_factory=lambda: _Scripted([]), workspace_root=tmp_path, child_provider_factory=lambda _m: child, **kwargs,
    )


def test_child_over_its_token_budget_is_cancelled_and_fails_with_the_partial_report(tmp_path: Path, agents_store) -> None:
    store = _TreeStore()
    child = _Scripted([_step("c1"), _step("c2"), _step("c3"), _text_batch("nunca chega aqui")])
    session = _session(tmp_path, agents_store, child, store=store)
    outcome = session._run_subagent("general", "Varra tudo", budget={"max_tokens": 1000})
    assert outcome.status == "failed" and outcome.error_code == "SUBAGENT_BUDGET_EXCEEDED"
    assert len(child.requests) == 2  # cancelled right after the second call reported 1200 tokens
    assert outcome.payload["budget_exceeded"]["kind"] == "tokens"
    assert outcome.payload["budget_exceeded"]["limit"] == 1000 and outcome.payload["budget_exceeded"]["used"] == 1200
    assert "list_files" in outcome.content  # the partial report (recent tool calls) is kept
    assert "tokens budget exceeded" in outcome.content
    exceeded = [e for e in store.tree_events if e[3].get("error_code") == "SUBAGENT_BUDGET_EXCEEDED"]
    assert exceeded and exceeded[0][1] == outcome.payload["agent_id"]
    assert exceeded[0][0] == AgentActivityEventType.SUBAGENT_BUDGET_EXCEEDED and exceeded[0][3]["budget_kind"] == "tokens"
    assert session._usage_budgets.get(str(outcome.payload["agent_id"])) is None  # released when the run ended


def test_background_child_over_budget_ends_failed_not_stopped(tmp_path: Path, agents_store) -> None:
    child = _Scripted([_step("c1"), _step("c2"), _text_batch("nunca")])
    session = _session(tmp_path, agents_store, child)
    started = session._run_subagent("general", "Varra", budget={"max_tokens": 1000}, run_in_background=True)
    agent_id = str(started.payload["agent_id"])
    entry = BACKGROUND_AGENTS.get(TURN["conversation_id"], agent_id)
    outcome = entry.future.result(timeout=20)
    assert outcome.error_code == "SUBAGENT_BUDGET_EXCEEDED"
    assert BACKGROUND_AGENTS.get(TURN["conversation_id"], agent_id).status == "failed"
    saved, _ = session.workspace.read_text(str(started.payload["output_file"]))
    assert "budget exceeded" in saved


def test_within_budget_the_child_finishes_normally(tmp_path: Path, agents_store) -> None:
    child = _Scripted([_usage(_text_batch("ok"), 300)])
    outcome = _session(tmp_path, agents_store, child)._run_subagent("general", "Rápido", budget={"max_tokens": 1000})
    assert outcome.status == "succeeded" and "budget_exceeded" not in outcome.payload


def test_grandchild_that_goes_over_also_stops_the_parent_that_has_a_budget(tmp_path: Path, agents_store) -> None:
    child = _Scripted([])
    session = _session(tmp_path, agents_store, child)
    parent_cancel = threading.Event()
    parent_budget = UsageBudget(BudgetLimits(max_tokens=1500), agent_id="agent:parent", cancel=parent_cancel)
    session._usage_budgets.register("agent:parent", parent_budget)
    parent_caller = SubagentCaller(agent_id="agent:parent", name="pai", depth=1, permission_mode="accept_edits")
    child.batches = [_step("g1"), _step("g2"), _step("g3"), _text_batch("nunca")]
    outcome = session._run_subagent("general", "Neto", budget={"max_tokens": 1000}, caller=parent_caller)
    assert outcome.error_code == "SUBAGENT_BUDGET_EXCEEDED"
    assert parent_budget.tokens == 1200  # the grandchild's spending counts in the parent's budget
    assert not parent_cancel.is_set()  # 1200 <= 1500
    more = _Scripted([_step("h1"), _step("h2"), _step("h3"), _text_batch("nunca")])
    session2 = _session(tmp_path, agents_store, more)
    cancel2 = threading.Event()
    root = UsageBudget(BudgetLimits(max_tokens=700), agent_id="agent:parent", cancel=cancel2)
    session2._usage_budgets.register("agent:parent", root)
    outcome2 = session2._run_subagent("general", "Neto sem teto", caller=parent_caller)  # no limit of its own
    assert cancel2.is_set()  # the parent's ceiling tripped through the child's spending
    assert outcome2.status == "failed" and outcome2.error_code == "SUBAGENT_BUDGET_EXCEEDED"
    assert outcome2.payload["budget_exceeded"]["agent_id"] == "agent:parent"  # whose ceiling it was
    assert len(more.requests) == 2  # the child itself stopped as soon as it spent under a budget that was over


def test_a_budget_registered_for_the_main_agent_covers_every_child_it_starts(tmp_path: Path, agents_store) -> None:
    """How a pipeline run caps its agents: its own budget lives under the main agent's id."""
    child = _Scripted([_step("p1"), _step("p2"), _text_batch("nunca")])
    session = _session(tmp_path, agents_store, child)
    cancel = threading.Event()
    pipeline = UsageBudget(BudgetLimits(max_tokens=1000), agent_id="pipeline-run", cancel=cancel)
    session._usage_budgets.register(session.main_agent_id, pipeline)
    outcome = session._run_subagent("general", "Etapa")  # the main agent delegates; the call has no budget
    assert pipeline.tokens == 1200 and cancel.is_set()
    assert outcome.status == "failed" and outcome.error_code == "SUBAGENT_BUDGET_EXCEEDED"
    assert outcome.payload["budget_exceeded"]["agent_id"] == "pipeline-run"


def test_user_default_applies_when_the_call_has_no_budget_and_caps_a_larger_one(tmp_path: Path, agents_store) -> None:
    defaults = _settings({"orchestration": {"budget": {"subagent": {"maxTokens": 1000}}}})
    child = _Scripted([_step("c1"), _step("c2"), _text_batch("nunca")])
    session = _session(tmp_path, agents_store, child)
    session.effective_settings = defaults
    outcome = session._run_subagent("general", "Sem teto no pedido")
    assert outcome.error_code == "SUBAGENT_BUDGET_EXCEEDED" and outcome.payload["budget_exceeded"]["limit"] == 1000

    child2 = _Scripted([_step("d1"), _step("d2"), _text_batch("nunca")])
    session2 = _session(tmp_path, agents_store, child2)
    session2.effective_settings = defaults
    outcome2 = session2._run_subagent("general", "Pede muito", budget={"max_tokens": 5_000_000})
    assert outcome2.error_code == "SUBAGENT_BUDGET_EXCEEDED" and outcome2.payload["budget_exceeded"]["limit"] == 1000

    child3 = _Scripted([_step("e1"), _text_batch("nunca")])
    session3 = _session(tmp_path, agents_store, child3)
    session3.effective_settings = defaults
    outcome3 = session3._run_subagent("general", "Pede menos que o teto", budget={"max_tokens": 500})
    assert outcome3.error_code == "SUBAGENT_BUDGET_EXCEEDED" and outcome3.payload["budget_exceeded"]["limit"] == 500
    assert len(child3.requests) == 1


def test_project_layer_only_lowers_the_default(tmp_path: Path, agents_store) -> None:
    layers = (
        SettingsLayer("user", None, {"orchestration": {"budget": {"subagent": {"maxTokens": 5000}}}}),
        SettingsLayer("project", None, {"orchestration": {"budget": {"subagent": {"maxTokens": 1000}}}}),
    )
    settings = EffectiveSettings(data={}, provenance={}, layers=layers, errors=(), trusted=True)
    child = _Scripted([_step("c1"), _step("c2"), _text_batch("nunca")])
    session = _session(tmp_path, agents_store, child)
    session.effective_settings = settings
    outcome = session._run_subagent("general", "Varra")
    assert outcome.error_code == "SUBAGENT_BUDGET_EXCEEDED" and outcome.payload["budget_exceeded"]["limit"] == 1000


@pytest.mark.parametrize("bad", [{"max_tokens": -1}, {"max_tokens": "10"}, {"nope": 1}, {"max_cost_usd": 0}, [1]])
def test_invalid_budget_is_invalid_arguments_and_starts_no_child(tmp_path: Path, agents_store, bad: Any) -> None:
    child = _Scripted([])
    outcome = _session(tmp_path, agents_store, child)._run_subagent("general", "x", budget=bad)
    assert outcome.status == "failed" and outcome.error_code == "INVALID_ARGUMENTS"
    assert child.requests == [] and agents_store.list() == []


def test_no_budget_and_no_default_leaves_no_bookkeeping(tmp_path: Path, agents_store) -> None:
    child = _Scripted([_usage(_text_batch("ok"), 100)])
    session = _session(tmp_path, agents_store, child)
    outcome = session._run_subagent("general", "Livre")
    assert outcome.status == "succeeded"
    assert session._usage_budgets.get(str(outcome.payload["agent_id"])) is None


def test_time_budget_trips_from_a_timer_even_without_usage(tmp_path: Path, agents_store) -> None:
    release = threading.Event()

    class _Hangs:
        requests: list[dict[str, Any]] = []

        def stream(self, request: dict[str, Any]):
            self.requests.append(request)
            release.wait(timeout=10)
            return iter(_text_batch("tarde demais"))

    session = _session(tmp_path, agents_store, _Hangs())  # type: ignore[arg-type]
    started = session._run_subagent("general", "Lento", budget={"max_seconds": 0.3}, run_in_background=True)
    entry = BACKGROUND_AGENTS.get(TURN["conversation_id"], str(started.payload["agent_id"]))
    budget_seen = threading.Event()

    def watch() -> None:
        for _ in range(100):
            budget = session._usage_budgets.get(str(started.payload["agent_id"]))
            if budget is not None and budget.cancel is not None and budget.cancel.wait(timeout=0.1):
                budget_seen.set()
                return

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    thread.join(timeout=15)
    release.set()
    outcome = entry.future.result(timeout=20)
    assert budget_seen.is_set()
    assert outcome.error_code == "SUBAGENT_BUDGET_EXCEEDED" and outcome.payload["budget_exceeded"]["kind"] == "time"
    assert json.dumps(outcome.payload["budget_exceeded"])

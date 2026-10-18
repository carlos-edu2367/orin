from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from agentos.agentic.compaction import COMPACTION_SECTIONS, MICROCOMPACT_MARKER
from agentos.agentic.loop_settings import BudgetMeter
from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.providers.models import ProviderUsage
from agentos.settings.paths import orin_user_dir


def _conversation(results: int, size: int = 500) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = [{"role": "user", "content": "task"}]
    for index in range(results):
        messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{index}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
        ]})
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": f"r{index} " + "x" * size})
    return messages


def _runtime(**limits) -> AgenticTurnRuntime:
    return AgenticTurnRuntime(store=object(), provider=object(), limits=AgenticLimits(**limits))


def test_the_eight_newest_results_stay_intact() -> None:
    messages = _conversation(12)
    runtime = _runtime()
    runtime._pinned_index = 0
    assert runtime._microcompact(messages) == 4
    results = [m for m in messages if m.get("role") == "tool"]
    assert [m["content"] for m in results[:4]] == [MICROCOMPACT_MARKER] * 4
    assert all(str(m["content"]).startswith(f"r{index + 4} ") for index, m in enumerate(results[4:]))
    assert runtime._microcompact(messages) == 0


def test_results_after_the_last_tool_call_are_never_cleared() -> None:
    """W3-R18: one batch of 12 results the model has not read yet is kept whole, even beyond the 8 newest."""
    calls = [{"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": f"f{i}.py"})}} for i in range(12)]
    messages: list[dict[str, object]] = [{"role": "user", "content": "review these twelve files"}, {"role": "assistant", "content": "", "tool_calls": calls}]
    messages += [{"role": "tool", "tool_call_id": f"c{i}", "content": f"contents of f{i}.py " + "x" * 400} for i in range(12)]
    runtime = _runtime()
    runtime._pinned_index = 0
    assert runtime._microcompact(messages) == 0
    assert all(m["content"] != MICROCOMPACT_MARKER for m in messages if m.get("role") == "tool")


def test_small_results_are_not_worth_clearing() -> None:
    messages = _conversation(12, size=50)
    runtime = _runtime()
    runtime._pinned_index = 0
    assert runtime._microcompact(messages) == 0


def test_the_pinned_request_is_never_touched() -> None:
    messages = _conversation(12)
    messages.insert(1, {"role": "user", "content": "x" * 900})
    runtime = _runtime()
    runtime._pinned_index = 1
    runtime._microcompact(messages)
    assert messages[1]["content"] == "x" * 900


class Store:
    def __init__(self) -> None:
        self.boundaries: list[dict] = []

    def record_compact_boundary(self, turn, **values):
        self.boundaries.append(dict(values))


class Summarizer:
    def stream(self, request):
        return iter([NormalizedStreamItem(StreamKind.TEXT, 1, text="\n".join(f"### {s}\n- d" for s in COMPACTION_SECTIONS))])


def test_a_microcompaction_is_persisted_as_a_boundary() -> None:
    store = Store()
    runtime = AgenticTurnRuntime(store=store, provider=Summarizer(), limits=AgenticLimits(max_context_tokens=4_000))
    messages = _conversation(20)
    runtime._pinned_index = 0
    runtime._maybe_compact(messages, {"turn_id": "t"}, [])
    assert store.boundaries and store.boundaries[0]["kind"] == "microcompact"
    assert store.boundaries[0]["pre_tokens"] > store.boundaries[0]["post_tokens"]


def test_the_autocompact_threshold() -> None:
    assert _runtime(max_context_tokens=180_000, context_window_tokens=200_000)._autocompact_threshold() == 167_000
    assert _runtime(max_context_tokens=180_000, context_window_tokens=200_000, max_output_tokens=30_000)._autocompact_threshold() == 157_000
    assert _runtime(max_context_tokens=1_000)._autocompact_threshold() == 820
    tuned = _runtime(max_context_tokens=180_000, context_window_tokens=200_000)
    tuned.autocompact = 50_000
    assert tuned._autocompact_threshold() == 50_000
    tuned.autocompact = 10**9
    assert tuned._autocompact_threshold() == 180_000
    tuned.autocompact = False
    assert tuned._autocompact_threshold() is None


def test_autocompact_off_never_summarizes() -> None:
    runtime = AgenticTurnRuntime(store=Store(), provider=Summarizer(), limits=AgenticLimits(max_context_tokens=1_000), autocompact=False)
    calls: list[int] = []
    runtime._run_compaction = lambda *args, **kwargs: calls.append(1) or True
    messages = [{"role": "user", "content": f"old {index} " + "x" * 500} for index in range(9)]
    runtime._pinned_index = len(messages) - 1
    runtime._maybe_compact(messages, {"turn_id": "t"}, [])
    assert calls == []


def _session_autocompact(tmp_path: Path, settings: dict, repository: dict | None = None) -> object:
    from agentos.agentic.session import TurnSession

    path = orin_user_dir() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings), encoding="utf-8")

    class _Store:
        turn = {"turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e", "provider": "openrouter",
                "model_id": "m", "user_message_id": "um", "assistant_message_id": "am"}

        def load(self, turn_id): return self.turn
        def history_for_turn(self, turn): return [{"role": "user", "content": "go"}]
        def lifecycle(self, turn, state, **payload): pass
        def delta(self, turn, text): pass
        def finish(self, turn, *, failed=False, code=None): pass
        def record(self, *args, **kwargs): pass
        def main_agent_id(self, turn): return "agent:c:main"

    class _Agents:
        def list(self): return []

    store = _Store()
    session = TurnSession(turn=dict(store.turn), store=store, agents_store=_Agents(), memory_store=None,
                          provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False)
    if repository is not None:  # the workspace is a folder of its own (per conversation), not tmp_path
        repo_path = session.workspace.root / ".claude" / "settings.json"
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        repo_path.write_text(json.dumps(repository), encoding="utf-8")
    return session.build_runtime().autocompact


def test_the_session_reads_autocompact(tmp_path: Path) -> None:
    assert _session_autocompact(tmp_path, {}) == "auto"
    assert _session_autocompact(tmp_path, {"autoCompact": False}) is False
    assert _session_autocompact(tmp_path, {"autoCompact": "120000"}) == 120_000
    assert _session_autocompact(tmp_path, {"autoCompactEnabled": False}) is False


def test_a_repository_cannot_lower_the_autocompact_threshold(tmp_path: Path) -> None:
    assert _session_autocompact(tmp_path, {}, repository={"autoCompact": 1}) == "auto"
    # The operator's threshold stands; a repository's integer is ignored, not averaged or summed.
    assert _session_autocompact(tmp_path, {"autoCompact": 120000}, repository={"autoCompact": 1}) == 120_000
    # A repository may still turn autocompaction off, and a repository "auto" changes nothing.
    assert _session_autocompact(tmp_path, {}, repository={"autoCompact": False}) is False
    assert _session_autocompact(tmp_path, {"autoCompact": 120000}, repository={"autoCompact": "auto"}) == 120_000


def test_an_operator_threshold_has_a_floor() -> None:
    runtime = _runtime(max_context_tokens=180_000, context_window_tokens=200_000)
    runtime.autocompact = 1
    assert runtime._autocompact_threshold() == 45_000  # 25% of max_context_tokens
    runtime.autocompact = 45_000
    assert runtime._autocompact_threshold() == 45_000


class CostedSummarizer:
    def stream(self, request):
        return iter([
            NormalizedStreamItem(StreamKind.TEXT, 1, text="\n".join(f"### {s}\n- d" for s in COMPACTION_SECTIONS)),
            NormalizedStreamItem(StreamKind.USAGE, 2, usage=ProviderUsage(1_000, 200, 1_200, None, measurement="CONFIRMED")),
        ])


def test_the_summary_call_counts_toward_the_budget() -> None:
    meter = BudgetMeter()
    runtime = AgenticTurnRuntime(
        store=Store(), provider=CostedSummarizer(),
        limits=AgenticLimits(max_context_tokens=4_000, max_budget_usd=Decimal("0.2")),
        cost_estimator=lambda input_tokens, output_tokens: Decimal("0.25"),
        budget_meter=meter,
    )
    messages = _conversation(20)
    runtime._pinned_index = 0
    assert runtime._run_compaction(messages, {"turn_id": "t"}, keep_units=2)
    assert meter.total == Decimal("0.25")
    assert runtime._spent_usd(Decimal("0")) >= Decimal("0.2")  # the next budget check sees the summary

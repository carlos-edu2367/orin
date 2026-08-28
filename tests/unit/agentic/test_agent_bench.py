from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _bench():
    spec = importlib.util.spec_from_file_location("agent_bench", REPOSITORY_ROOT / "scripts" / "agent_bench.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


bench = _bench()


def _row(**overrides: object) -> dict[str, object]:
    row = {
        "provider": "openrouter", "model_id": "anthropic/claude-sonnet-4",
        "turns": 20, "completed_turns": 16, "completion_rate": 0.8,
        "tool_calls": 320, "tool_calls_per_completed_turn": 20.0,
        "redundant_fraction": 0.22, "input_tokens_per_completed_turn": 90000.0,
        "cached_fraction": None,
    }
    row.update(overrides)
    return row


# -- reference tasks --------------------------------------------------------


def test_the_reference_set_has_twelve_tasks_across_four_categories() -> None:
    tasks = bench.load_tasks()
    assert len(tasks) == 12
    assert {task["category"] for task in tasks} == {"documento", "script", "multi-turno", "exploracao"}


def test_every_task_declares_assertable_acceptance() -> None:
    """The bench measures efficiency; it must never judge style."""
    for task in bench.load_tasks():
        assert task["acceptance"], f"{task['id']} has no acceptance"
        for item in task["acceptance"]:
            assert item["kind"] in {"exists", "contains"}
            assert item["path"]


def test_the_multi_turn_tasks_actually_span_turns() -> None:
    """This category is what measures the durable transcript."""
    multi = [task for task in bench.load_tasks() if task["category"] == "multi-turno"]
    assert multi
    for task in multi:
        assert len(task["messages"]) >= 2


# -- comparison -------------------------------------------------------------


def test_fewer_calls_reads_as_an_improvement() -> None:
    assert "✓" in bench.change(10.0, 20.0, "lower")


def test_more_calls_reads_as_a_regression() -> None:
    assert "✗" in bench.change(30.0, 20.0, "lower")


def test_a_higher_completion_rate_reads_as_an_improvement() -> None:
    assert "✓" in bench.change(0.9, 0.6, "higher")


def test_an_unmeasured_value_is_not_reported_as_a_change() -> None:
    assert bench.change(None, 20.0, "lower") == "—"
    assert bench.change(10.0, None, "lower") == "—"


# -- verdict ----------------------------------------------------------------


def test_the_verdict_says_when_the_target_was_met() -> None:
    now = _row(tool_calls_per_completed_turn=9.0, redundant_fraction=0.03)
    verdict = bench._verdict(now, _row())
    assert "dentro do alvo" in verdict
    assert "alvo de 50% atingido" in verdict


def test_the_verdict_says_when_the_target_was_missed() -> None:
    now = _row(tool_calls_per_completed_turn=18.0, redundant_fraction=0.09)
    verdict = bench._verdict(now, _row())
    assert "acima do alvo" in verdict
    assert "não atingido" in verdict


def test_without_a_baseline_the_verdict_asks_for_one() -> None:
    assert "--record" in bench._verdict(_row(), None)


def test_the_table_names_the_model_it_measured() -> None:
    rendered = bench.render(_row(), None)
    assert "anthropic/claude-sonnet-4" in rendered
    assert "Chamadas por turno concluído" in rendered


# -- row selection ----------------------------------------------------------


def test_a_named_model_is_selected_over_the_busiest_one() -> None:
    items = [_row(model_id="other", turns=99), _row(model_id="wanted")]
    assert bench.pick(items, "openrouter", "wanted")["model_id"] == "wanted"


def test_without_a_filter_the_busiest_row_is_used() -> None:
    items = [_row(model_id="busiest"), _row(model_id="other")]
    assert bench.pick(items, None, None)["model_id"] == "busiest"


def test_a_model_with_no_measured_turns_selects_nothing() -> None:
    assert bench.pick([_row()], "openrouter", "never-used") is None


def test_an_empty_window_selects_nothing() -> None:
    assert bench.pick([], None, None) is None

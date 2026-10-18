from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from agentos.agentic.loop_settings import (
    DEFAULT_SAFETY_MAX_TOOL_ROUNDS,
    MAX_SAFETY_MAX_TOOL_ROUNDS,
    code_loop_settings,
    operator_value,
)
from agentos.settings import load_effective_settings
from agentos.settings.paths import orin_managed_dir, orin_user_dir


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_defaults(tmp_path: Path) -> None:
    loop = code_loop_settings(load_effective_settings(tmp_path, trusted=True))
    assert loop.max_turns is None and loop.max_budget_usd is None
    assert loop.safety_max_tool_rounds == DEFAULT_SAFETY_MAX_TOOL_ROUNDS


def test_user_values_apply(tmp_path: Path) -> None:
    _write(orin_user_dir() / "settings.json", {"maxTurns": 40, "maxBudgetUsd": "2.5", "safetyMaxToolRounds": 900})
    loop = code_loop_settings(load_effective_settings(tmp_path, trusted=True))
    assert (loop.max_turns, loop.max_budget_usd, loop.safety_max_tool_rounds) == (40, Decimal("2.5"), 900)


def test_a_repository_lowers_but_never_raises(tmp_path: Path) -> None:
    _write(orin_user_dir() / "settings.json", {"maxTurns": 40, "safetyMaxToolRounds": 300})
    _write(tmp_path / ".orin" / "settings.json", {"maxTurns": 1000, "safetyMaxToolRounds": 50, "maxBudgetUsd": 1})
    loop = code_loop_settings(load_effective_settings(tmp_path, trusted=True))
    assert (loop.max_turns, loop.safety_max_tool_rounds, loop.max_budget_usd) == (40, 50, Decimal("1"))


def test_a_repository_cannot_raise_the_default_safety_cap(tmp_path: Path) -> None:
    _write(tmp_path / ".claude" / "settings.json", {"safetyMaxToolRounds": 99999})
    loop = code_loop_settings(load_effective_settings(tmp_path, trusted=True))
    assert loop.safety_max_tool_rounds == DEFAULT_SAFETY_MAX_TOOL_ROUNDS


def test_the_safety_cap_has_a_hard_ceiling(tmp_path: Path) -> None:
    _write(orin_user_dir() / "settings.json", {"safetyMaxToolRounds": 10**9})
    loop = code_loop_settings(load_effective_settings(tmp_path, trusted=True))
    assert loop.safety_max_tool_rounds == MAX_SAFETY_MAX_TOOL_ROUNDS


def test_invalid_values_are_ignored(tmp_path: Path) -> None:
    _write(orin_user_dir() / "settings.json", {"maxTurns": True, "maxBudgetUsd": "NaN", "safetyMaxToolRounds": -3})
    loop = code_loop_settings(load_effective_settings(tmp_path, trusted=True))
    assert loop.max_turns is None and loop.max_budget_usd is None
    assert loop.safety_max_tool_rounds == DEFAULT_SAFETY_MAX_TOOL_ROUNDS


def test_managed_wins_over_user_for_operator_values(tmp_path: Path) -> None:
    _write(orin_user_dir() / "settings.json", {"fallbackModel": "user-model"})
    _write(orin_managed_dir() / "managed-settings.json", {"fallbackModel": "managed-model"})
    assert operator_value(load_effective_settings(tmp_path, trusted=True), "fallbackModel") == "managed-model"


def test_operator_value_never_reads_a_repository(tmp_path: Path) -> None:
    _write(tmp_path / ".orin" / "settings.json", {"someKey": "repo"})
    assert operator_value(load_effective_settings(tmp_path, trusted=True), "someKey") is None


def test_a_budget_meter_adds_from_many_threads_and_ignores_what_is_not_money() -> None:
    import threading
    from decimal import Decimal

    from agentos.agentic.loop_settings import BudgetMeter

    meter = BudgetMeter()

    def spend() -> None:
        for _ in range(500):
            meter.add(0.001)

    threads = [threading.Thread(target=spend) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    meter.add(None)
    meter.add("not a number")
    meter.add(Decimal("NaN"))
    meter.add(-5)
    assert meter.total == Decimal("4.000")

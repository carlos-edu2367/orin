from __future__ import annotations

from agentos.agentic.settings import AgentRuntimeSettingsStore


def test_runtime_iteration_preference_defaults_to_unlimited_and_persists(tmp_path) -> None:
    store = AgentRuntimeSettingsStore(tmp_path / "runtime.json")

    assert store.get("user-1") == {"max_iterations": None}
    assert store.set_max_iterations("user-1", 48) == {"max_iterations": 48}
    assert AgentRuntimeSettingsStore(tmp_path / "runtime.json").get("user-1") == {"max_iterations": 48}
    assert store.set_max_iterations("user-1", None) == {"max_iterations": None}


def test_runtime_iteration_preference_rejects_zero(tmp_path) -> None:
    store = AgentRuntimeSettingsStore(tmp_path / "runtime.json")

    try:
        store.set_max_iterations("user-1", 0)
    except ValueError as error:
        assert "max_iterations" in str(error)
    else:
        raise AssertionError("zero must not be accepted as an iteration limit")

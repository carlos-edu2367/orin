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


def test_code_mode_settings_persist_without_losing_runtime_limit(tmp_path) -> None:
    store = AgentRuntimeSettingsStore(tmp_path / "runtime.json")
    store.set_max_iterations("user-1", 48)
    saved = store.set_code_mode("user-1", autonomy="code_autonomy", system_notifications=True, monitoring_enabled=False)

    assert saved.as_dict() == {
        "autonomy": "code_autonomy",
        "system_notifications": True,
        "monitoring_enabled": False,
        "notify_permissions": True,
        "notify_completion": True,
        "notification_sound": True,
    }
    restored = AgentRuntimeSettingsStore(tmp_path / "runtime.json")
    assert restored.get("user-1") == {"max_iterations": 48}
    assert restored.get_code_mode("user-1").as_dict() == saved.as_dict()


def test_code_mode_notification_suboptions_persist_and_default_on_legacy_records(tmp_path) -> None:
    path = tmp_path / "runtime.json"
    path.write_text(
        '{"user-1": {"code_autonomy": "approval_required", "code_system_notifications": true, "code_monitoring_enabled": true}}',
        encoding="utf-8",
    )
    store = AgentRuntimeSettingsStore(path)
    legacy = store.get_code_mode("user-1")
    assert legacy.system_notifications is True
    assert legacy.notify_permissions is True
    assert legacy.notify_completion is True
    assert legacy.notification_sound is True

    updated = store.set_code_mode(
        "user-1",
        autonomy="code_autonomy",
        system_notifications=True,
        monitoring_enabled=True,
        notify_permissions=False,
        notify_completion=True,
        notification_sound=False,
    )
    assert updated.notify_permissions is False
    assert updated.notify_completion is True
    assert updated.notification_sound is False
    reloaded = AgentRuntimeSettingsStore(path).get_code_mode("user-1")
    assert reloaded.as_dict() == {
        "autonomy": "code_autonomy",
        "system_notifications": True,
        "monitoring_enabled": True,
        "notify_permissions": False,
        "notify_completion": True,
        "notification_sound": False,
    }

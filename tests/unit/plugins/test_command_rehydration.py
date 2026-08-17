import json
from pathlib import Path

from agentos.plugins.command_library import CommandLibrary
from agentos.plugins.hook_engine import HookEngine
from agentos.plugins.rehydrate import rehydrate_commands, rehydrate_hooks


class FakePluginService:
    def __init__(self, records, contributions=None):
        self._records = records
        self._all_contributions = contributions or {}

    def list(self, user_id):
        return list(self._records)

    def _contributions(self, user_id, plugin_id):
        return list(self._all_contributions.get(plugin_id, []))


def _package(tmp_path):
    (tmp_path / ".claude-plugin").mkdir(parents=True)
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo", "version": "1.0.0"}), encoding="utf-8"
    )
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "daily.md").write_text("body", encoding="utf-8")
    return tmp_path


def _hooks_package(tmp_path):
    (tmp_path / ".claude-plugin").mkdir(parents=True)
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo", "version": "1.0.0"}), encoding="utf-8"
    )
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"SessionStart": [
        {"matcher": "", "hooks": [{"type": "command", "command": "x"}]}
    ]}}), encoding="utf-8")
    return tmp_path


def test_a_fresh_process_rebuilds_its_command_index_from_active_plugins(tmp_path):
    package = _package(tmp_path / "pkg")
    service = FakePluginService([
        {"plugin_id": "demo", "state": "active", "install_path": str(package), "package_digest": "abc"},
    ])
    library = CommandLibrary()

    rehydrate_commands(service, library, user_id="u1")

    assert library.resolve("u1", "daily").command_id == "demo:daily"


def test_an_inactive_plugin_contributes_nothing_after_rehydration(tmp_path):
    package = _package(tmp_path / "pkg")
    service = FakePluginService([
        {"plugin_id": "demo", "state": "disabled", "install_path": str(package), "package_digest": "abc"},
    ])
    library = CommandLibrary()

    rehydrate_commands(service, library, user_id="u1")

    assert library.resolve("u1", "daily") is None


def test_an_unreadable_package_does_not_break_rehydration(tmp_path):
    service = FakePluginService([
        {"plugin_id": "gone", "state": "active", "install_path": str(tmp_path / "missing"), "package_digest": "abc"},
        {"plugin_id": "demo", "state": "active", "install_path": str(_package(tmp_path / "pkg")), "package_digest": "abc"},
    ])
    library = CommandLibrary()

    rehydrate_commands(service, library, user_id="u1")

    assert library.resolve("u1", "daily") is not None


def test_rehydrate_hooks_registers_active_plugins_with_their_stored_consent(tmp_path):
    package = _hooks_package(tmp_path / "pkg")
    service = FakePluginService(
        [{"plugin_id": "demo", "state": "active", "install_path": str(package), "package_digest": "abc"}],
        contributions={"demo": [{"kind": "hook", "reference": "demo:SessionStart:0", "enabled": True}]},
    )
    engine = HookEngine()

    rehydrate_hooks(service, engine, user_id="u1")

    outcomes = engine.dispatch(user_id="u1", event="SessionStart", payload={})
    assert len(outcomes) == 1
    assert outcomes[0].status == "rejected"  # "x" is not a confinable command; proves the hook WAS registered


def test_rehydrate_hooks_respects_disabled_consent(tmp_path):
    package = _hooks_package(tmp_path / "pkg")
    service = FakePluginService(
        [{"plugin_id": "demo", "state": "active", "install_path": str(package), "package_digest": "abc"}],
        contributions={"demo": [{"kind": "hook", "reference": "demo:SessionStart:0", "enabled": False}]},
    )
    engine = HookEngine()

    rehydrate_hooks(service, engine, user_id="u1")

    assert engine.dispatch(user_id="u1", event="SessionStart", payload={}) == ()


def test_rehydrate_hooks_fails_closed_on_partially_consented_rows(tmp_path):
    """Consent is granted or revoked for every hook row of a plugin at once
    (see PluginService.set_hooks_enabled), so heterogeneous rows should never
    occur in practice — but if they somehow did (a partial write, a manual
    edit), rehydration must not treat that as consent. Mirrors the `all(...)`
    PluginService.set_enabled already uses for the same question."""
    package = _hooks_package(tmp_path / "pkg")
    service = FakePluginService(
        [{"plugin_id": "demo", "state": "active", "install_path": str(package), "package_digest": "abc"}],
        contributions={"demo": [
            {"kind": "hook", "reference": "demo:SessionStart:0", "enabled": True},
            {"kind": "hook", "reference": "demo:SessionStart:1", "enabled": False},
        ]},
    )
    engine = HookEngine()

    rehydrate_hooks(service, engine, user_id="u1")

    assert engine.dispatch(user_id="u1", event="SessionStart", payload={}) == ()


def test_rehydrate_hooks_ignores_inactive_plugins(tmp_path):
    package = _hooks_package(tmp_path / "pkg")
    service = FakePluginService(
        [{"plugin_id": "demo", "state": "disabled", "install_path": str(package), "package_digest": "abc"}],
        contributions={"demo": [{"kind": "hook", "reference": "demo:SessionStart:0", "enabled": True}]},
    )
    engine = HookEngine()

    rehydrate_hooks(service, engine, user_id="u1")

    assert engine.dispatch(user_id="u1", event="SessionStart", payload={}) == ()

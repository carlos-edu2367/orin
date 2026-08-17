import json
from pathlib import Path

from agentos.plugins.command_library import CommandLibrary
from agentos.plugins.rehydrate import rehydrate_commands


class FakePluginService:
    def __init__(self, records):
        self._records = records

    def list(self, user_id):
        return list(self._records)


def _package(tmp_path):
    (tmp_path / ".claude-plugin").mkdir(parents=True)
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo", "version": "1.0.0"}), encoding="utf-8"
    )
    (tmp_path / "commands").mkdir()
    (tmp_path / "commands" / "daily.md").write_text("body", encoding="utf-8")
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

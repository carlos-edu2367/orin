"""W5 review fixes (part 1): regression tests for the findings in docs/superpowers/reviews/2026-10-19-w5-review.md."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine

from agentos.lsp.config import lsp_servers
from agentos.persistence.postgres.schema import metadata
from agentos.plugins.runtime import active_plugins
from agentos.plugins.service import PluginService
from tests.unit.plugins.fakes import FakeMcpService, FakeSkillLibrary

ENCODED = {"py": {
    "command": "powershell", "args": ["-NoProfile", "-EncodedCommand", "ZQBjAGgAbwAgAGgAaQA="],
    "extensionToLanguage": {".py": "python"},
}}


def _service(tmp_path: Path) -> PluginService:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    metadata.create_all(engine)
    return PluginService(engine, plugin_root=tmp_path / "plugins", skill_library=FakeSkillLibrary(), mcp_service=FakeMcpService())


def _package(root: Path, *, lsp: dict = ENCODED, hook: bool = True) -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "helper", "version": "1.0.0"}), encoding="utf-8")
    (root / ".lsp.json").write_text(json.dumps(lsp), encoding="utf-8")
    if hook:
        (root / "hooks").mkdir()
        (root / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": "python ${CLAUDE_PLUGIN_ROOT}/hooks/hello.py"}]}]}}), encoding="utf-8")
        (root / "hooks" / "hello.py").write_text("print('hello')", encoding="utf-8")
    return root


def _started(service: PluginService) -> tuple[list[tuple[str, tuple[str, ...]]], list[str]]:
    plugins, _ = active_plugins(service, "u1", {})
    configs, problems = lsp_servers(plugins, user_id="u1")
    return [(config.command, config.args) for config in configs], problems


# W5-R6: a plugin's language server is shown on the approval card and starts only once the person allowed it.


def test_w5_r6_the_approval_card_lists_the_language_server(tmp_path: Path) -> None:
    card = _service(tmp_path).inspect(user_id="u1", reference=str(_package(tmp_path / "helper")))
    assert card["lsp_servers"] == [{
        "name": "py", "command": "powershell", "args": ["-NoProfile", "-EncodedCommand", "ZQBjAGgAbwAgAGgAaQA="], "extensions": [".py"],
    }]
    assert card["contribution_count"] == 2  # the hook and the language server


def test_w5_r6_a_package_with_only_a_language_server_installs(tmp_path: Path) -> None:
    card = _service(tmp_path).inspect(user_id="u1", reference=str(_package(tmp_path / "pyright", hook=False)))
    assert card["state"] == "pending_approval" and card["contribution_count"] == 1


def test_w5_r6_a_language_server_starts_only_after_the_person_allows_it(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.inspect(user_id="u1", reference=str(_package(tmp_path / "helper")))
    service.approve(user_id="u1", plugin_id="helper")
    assert _started(service)[0] == []  # approved, but its entry is not allowed yet
    service.set_hooks_enabled(user_id="u1", plugin_id="helper", enabled=True)
    assert _started(service)[0] == [("powershell", ("-NoProfile", "-EncodedCommand", "ZQBjAGgAbwAgAGgAaQA="))]


def test_w5_r6_an_edited_language_server_entry_waits_for_consent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    record = service.inspect(user_id="u1", reference=str(_package(tmp_path / "helper")))
    service.approve(user_id="u1", plugin_id="helper")
    service.set_hooks_enabled(user_id="u1", plugin_id="helper", enabled=True)
    assert len(_started(service)[0]) == 1
    changed = {"py": {"command": "cmd", "args": ["/c", "evil.bat"], "extensionToLanguage": {".py": "python"}}}
    (Path(record["install_path"]) / ".lsp.json").write_text(json.dumps(changed), encoding="utf-8")
    assert _started(service)[0] == []


def test_w5_r6_pausing_a_plugin_grants_no_language_server_consent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.inspect(user_id="u1", reference=str(_package(tmp_path / "helper")))
    service.approve(user_id="u1", plugin_id="helper")
    service.set_enabled(user_id="u1", plugin_id="helper", enabled=False)
    service.set_enabled(user_id="u1", plugin_id="helper", enabled=True)
    assert _started(service)[0] == []
    assert [row["enabled"] for row in service._contributions("u1", "helper") if row["kind"] == "lsp"] == [False]


def test_w5_r6_an_unchanged_language_server_keeps_its_consent_across_a_disable(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.inspect(user_id="u1", reference=str(_package(tmp_path / "helper")))
    service.approve(user_id="u1", plugin_id="helper")
    service.set_hooks_enabled(user_id="u1", plugin_id="helper", enabled=True)
    service.set_enabled(user_id="u1", plugin_id="helper", enabled=False)
    service.set_enabled(user_id="u1", plugin_id="helper", enabled=True)
    assert len(_started(service)[0]) == 1


def test_w5_r13_a_closed_manager_starts_no_language_server(tmp_path: Path) -> None:
    """A check still running when the turn closes must not leave a server behind (the review's R13 leftover)."""
    import threading

    from agentos.lsp.config import LspServerConfig
    from agentos.lsp.manager import LspManager

    started: list["_Client"] = []
    entered, release = threading.Event(), threading.Event()

    class _Client:
        def __init__(self, config, *, workspace_root):
            self.closed = False
            started.append(self)

        def start(self):
            entered.set()
            release.wait(5)

        def diagnostics(self, path, text, language, *, timeout):
            return []

        def close(self):
            self.closed = True

    config = LspServerConfig(name="kit:fake", plugin_id="kit", command="fake", extensions=((".txt", "plaintext"),))
    manager = LspManager([config], workspace_root=tmp_path, client_factory=_Client)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    worker = threading.Thread(target=manager.check, args=(tmp_path / "a.txt",))
    worker.start()
    assert entered.wait(5)
    manager.close()  # the turn ends while the server is still starting
    release.set()
    worker.join(5)
    assert [client.closed for client in started] == [True]
    assert manager.check(tmp_path / "a.txt") == ("kit:fake", None, "") and len(started) == 1  # nothing new after close

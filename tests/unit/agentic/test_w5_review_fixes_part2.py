"""W5 review fixes, part 2 (docs/superpowers/reviews/2026-10-19-w5-review.md): the teammate roster (W5-R12) and the
language servers after a turn closed (W5-R13)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import agentos.lsp.config as lsp_config
import agentos.lsp.manager as lsp_manager
from agentos.agentic.tools._shared import ToolOutcome
from tests.unit.agentic.test_disk_skills_session import _session


# W5-R12: two starts of one new teammate name ------------------------------------------------------------------------


def test_two_starts_of_one_new_teammate_name_run_only_one(tmp_path: Path, monkeypatch) -> None:
    session = _session(tmp_path, "hi")
    started: list[str] = []

    def fake_start(subagent_type, task, **kwargs):
        started.append(f"{kwargs.get('teammate_name')}:{task}")
        time.sleep(0.3)  # the real start records the roster entry only after it builds the record
        return ToolOutcome("succeeded", "started", "started", {})

    monkeypatch.setattr(session, "_start_subagent", fake_start)
    barrier = threading.Barrier(2)
    outcomes: dict[str, ToolOutcome] = {}

    def call(task: str) -> None:
        barrier.wait()
        outcomes[task] = session._run_subagent("general-purpose", task, teammate_name="reviewer", team_name="t")

    threads = [threading.Thread(target=call, args=(task,)) for task in ("review A", "review B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(started) == 1, started
    refused = [outcome for outcome in outcomes.values() if outcome.status != "succeeded"]
    assert len(refused) == 1 and refused[0].error_code == "SUBAGENT_STILL_RUNNING"


def test_a_start_that_fails_releases_the_new_name(tmp_path: Path) -> None:
    session = _session(tmp_path, "hi")
    outcome = session._run_subagent("no-such-agent-type", "task", teammate_name="reviewer", team_name="t")
    assert outcome.status != "succeeded"
    assert "reviewer" not in session._team_roster()
    assert session._teammate_refusal("reviewer", "t") is None  # the name can be used again


def test_a_message_to_a_name_being_started_is_queued(tmp_path: Path) -> None:
    session = _session(tmp_path, "hi")
    assert session._teammate_refusal("reviewer", "t") is None  # the start has claimed the name
    outcome = session._send_team_message(sender="lead", to="reviewer", message="check the tests")
    assert outcome.status == "succeeded"
    assert outcome.payload.get("status") == "queued"


# W5-R13: language servers after the turn closed ---------------------------------------------------------------------


class _FakeManager:
    created: list["_FakeManager"] = []

    def __init__(self, configs, *, workspace_root):
        self.closed = False
        self.events: list[str] = []
        _FakeManager.created.append(self)

    def server_for(self, path):
        return object()

    def check(self, path):
        time.sleep(0.2)  # a check in flight when the turn closes
        self.events.append("check finished")
        return "fake", [], ""

    def close(self):
        self.closed = True
        self.events.append("closed")


def _lsp_session(tmp_path: Path, monkeypatch):
    _FakeManager.created = []
    monkeypatch.setattr(lsp_config, "lsp_servers", lambda plugins, user_id: (["one config"], []))
    monkeypatch.setattr(lsp_manager, "LspManager", _FakeManager)
    session = _session(tmp_path, "hi")
    session.plugin_hooks = True
    session._active_plugins = ()
    session.workspace.root.mkdir(parents=True, exist_ok=True)
    return session


def test_a_write_after_the_turn_closed_starts_no_language_server(tmp_path: Path, monkeypatch) -> None:
    session = _lsp_session(tmp_path, monkeypatch)
    session._lsp_after_write("main.py")
    session.close_background_subagents()
    session._lsp_after_write("late.py")
    time.sleep(0.4)
    assert len(_FakeManager.created) == 1, len(_FakeManager.created)
    assert _FakeManager.created[0].closed


def test_closing_waits_briefly_for_a_check_already_running(tmp_path: Path, monkeypatch) -> None:
    session = _lsp_session(tmp_path, monkeypatch)
    session._lsp_after_write("main.py")
    time.sleep(0.05)  # the check has started on the worker thread
    session.close_background_subagents()
    assert _FakeManager.created[0].events == ["check finished", "closed"]

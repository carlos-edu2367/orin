"""W5 review fixes, part 2 (docs/superpowers/reviews/2026-10-19-w5-review.md): the teammate roster (W5-R12)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

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

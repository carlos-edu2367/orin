"""W6-R2: a CLI turn's lease is dropped when the turn closes, however it closes."""
from __future__ import annotations

import pytest

from agentos.agentic import cli_lease
from agentos.workers.chat import ChatWorker


def _worker(monkeypatch, error: BaseException | None) -> None:
    def body(self, turn):
        if error is not None:
            raise error

    monkeypatch.setattr(ChatWorker, "_run_claimed_turn", body)


def test_the_lease_is_dropped_when_a_cli_turn_closes(monkeypatch) -> None:
    cli_lease.renew("turn_closes")
    _worker(monkeypatch, None)
    ChatWorker._run_claimed(object.__new__(ChatWorker), {"turn_id": "turn_closes", "cli_options": "{}"})
    assert cli_lease.expired("turn_closes") is True


def test_the_lease_is_dropped_when_a_cli_turn_fails(monkeypatch) -> None:
    cli_lease.renew("turn_fails")
    _worker(monkeypatch, RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        ChatWorker._run_claimed(object.__new__(ChatWorker), {"turn_id": "turn_fails", "cli_options": "{}"})
    assert cli_lease.expired("turn_fails") is True


def test_an_interface_turn_has_no_lease_to_drop(monkeypatch) -> None:
    _worker(monkeypatch, None)
    ChatWorker._run_claimed(object.__new__(ChatWorker), {"turn_id": "turn_plain", "cli_options": None})

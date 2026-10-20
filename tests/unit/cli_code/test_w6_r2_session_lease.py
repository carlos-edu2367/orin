"""W6-R2: a CLI turn stops when its `orin code` lease expires; an interface turn is unaffected."""
from __future__ import annotations

import os
import time
from pathlib import Path

from agentos.agentic import cli_lease
from agentos.code_mode.cli_options import CliTurnOptions

from tests.unit.cli_code.test_cli_turn_session import _Agents, _session, _Store
from agentos.agentic.session import TurnSession


def _cli_session(tmp_path: Path, turn_id: str = "t") -> TurnSession:
    store = _Store()
    store.turn["turn_id"] = turn_id
    return TurnSession(
        turn={**store.turn, "code_mode": "code", "cli_options": CliTurnOptions().dumps()}, store=store, agents_store=_Agents(),
        memory_store=None, provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False,
    )


def _age(turn_id: str, seconds: float) -> None:
    path = cli_lease.leases_root() / turn_id
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_a_cli_turn_with_a_fresh_lease_is_not_cancelled(tmp_path: Path) -> None:
    cli_lease.renew("t")
    session = _cli_session(tmp_path)
    assert session.cancelled(session.turn) is False


def test_a_cli_turn_with_an_old_lease_is_cancelled_and_says_so_once(tmp_path: Path) -> None:
    cli_lease.renew("t")
    _age("t", cli_lease.LEASE_SECONDS + 5)
    session = _cli_session(tmp_path)
    recorded: list[tuple] = []
    session._record = lambda event_type, summary, payload=None, **kw: recorded.append((event_type, summary, payload))
    assert session.cancelled(session.turn) is True
    assert session.cancelled(session.turn) is True
    assert len(recorded) == 1
    assert "stopped following this turn" in recorded[0][1]


def test_a_cli_turn_whose_lease_is_missing_is_cancelled(tmp_path: Path) -> None:
    session = _cli_session(tmp_path, turn_id="t_no_lease")
    assert session.cancelled(session.turn) is True


def test_an_interface_turn_is_not_cancelled_by_a_lease(tmp_path: Path) -> None:
    session = _session(tmp_path)
    assert session.cancelled(session.turn) is False


def test_an_explicit_cancel_still_cancels_a_cli_turn(tmp_path: Path) -> None:
    cli_lease.renew("t")
    store = _Store()
    session = TurnSession(
        turn={**store.turn, "code_mode": "code", "cli_options": CliTurnOptions().dumps()}, store=store, agents_store=_Agents(),
        memory_store=None, provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False,
        cancelled=lambda turn: True,
    )
    assert session.cancelled(session.turn) is True

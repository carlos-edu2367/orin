"""W6 Batch F: a turn's SDK relay is removed when the turn closes (the registry must not keep sessions alive)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agentos.code_mode.cli_options import CliTurnOptions
from agentos.mcp.transport_sdk import relay_for

from tests.unit.cli_code.test_cli_turn_session import _session


def test_the_relay_lives_as_long_as_the_turn(tmp_path: Path) -> None:
    options = CliTurnOptions(mcp_servers={"calc": {"type": "sdk", "name": "calc"}})
    session = _session(tmp_path, options)
    session.mcp_service = SimpleNamespace(allow_stdio=True)  # a desktop Orin reads the person's servers
    session._mcp_file_connect = lambda config, secrets: ("ok", ())  # no discovery: only the registration matters
    runtime = session.build_runtime()
    key = str(session.turn["turn_id"])
    assert relay_for(key) is not None
    runtime.close()
    assert relay_for(key) is None

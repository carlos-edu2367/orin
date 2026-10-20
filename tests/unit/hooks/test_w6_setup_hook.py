"""W6: `orin code --init` / `--maintenance` fire the Setup hooks (matched on their trigger) before SessionStart."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentos.agentic.lifecycle import HookEvent
from agentos.cli_code.args import CliUsageError, parse_code_arguments
from agentos.cli_code.headless import refuse_unsupported
from agentos.cli_code.turn_options import build_turn_options
from agentos.code_mode.cli_options import CliTurnOptions
from agentos.hooks.matching import matcher_applies

from tests.unit.cli_code.test_cli_turn_session import _session
from tests.unit.hooks.test_session_hooks_w2 import _user


def test_setup_matches_on_its_trigger() -> None:
    assert matcher_applies(HookEvent.SETUP, "init", {"trigger": "init"})
    assert not matcher_applies(HookEvent.SETUP, "maintenance", {"trigger": "init"})


def parse_code_args(argv):
    return parse_code_arguments(argv)[0]


def test_only_the_first_turn_carries_the_trigger(tmp_path: Path) -> None:
    options = parse_code_args(["-p", "hi", "--init"])
    refuse_unsupported(options)  # no longer refused
    assert build_turn_options(options, tmp_path, host_answers_prompts=False, first_turn=True).setup == "init"
    assert build_turn_options(options, tmp_path, host_answers_prompts=False).setup is None
    maintenance = parse_code_args(["-p", "hi", "--maintenance"])
    assert build_turn_options(maintenance, tmp_path, host_answers_prompts=False, first_turn=True).setup == "maintenance"
    with pytest.raises(CliUsageError):
        refuse_unsupported(parse_code_args(["-p", "hi", "--init", "--maintenance"]))


def test_a_setup_hook_adds_its_output_to_the_turn(tmp_path: Path) -> None:
    script = tmp_path / "setup.py"
    script.write_text("import json, sys; d = json.load(sys.stdin); print('prepared by ' + d['trigger'])", encoding="utf-8")
    _user({"hooks": {"Setup": [{"matcher": "init", "hooks": [{"type": "command", "command": f'"{sys.executable}" "{script}"'}]}]}})
    runtime = _session(tmp_path, CliTurnOptions(setup="init")).build_runtime()
    assert "prepared by init" in runtime.system_prompt + runtime.volatile_prompt
    plain = _session(tmp_path / "other", CliTurnOptions()).build_runtime()
    assert "prepared by" not in plain.system_prompt + plain.volatile_prompt

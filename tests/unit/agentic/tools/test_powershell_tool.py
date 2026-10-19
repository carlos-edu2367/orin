"""W4: the PowerShell tool runs Windows PowerShell through the Bash runner."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.runtime import CODE_MODE_CORE_TOOLS
from agentos.agentic.tools.shell.bash import BashOutput
from agentos.agentic.tools.shell.powershell import powershell_executable
from agentos.agentic.workspace import ConversationWorkspace

windows_only = pytest.mark.skipif(os.name != "nt" or powershell_executable() is None, reason="Windows PowerShell only")


def _toolset(tmp_path: Path, **options) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path / "w", "c1"), **options)


def test_the_tool_exists_only_where_it_can_run(tmp_path: Path) -> None:
    names = {item.name for item in _toolset(tmp_path).definitions()}
    assert ("powershell" in names) == (os.name == "nt" and powershell_executable() is not None)
    assert "powershell" not in {item.name for item in _toolset(tmp_path, enable_terminal=False).definitions()}
    assert "powershell" in CODE_MODE_CORE_TOOLS


@windows_only
def test_it_runs_and_reports_utf8_output(tmp_path: Path) -> None:
    outcome = _toolset(tmp_path).invoke("PowerShell", {"command": "Write-Output 'olá'"})
    assert outcome.status == "succeeded", outcome.content
    output = outcome.data["tool_output"]
    BashOutput.model_validate(output)
    assert output["stdout"].strip() == "olá" and output["exitCode"] == 0


@windows_only
def test_a_read_only_subagent_cannot_run_it(tmp_path: Path) -> None:
    outcome = _toolset(tmp_path, read_only_mode=True).invoke("PowerShell", {"command": "Write-Output hi"})
    assert outcome.status == "failed" and outcome.error_code in {"READ_ONLY_SUBAGENT", "UNKNOWN_TOOL"}


@windows_only
def test_code_mode_asks_before_running_it(tmp_path: Path) -> None:
    outcome = _toolset(tmp_path, code_mode_active=True, permission_mode="ask_permissions", non_interactive_ask="deny").invoke(
        "PowerShell", {"command": "Write-Output hi"},
    )
    assert outcome.status == "failed" and outcome.error_code == "CODE_PERMISSION_DENIED"


def test_the_model_cannot_pass_a_raw_argv(tmp_path: Path) -> None:
    outcome = _toolset(tmp_path).invoke("Bash", {"command": "echo hi", "argv": ["cmd", "/c", "echo pwned"]})
    assert outcome.status == "failed" and outcome.error_code == "INVALID_ARGUMENTS"


from agentos.agentic.tools.shell.powershell import decode_clixml


def test_clixml_error_records_are_decoded() -> None:
    raw = (
        '#< CLIXML\r\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
        '<S S="Error">boom_x000D__x000A_</S><S S="Error">second &amp; last_x000D__x000A_</S></Objs>'
    )
    assert decode_clixml(raw) == "boom\r\nsecond & last"
    assert decode_clixml("plain error") == "plain error"
    assert decode_clixml('#< CLIXML\r\n<Objs><Obj S="progress" RefId="0"></Obj></Objs>') == ""


@windows_only
def test_errors_reach_the_model_as_text(tmp_path: Path) -> None:
    outcome = _toolset(tmp_path).invoke("PowerShell", {"command": "Write-Error 'boom'"})
    stderr = outcome.data["tool_output"]["stderr"]
    assert "boom" in stderr and "CLIXML" not in stderr and "<Objs" not in stderr

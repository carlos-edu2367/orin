import sys
import time
from pathlib import Path

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.tools._shared import AgentToolError
from agentos.agentic.tools.orin import verify as verify_module
from agentos.agentic.workspace import ConversationWorkspace


def test_a_verification_step_reports_pass_and_output(tmp_path: Path) -> None:
    passed, output = verify_module._run_verification_step(
        f'"{sys.executable}" -c "print(\'ok\')"', cwd=tmp_path, timeout=60, cancelled=lambda: False,
    )
    assert passed is True and "ok" in output


def test_a_verification_step_stops_when_the_turn_is_cancelled(tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(AgentToolError) as caught:
        verify_module._run_verification_step(
            f'"{sys.executable}" -c "import time; time.sleep(30)"', cwd=tmp_path, timeout=60, cancelled=lambda: True,
        )
    assert caught.value.error_code == "INTERRUPTED_BY_USER"
    assert time.monotonic() - started < 10


def test_verify_project_passes_the_turn_cancel_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    def fake_step(command, *, cwd, timeout, cancelled):
        seen.append(cancelled())
        return True, "ok"

    monkeypatch.setattr(verify_module, "_run_verification_step", fake_step)
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), cancel_check=lambda: True)
    (toolset.workspace.root / "pyproject.toml").write_text("[project]\nname = \"demo\"\n\n[tool.ruff]\n", encoding="utf-8")
    toolset.invoke("verify_project", {"steps": ["lint"]})
    assert seen == [True]

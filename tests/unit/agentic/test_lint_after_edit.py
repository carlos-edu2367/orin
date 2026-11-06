from __future__ import annotations

import json
from pathlib import Path
import pytest

from agentos.agentic import agent_tools
from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace


@pytest.fixture()
def workspace(tmp_path: Path) -> ConversationWorkspace:
    return ConversationWorkspace(tmp_path, "chat_lint")


@pytest.fixture()
def toolset(workspace: ConversationWorkspace) -> AgentToolset:
    return AgentToolset(workspace)


RUFF_F401_ONLY = {
    "code": "F401",
    "message": "`os` imported but unused",
    "location": {"row": 1, "column": 8},
}

RUFF = json.dumps(
    [
        RUFF_F401_ONLY,
        {
            "code": "E501",
            "message": "Line too long",
            "location": {"row": 40, "column": 89},
        },
    ]
)


def _fake_run(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> None:
    monkeypatch.setattr(
        agent_tools.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "R", (), {"returncode": returncode, "stdout": stdout, "stderr": stderr}
        )(),
    )


def test_only_problems_on_changed_lines_are_reported(
    toolset: AgentToolset,
    workspace: ConversationWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace.write_text("app.py", "".join(f"x{i} = {i}\n" for i in range(1, 51)))
    toolset.invoke("read_file", {"path": "app.py"})
    _fake_run(monkeypatch, RUFF, returncode=1)
    outcome = toolset.invoke(
        "edit_file",
        {"path": "app.py", "old_text": "x40 = 40", "new_text": "x40 = 'a' * 200"},
    )
    assert "[lint — 1 problema(s) nas linhas alteradas, +1 fora delas]" in outcome.content
    assert "40:89 E501" in outcome.content and "F401" not in outcome.content


def test_clean_or_out_of_range_lint_adds_nothing(
    toolset: AgentToolset,
    workspace: ConversationWorkspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace.write_text("app.py", "".join(f"x{i} = {i}\n" for i in range(1, 51)))
    toolset.invoke("read_file", {"path": "app.py"})
    _fake_run(monkeypatch, json.dumps([RUFF_F401_ONLY]), returncode=1)
    outcome = toolset.invoke(
        "edit_file", {"path": "app.py", "old_text": "x40 = 40", "new_text": "x40 = 41"}
    )
    assert "lint" not in outcome.content and "diagnóstico" not in outcome.content


def test_missing_linter_adds_nothing(
    toolset: AgentToolset, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run(monkeypatch, "", returncode=1, stderr="No module named ruff")
    outcome = toolset.invoke("write_file", {"path": "new.py", "content": "import os\n"})
    assert "lint" not in outcome.content and "No module named" not in outcome.content


def test_new_file_counts_every_line_as_changed(
    toolset: AgentToolset, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run(monkeypatch, json.dumps([RUFF_F401_ONLY]), returncode=1)
    outcome = toolset.invoke("write_file", {"path": "new.py", "content": "import os\n"})
    assert "1:8 F401" in outcome.content


def test_eslint_json_and_summary_truncation(
    toolset: AgentToolset, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentos.agentic.diagnostics import parse_file_diagnostics, summarize_diagnostics

    eslint_out = json.dumps(
        [
            {
                "messages": [
                    {
                        "line": i if i <= 6 else 20,
                        "column": 2,
                        "ruleId": f"rule-{i}",
                        "message": "x" * 120,
                    }
                    for i in range(1, 8)
                ]
            }
        ]
    )
    diags = parse_file_diagnostics("eslint_json", eslint_out)
    assert len(diags) == 7
    summary = summarize_diagnostics(diags, [(1, 6)], max_items=5)
    assert summary is not None
    assert "[lint — 6 problema(s) nas linhas alteradas, +1 fora delas]" in summary
    assert len(summary) <= 600


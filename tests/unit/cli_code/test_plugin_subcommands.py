"""W6 Task 21: `orin code plugin validate` (no running Orin needed)."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from agentos.cli_code.main import main


def _run(argv: list[str], cwd: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, str, str]:
    monkeypatch.chdir(cwd)
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdin=io.StringIO(""), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()



def test_plugin_validate_reads_a_folder_without_installing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = tmp_path / "demo"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "demo", "version": "1.0.0", "description": "d"}), encoding="utf-8")
    code, out, err = _run(["plugin", "validate", "demo"], tmp_path, monkeypatch)
    assert code == 0, err
    assert out.startswith("The plugin is valid.")
    assert _run(["plugin", "validate", "missing"], tmp_path, monkeypatch)[0] == 1
    assert _run(["plugin", "update", "x"], tmp_path, monkeypatch)[0] == 1

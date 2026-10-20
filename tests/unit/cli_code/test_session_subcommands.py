"""W6 Task 20: `orin code agents`, `auto-mode`, background runs and `purge`."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from agentos.cli_code.main import main
from agentos.settings.paths import orin_user_dir


def _run(argv: list[str], cwd: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, str, str]:
    monkeypatch.chdir(cwd)
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdin=io.StringIO(""), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()



def test_agents_lists_the_folders_agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = orin_user_dir() / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "reviewer.md").write_text("---\nname: reviewer\ndescription: Reviews diffs\n---\nBe strict.\n", encoding="utf-8")
    code, out, _ = _run(["agents", "--json"], tmp_path, monkeypatch)
    listed = json.loads(out)
    assert code == 0 and {"name": "reviewer", "origin": "user"}.items() <= next(item for item in listed["agents"] if item["name"] == "reviewer").items()
    code, out, _ = _run(["agents"], tmp_path, monkeypatch)
    assert "reviewer [user]: Reviews diffs" in out


def test_auto_mode_defaults_config_and_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, out, _ = _run(["auto-mode", "defaults"], tmp_path, monkeypatch)
    assert code == 0 and json.loads(out)["hard_deny_patterns"]
    settings = orin_user_dir() / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"autoMode": {"allow": ["tests"]}, "model": "ollama/x"}), encoding="utf-8")
    assert json.loads(_run(["auto-mode", "config"], tmp_path, monkeypatch)[1])["autoMode"] == {"allow": ["tests"]}
    assert _run(["auto-mode", "reset"], tmp_path, monkeypatch)[0] == 0
    assert json.loads(settings.read_text(encoding="utf-8")) == {"model": "ollama/x"}


def test_background_bookkeeping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.cli_code.commands.background import background_root

    root = background_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "bg_done.log").write_bytes(b"all done\n")
    (root / "bg_done.json").write_text(json.dumps({"id": "bg_done", "pid": 0, "cwd": str(tmp_path), "args": [], "log": str(root / "bg_done.log")}), encoding="utf-8")
    assert _run(["logs", "bg_done"], tmp_path, monkeypatch)[1] == "all done\n"
    assert "had already finished" in _run(["stop", "bg_done"], tmp_path, monkeypatch)[1]
    assert json.loads(_run(["agents", "--json"], tmp_path, monkeypatch)[1])["background"][0]["state"] == "finished"
    assert _run(["rm", "bg_done"], tmp_path, monkeypatch)[0] == 0 and not (root / "bg_done.json").exists()
    assert _run(["logs", "bg_done"], tmp_path, monkeypatch)[0] == 1
    assert _run(["logs", "../escape"], tmp_path, monkeypatch)[0] == 1


def test_purge_needs_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.installation import orin_paths

    index = orin_paths().data / "cli-sessions.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("{}", encoding="utf-8")
    assert _run(["purge"], tmp_path, monkeypatch)[0] == 1 and index.exists()
    assert _run(["purge", "--yes"], tmp_path, monkeypatch)[0] == 0 and not index.exists()


@pytest.mark.parametrize("name", ["import", "review"])
def test_subcommands_without_support_say_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    code, _, err = _run([name, "claude"], tmp_path, monkeypatch)
    assert code == 1 and "not supported yet" in err or "W8" in err


def test_bg_needs_a_print_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, _, err = _run(["--bg", "--input-format", "stream-json", "--output-format", "stream-json"], tmp_path, monkeypatch)
    assert code == 1 and "--bg" in err

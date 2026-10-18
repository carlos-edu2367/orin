from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path

from agentos.hooks import session_end
from agentos.settings.paths import orin_user_dir


def _hook_settings(command: str) -> None:
    path = orin_user_dir() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": command, "timeout": 30}]}]}}), encoding="utf-8")


def test_session_end_runs_the_hooks_of_every_session(tmp_path: Path) -> None:
    session_end.clear_registry()  # other tests register sessions through build_runtime
    marker = tmp_path / "ended.json"
    script = tmp_path / "hook.py"
    script.write_text(textwrap.dedent(f"""
        import json, sys
        data = json.loads(sys.stdin.read())
        with open(r"{marker}", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({{"reason": data.get("reason"), "session": data.get("session_id")}}) + "\\n")
    """), encoding="utf-8")
    _hook_settings(f'"{sys.executable}" "{script}"')
    workspace = tmp_path / "w"
    workspace.mkdir()
    session_end.register_session("conv-a", workspace_root=workspace, user_id="u", trusted=False)
    session_end.register_session("conv-b", workspace_root=workspace, user_id="u", trusted=False)
    assert session_end.end_sessions(timeout_seconds=30) == 2
    lines = [json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()]
    assert sorted(item["session"] for item in lines) == ["conv-a", "conv-b"] and {item["reason"] for item in lines} == {"other"}
    assert session_end.end_sessions() == 0


def test_shutdown_never_waits_longer_than_its_budget(tmp_path: Path) -> None:
    session_end.clear_registry()
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(20)\n", encoding="utf-8")
    _hook_settings(f'"{sys.executable}" "{script}"')
    workspace = tmp_path / "w"
    workspace.mkdir()
    session_end.register_session("conv-slow", workspace_root=workspace, user_id="u", trusted=False)
    started = time.monotonic()
    session_end.end_sessions(timeout_seconds=1.0)
    assert time.monotonic() - started < 5


def test_without_hooks_nothing_runs(tmp_path: Path) -> None:
    session_end.clear_registry()
    session_end.register_session("conv-none", workspace_root=tmp_path, user_id="u", trusted=False)
    assert session_end.end_sessions() == 0

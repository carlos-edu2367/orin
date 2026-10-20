"""W6-R4: `orin code` refuses bypass mode under a policy that disables it, and set_permission_mode obeys the same rules."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from agentos.cli_code.args import CliUsageError, CodeOptions
from agentos.cli_code.headless import HeadlessRun
from agentos.cli_code.main import main
from agentos.settings.paths import orin_managed_dir


class _Client:
    def __init__(self) -> None:
        self.posts: list[str] = []

    def post(self, path, body=None, *, timeout=None):
        self.posts.append(path)
        return {"conversation_id": "chat_1", "turn_id": "turn_1"}

    def get(self, path, **params):
        return {}

    def close(self) -> None:
        pass


def _disable_bypass_by_policy() -> None:
    orin_managed_dir().mkdir(parents=True, exist_ok=True)
    (orin_managed_dir() / "managed-settings.json").write_text(
        json.dumps({"permissions": {"disableBypassPermissionsMode": "disable"}}), encoding="utf-8")


def _run(options: CodeOptions, tmp_path: Path, client: _Client | None = None) -> tuple[HeadlessRun, io.StringIO, _Client]:
    client = client or _Client()
    stdout, stderr = io.StringIO(), io.StringIO()
    run = HeadlessRun(options, client=client, data_dir=tmp_path, cwd=tmp_path, stdin=io.StringIO(""),
                      stdout=stdout, stderr=stderr)
    return run, stdout, client


@pytest.mark.parametrize("flags", [
    {"dangerously_skip_permissions": True},
    {"permission_mode": "bypassPermissions"},
    {"permission_mode": "full_autonomy"},
], ids=["dangerously-skip", "permission-mode-bypass", "permission-mode-full-autonomy"])
def test_a_run_asking_for_bypass_exits_under_policy(tmp_path: Path, flags: dict) -> None:
    _disable_bypass_by_policy()
    run, _stdout, client = _run(CodeOptions(prompt="hi", **flags), tmp_path)
    with pytest.raises(CliUsageError) as refused:
        run.run()
    assert str(refused.value) == "bypass mode is disabled by policy (permissions.disableBypassPermissionsMode)"
    assert client.posts == []  # nothing was sent: no turn, no file


def test_the_cli_reports_the_refusal_as_exit_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _disable_bypass_by_policy()
    client = _Client()
    import agentos.cli_code.runtime as runtime

    monkeypatch.setattr(runtime, "connect_local", lambda paths: client)
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(["-p", "hi", "--dangerously-skip-permissions"], stdin=io.StringIO(""), stdout=stdout, stderr=stderr)
    assert code == 1
    assert stderr.getvalue() == "Error: bypass mode is disabled by policy (permissions.disableBypassPermissionsMode)\n"
    assert client.posts == []


def test_a_run_without_policy_is_not_refused_at_the_start(tmp_path: Path) -> None:
    run, _stdout, _client = _run(CodeOptions(prompt="hi", dangerously_skip_permissions=True), tmp_path)
    run._resolve_session = lambda: (_ for _ in ()).throw(RuntimeError("reached the session"))
    with pytest.raises(RuntimeError, match="reached the session"):
        run.run()


def _control(run: HeadlessRun, mode: str) -> dict:
    run.bridge.handle({"type": "control_request", "request_id": "q1",
                       "request": {"subtype": "set_permission_mode", "mode": mode}})
    line = run.out._stdout.getvalue().strip().splitlines()[-1]
    return json.loads(line)["response"]


def _start(tmp_path: Path, *, allow: bool = False, start_bypass: bool = False, policy: bool = False):
    options = CodeOptions(prompt="hi", allow_dangerously_skip_permissions=allow, dangerously_skip_permissions=start_bypass)
    run, _stdout, _client = _run(options, tmp_path)
    run.bypass_policy = policy  # what run() reads from the folder's settings before the first turn
    return run


def test_set_permission_mode_bypass_is_refused_without_the_allow_flag(tmp_path: Path) -> None:
    run = _start(tmp_path, policy=False)
    response = _control(run, "bypassPermissions")
    assert response["subtype"] == "error"
    assert run.options.permission_mode is None and run.options.dangerously_skip_permissions is False


def test_set_permission_mode_bypass_is_accepted_with_the_allow_flag(tmp_path: Path) -> None:
    run = _start(tmp_path, allow=True, policy=False)
    response = _control(run, "bypassPermissions")
    assert response["subtype"] == "success"
    assert run.options.permission_mode == "bypassPermissions"


def test_set_permission_mode_bypass_is_refused_under_policy_even_with_the_allow_flag(tmp_path: Path) -> None:
    run = _start(tmp_path, allow=True, policy=True)
    response = _control(run, "bypassPermissions")
    assert response["subtype"] == "error"
    assert "disabled by policy" in response["error"]
    assert run.options.permission_mode is None


def test_the_start_flag_keeps_bypass_available_after_a_switch_away(tmp_path: Path) -> None:
    run = _start(tmp_path, start_bypass=True, policy=False)
    assert _control(run, "default")["subtype"] == "success"
    assert _control(run, "bypassPermissions")["subtype"] == "success"


def test_allow_flag_does_not_change_the_starting_mode(tmp_path: Path) -> None:
    run = _start(tmp_path, allow=True, policy=False)
    assert run.options.dangerously_skip_permissions is False
    assert run.options.permission_mode is None

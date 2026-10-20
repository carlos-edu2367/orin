"""W6 Task 2: the per-run API token `orin code` authenticates with (spec §9.1)."""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path, PureWindowsPath

import pytest
from fastapi.testclient import TestClient

from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticationError, LoopbackSecurityService
from agentos.installation.api_token import issue_api_token, read_api_token, revoke_api_token, token_path


def test_a_token_is_issued_read_and_revoked(tmp_path: Path) -> None:
    first = issue_api_token(tmp_path)
    assert len(first) >= 40 and read_api_token(tmp_path) == first
    second = issue_api_token(tmp_path)
    assert second != first and read_api_token(tmp_path) == second
    assert [item.name for item in tmp_path.iterdir()] == ["api-token"]  # no temporary file left behind
    revoke_api_token(tmp_path)
    assert read_api_token(tmp_path) is None
    revoke_api_token(tmp_path)  # idempotent


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_token_file_is_owner_only_on_posix(tmp_path: Path) -> None:
    issue_api_token(tmp_path)
    assert stat.S_IMODE(token_path(tmp_path).stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL")
def test_the_token_file_is_owner_only_on_windows(tmp_path: Path) -> None:
    issue_api_token(tmp_path)
    listing = subprocess.run(["icacls", str(token_path(tmp_path))], capture_output=True, text=True, check=True).stdout
    entries = [line.strip() for line in listing.splitlines()[:-1] if ":(" in line]
    assert entries, listing
    assert all("(I)" not in entry for entry in entries), listing  # nothing inherited from the folder
    assert len(entries) == 1 and os.environ["USERNAME"].lower() in entries[0].lower(), listing


def test_a_failed_restriction_leaves_no_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.installation import api_token

    def refuse(path: Path) -> None:
        raise api_token.ApiTokenError("no")

    monkeypatch.setattr(api_token, "restrict_to_owner", refuse)
    with pytest.raises(api_token.ApiTokenError):
        issue_api_token(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_loopback_accepts_only_the_current_token() -> None:
    current = {"token": "run-token-1"}
    security = LoopbackSecurityService(api_token=lambda: current["token"])
    assert security.authenticate(bearer_token=None, session_id=None).credential_kind == "loopback"
    cli = security.authenticate(bearer_token="run-token-1", session_id=None)
    assert cli.credential_kind == "cli_token" and cli.user_id == "local-user"
    for wrong in ("run-token-2", "run-token-1 ", "ﬀ"):
        with pytest.raises(AuthenticationError):
            security.authenticate(bearer_token=wrong, session_id=None)
    current["token"] = None  # the runtime issued no token: every bearer is refused
    with pytest.raises(AuthenticationError):
        security.authenticate(bearer_token="run-token-1", session_id=None)
    with pytest.raises(AuthenticationError):
        security.authenticate(bearer_token=None, session_id="sid")


def test_the_gateway_still_requires_a_loopback_peer_for_the_token() -> None:
    app = create_app(ApiServices(security=LoopbackSecurityService(api_token=lambda: "t")))
    remote = TestClient(app, client=("192.0.2.9", 40000)).get("/v1/conversations", headers={"Authorization": "Bearer t"})
    assert remote.status_code == 401


def _supervisor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import io

    from agentos.installation.paths import OrinPaths
    from agentos.installation.profile import RuntimeProfile
    from agentos.launcher import supervisor as module
    from agentos.launcher.environment import RuntimeEnvironment
    from agentos.launcher.ports import PortChoice
    from agentos.launcher.ui import Console

    paths = OrinPaths(tmp_path / "config", tmp_path / "data", tmp_path / "logs", tmp_path / "cache", tmp_path / "run").ensure()
    monkeypatch.setattr(module, "load_environment", lambda *_: RuntimeEnvironment({"DATABASE_URL": "sqlite://"}, (), None))
    monkeypatch.setattr(module, "select_port", lambda *_, **__: PortChoice(48123, 48123, False))
    profile = RuntimeProfile("installed", tmp_path / "install", "9.9.9", None)
    return module.Supervisor(paths=paths, profile=profile, console=Console(io.StringIO(), colour=False)), paths


def test_the_supervisor_issues_a_fresh_token_each_run_and_removes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, paths = _supervisor(tmp_path, monkeypatch)
    supervisor._prepare()
    first = read_api_token(paths.run)
    assert first
    supervisor.shutdown()
    assert read_api_token(paths.run) is None
    supervisor._prepare()
    second = read_api_token(paths.run)
    assert second and second != first
    supervisor._rollback()  # a failed startup leaves no token behind either
    assert read_api_token(paths.run) is None


def test_a_token_that_cannot_be_restricted_does_not_stop_the_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.installation import api_token
    from agentos.launcher import supervisor as module

    supervisor, paths = _supervisor(tmp_path, monkeypatch)

    def refuse(run: Path) -> str:
        raise api_token.ApiTokenError("no ACL")

    monkeypatch.setattr(module, "issue_api_token", refuse)
    supervisor._prepare()  # no exception: the interface does not need the token
    assert read_api_token(paths.run) is None


def test_windows_tools_run_from_system32_and_grant_by_sid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A planted icacls.exe in the current folder or on PATH must never run; a non-ASCII account name must not matter.
    from agentos.installation import api_token

    calls: list[list[str]] = []

    class Done:
        def __init__(self, stdout: str) -> None:
            self.returncode, self.stdout, self.stderr = 0, stdout, ""

    def fake_run(command: list[str], **_: object) -> Done:
        calls.append(command)
        return Done('"desktop\\joão","S-1-5-21-1-2-3-1001"\r\n' if command[0].lower().endswith("whoami.exe") else "ok")

    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    monkeypatch.setattr(api_token.subprocess, "run", fake_run)
    api_token._windows_owner_only(tmp_path / "f")
    assert PureWindowsPath(calls[0][0]) == PureWindowsPath("C:/Windows/System32/whoami.exe")
    assert PureWindowsPath(calls[1][0]) == PureWindowsPath("C:/Windows/System32/icacls.exe")
    assert calls[1][-1] == "*S-1-5-21-1-2-3-1001:(F)" and "/inheritance:r" in calls[1]


def test_an_unknown_windows_account_issues_no_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.installation import api_token

    class Failed:
        returncode, stdout, stderr = 1, "", "denied"

    monkeypatch.setattr(api_token.subprocess, "run", lambda *_, **__: Failed())
    with pytest.raises(api_token.ApiTokenError):
        api_token._windows_owner_only(tmp_path / "f")

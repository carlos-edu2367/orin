from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from agentos.api import (
    ApiServices,
    AuthenticatedPrincipal,
    InMemorySecurityService,
    create_app,
)
from agentos.providers.antigravity_cli import command as antigravity_command

AUTH = {"Authorization": "Bearer pat-test"}


def _client() -> TestClient:
    security = InMemorySecurityService()
    security.add_pat(
        "pat-test",
        AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})),
    )
    app = create_app(ApiServices(security=security))
    return TestClient(app)


def test_resolve_antigravity_home_respects_env_and_defaults(tmp_path: Path) -> None:
    custom = tmp_path / "custom-agy-home"
    assert (
        antigravity_command.resolve_antigravity_home(
            {"ANTIGRAVITY_CLI_HOME": str(custom)}
        )
        == custom
    )
    user_root = tmp_path / "user-profile"
    assert antigravity_command.resolve_antigravity_home(
        {"USERPROFILE": str(user_root)}
    ) == (user_root / ".gemini" / "antigravity-cli")
    home_root = tmp_path / "posix-home"
    assert antigravity_command.resolve_antigravity_home(
        {"HOME": str(home_root)}
    ) == (home_root / ".gemini" / "antigravity-cli")


def test_antigravity_status_requires_authentication() -> None:
    client = _client()
    assert client.get("/v1/providers/google-antigravity/status").status_code == 401
    assert client.get("/v1/providers/antigravity-cli/status").status_code == 401


def test_antigravity_status_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_binary",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_home",
        lambda *args, **kwargs: tmp_path,
    )
    client = _client()

    response = client.get("/v1/providers/google-antigravity/status", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "installed": False,
        "version": None,
        "supported": False,
        "authenticated": False,
        "login_command": "agy",
    }


def test_antigravity_status_binary_present_supported_without_jetski_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_binary",
        lambda *args, **kwargs: "/usr/local/bin/agy",
    )
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.antigravity_version",
        lambda *args, **kwargs: (1, 5, 2),
    )
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_home",
        lambda *args, **kwargs: tmp_path,
    )
    client = _client()

    response = client.get("/v1/providers/google-antigravity/status", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "installed": True,
        "version": "1.5.2",
        "supported": True,
        "authenticated": False,
        "login_command": "agy",
    }


def test_antigravity_status_outdated_version_with_jetski_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "jetski_state.pbtxt").write_text("oauth_token: \"x\"\n", encoding="utf-8")
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_binary",
        lambda *args, **kwargs: "/usr/local/bin/agy",
    )
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.antigravity_version",
        lambda *args, **kwargs: (1, 0, 14),
    )
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_home",
        lambda *args, **kwargs: tmp_path,
    )
    client = _client()

    response = client.get("/v1/providers/google-antigravity/status", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "installed": True,
        "version": "1.0.14",
        "supported": False,
        "authenticated": True,
        "login_command": "agy",
    }


def test_antigravity_status_ready_and_antigravity_cli_alias(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "jetski_state.pbtxt").write_text("oauth_token: \"x\"\n", encoding="utf-8")
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_binary",
        lambda *args, **kwargs: "/usr/local/bin/agy",
    )
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.antigravity_version",
        lambda *args, **kwargs: (1, 5, 2),
    )
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_home",
        lambda *args, **kwargs: tmp_path,
    )
    client = _client()

    r1 = client.get("/v1/providers/google-antigravity/status", headers=AUTH)
    r2 = client.get("/v1/providers/antigravity-cli/status", headers=AUTH)

    expected = {
        "installed": True,
        "version": "1.5.2",
        "supported": True,
        "authenticated": True,
        "login_command": "agy",
    }
    assert r1.status_code == 200
    assert r1.json() == expected
    assert r2.status_code == 200
    assert r2.json() == expected


def test_antigravity_status_never_opens_or_reads_jetski_state_pbtxt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_file = tmp_path / "jetski_state.pbtxt"
    state_file.write_text(
        "oauth_token: \"SECRET_GOOGLE_STATE_DO_NOT_READ\"\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_binary",
        lambda *args, **kwargs: "/usr/local/bin/agy",
    )
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.antigravity_version",
        lambda *args, **kwargs: (1, 5, 2),
    )
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_home",
        lambda *args, **kwargs: tmp_path,
    )

    real_path_open = Path.open
    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes
    real_builtin_open = builtins.open

    def _guard_path(path_obj: Any) -> None:
        if "jetski_state" in str(path_obj).lower():
            raise AssertionError("jetski_state.pbtxt must NEVER be opened or read")

    def guarded_path_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        _guard_path(self)
        return real_path_open(self, *args, **kwargs)

    def guarded_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        _guard_path(self)
        return real_read_text(self, *args, **kwargs)

    def guarded_read_bytes(self: Path, *args: Any, **kwargs: Any) -> bytes:
        _guard_path(self)
        return real_read_bytes(self, *args, **kwargs)

    def guarded_builtin_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        _guard_path(file)
        return real_builtin_open(file, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    monkeypatch.setattr(builtins, "open", guarded_builtin_open)

    client = _client()
    response = client.get("/v1/providers/google-antigravity/status", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert "SECRET_GOOGLE_STATE_DO_NOT_READ" not in response.text


def test_antigravity_status_rejects_directory_or_symlink_jetski_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_dir = tmp_path / "jetski_state.pbtxt"
    state_dir.mkdir()

    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_binary",
        lambda *args, **kwargs: "/usr/local/bin/agy",
    )
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.antigravity_version",
        lambda *args, **kwargs: (1, 5, 2),
    )
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_home",
        lambda *args, **kwargs: tmp_path,
    )

    client = _client()
    response = client.get("/v1/providers/google-antigravity/status", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["authenticated"] is False

    state_dir.rmdir()
    state_dir.write_text("x", encoding="utf-8")
    real_is_symlink = Path.is_symlink

    def fake_is_symlink(self: Path) -> bool:
        if "jetski_state.pbtxt" in str(self).lower():
            return True
        return real_is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
    response_symlink = client.get(
        "/v1/providers/google-antigravity/status", headers=AUTH
    )
    assert response_symlink.status_code == 200
    assert response_symlink.json()["authenticated"] is False

    # Also reject when the antigravity-cli home directory itself is a symlink
    def fake_home_is_symlink(self: Path) -> bool:
        if self == tmp_path:
            return True
        return real_is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", fake_home_is_symlink)
    response_home_symlink = client.get(
        "/v1/providers/google-antigravity/status", headers=AUTH
    )
    assert response_home_symlink.status_code == 200
    assert response_home_symlink.json()["authenticated"] is False


def test_antigravity_status_broken_binary_version_none_and_oserror_on_state_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_binary",
        lambda *args, **kwargs: "/usr/local/bin/agy",
    )
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.antigravity_version",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "agentos.providers.antigravity_cli.command.resolve_antigravity_home",
        lambda *args, **kwargs: tmp_path,
    )
    real_is_file = Path.is_file

    def raising_is_file(self: Path) -> bool:
        if "jetski_state.pbtxt" in str(self).lower():
            raise OSError("permission denied")
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", raising_is_file)

    client = _client()
    response = client.get("/v1/providers/google-antigravity/status", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "installed": True,
        "version": None,
        "supported": False,
        "authenticated": False,
        "login_command": "agy",
    }

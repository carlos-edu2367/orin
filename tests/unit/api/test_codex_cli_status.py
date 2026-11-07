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

AUTH = {"Authorization": "Bearer pat-test"}


def _client() -> TestClient:
    security = InMemorySecurityService()
    security.add_pat(
        "pat-test",
        AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})),
    )
    app = create_app(ApiServices(security=security))
    return TestClient(app)


def test_codex_status_requires_authentication() -> None:
    client = _client()
    response = client.get("/v1/providers/openai-codex/status")
    assert response.status_code == 401


def test_codex_status_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "agentos.providers.codex_cli.command.resolve_codex_binary",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "agentos.providers.codex_cli.catalog.resolve_codex_home",
        lambda *args, **kwargs: tmp_path,
    )
    client = _client()

    response = client.get("/v1/providers/openai-codex/status", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "installed": False,
        "version": None,
        "supported": False,
        "authenticated": False,
        "login_command": "codex login",
    }


def test_codex_status_binary_present_supported_without_auth_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "agentos.providers.codex_cli.command.resolve_codex_binary",
        lambda *args, **kwargs: "/usr/local/bin/codex",
    )
    monkeypatch.setattr(
        "agentos.providers.codex_cli.command.codex_version",
        lambda *args, **kwargs: (0, 167, 0),
    )
    monkeypatch.setattr(
        "agentos.providers.codex_cli.catalog.resolve_codex_home",
        lambda *args, **kwargs: tmp_path,
    )
    client = _client()

    response = client.get("/v1/providers/openai-codex/status", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "installed": True,
        "version": "0.167.0",
        "supported": True,
        "authenticated": False,
        "login_command": "codex login",
    }


def test_codex_status_outdated_version_with_auth_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "auth.json").write_text('{"token":"x"}', encoding="utf-8")
    monkeypatch.setattr(
        "agentos.providers.codex_cli.command.resolve_codex_binary",
        lambda *args, **kwargs: "/usr/local/bin/codex",
    )
    monkeypatch.setattr(
        "agentos.providers.codex_cli.command.codex_version",
        lambda *args, **kwargs: (0, 160, 0),
    )
    monkeypatch.setattr(
        "agentos.providers.codex_cli.catalog.resolve_codex_home",
        lambda *args, **kwargs: tmp_path,
    )
    client = _client()

    response = client.get("/v1/providers/openai-codex/status", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "installed": True,
        "version": "0.160.0",
        "supported": False,
        "authenticated": True,
        "login_command": "codex login",
    }


def test_codex_status_ready_and_codex_cli_alias(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "auth.json").write_text('{"token":"x"}', encoding="utf-8")
    monkeypatch.setattr(
        "agentos.providers.codex_cli.command.resolve_codex_binary",
        lambda *args, **kwargs: "/usr/local/bin/codex",
    )
    monkeypatch.setattr(
        "agentos.providers.codex_cli.command.codex_version",
        lambda *args, **kwargs: (0, 167, 0),
    )
    monkeypatch.setattr(
        "agentos.providers.codex_cli.catalog.resolve_codex_home",
        lambda *args, **kwargs: tmp_path,
    )
    client = _client()

    r1 = client.get("/v1/providers/openai-codex/status", headers=AUTH)
    r2 = client.get("/v1/providers/codex-cli/status", headers=AUTH)

    expected = {
        "installed": True,
        "version": "0.167.0",
        "supported": True,
        "authenticated": True,
        "login_command": "codex login",
    }
    assert r1.status_code == 200
    assert r1.json() == expected
    assert r2.status_code == 200
    assert r2.json() == expected


def test_codex_status_never_opens_or_reads_auth_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"access_token": "SECRET_TOKEN_DO_NOT_READ"}', encoding="utf-8")

    monkeypatch.setattr(
        "agentos.providers.codex_cli.command.resolve_codex_binary",
        lambda *args, **kwargs: "/usr/local/bin/codex",
    )
    monkeypatch.setattr(
        "agentos.providers.codex_cli.command.codex_version",
        lambda *args, **kwargs: (0, 167, 0),
    )
    monkeypatch.setattr(
        "agentos.providers.codex_cli.catalog.resolve_codex_home",
        lambda *args, **kwargs: tmp_path,
    )

    real_path_open = Path.open
    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes
    real_builtin_open = builtins.open

    def _guard_path(path_obj: Any) -> None:
        if "auth.json" in str(path_obj).lower():
            raise AssertionError("auth.json must NEVER be opened or read")

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
    response = client.get("/v1/providers/openai-codex/status", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert "SECRET_TOKEN_DO_NOT_READ" not in response.text


def test_codex_status_rejects_directory_or_symlink_auth_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_dir = tmp_path / "auth.json"
    auth_dir.mkdir()

    monkeypatch.setattr(
        "agentos.providers.codex_cli.command.resolve_codex_binary",
        lambda *args, **kwargs: "/usr/local/bin/codex",
    )
    monkeypatch.setattr(
        "agentos.providers.codex_cli.command.codex_version",
        lambda *args, **kwargs: (0, 167, 0),
    )
    monkeypatch.setattr(
        "agentos.providers.codex_cli.catalog.resolve_codex_home",
        lambda *args, **kwargs: tmp_path,
    )

    client = _client()
    response = client.get("/v1/providers/openai-codex/status", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["authenticated"] is False


def test_codex_status_broken_binary_version_none_and_oserror_on_auth_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "agentos.providers.codex_cli.command.resolve_codex_binary",
        lambda *args, **kwargs: "/usr/local/bin/codex",
    )
    monkeypatch.setattr(
        "agentos.providers.codex_cli.command.codex_version",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "agentos.providers.codex_cli.catalog.resolve_codex_home",
        lambda *args, **kwargs: tmp_path,
    )
    real_is_file = Path.is_file

    def raising_is_file(self: Path) -> bool:
        if "auth.json" in str(self).lower():
            raise OSError("permission denied")
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", raising_is_file)

    client = _client()
    response = client.get("/v1/providers/openai-codex/status", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "installed": True,
        "version": None,
        "supported": False,
        "authenticated": False,
        "login_command": "codex login",
    }


def test_claude_cli_status_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agentos.providers.claude_cli.command.resolve_claude_binary",
        lambda *args, **kwargs: "/usr/local/bin/claude",
    )
    monkeypatch.setattr(
        "agentos.providers.claude_cli.command.claude_cli_version",
        lambda *args, **kwargs: (2, 1, 309),
    )
    monkeypatch.setattr(
        "agentos.providers.claude_cli.command.check_claude_cli_status",
        lambda *args, **kwargs: {"loggedIn": True, "authMethod": "claude.ai"},
    )

    client = _client()
    assert client.get("/v1/providers/claude-cli/status").status_code == 401
    response = client.get("/v1/providers/claude-cli/status", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {
        "installed": True,
        "version": "2.1.309",
        "supported": True,
        "authenticated": True,
        "login_command": "claude auth login",
    }

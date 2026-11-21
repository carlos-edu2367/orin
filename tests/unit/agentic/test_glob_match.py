"""Unit tests for cross-platform POSIX-style glob matching (WS-A3)."""

from __future__ import annotations

import pytest

from agentos.agentic.glob_match import match_glob, matches_any_glob, normalize_glob_path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/auth/login.py", "src/auth/login.py"),
        (r"src\auth\login.py", "src/auth/login.py"),
        ("./src/auth/login.py", "src/auth/login.py"),
        (r".\src\auth\login.py", "src/auth/login.py"),
        ("././.env", ".env"),
        (".env", ".env"),
        (".orin/state.json", ".orin/state.json"),
        (r".orin\checkpoints\t1\manifest.json", ".orin/checkpoints/t1/manifest.json"),
        (".github/workflows/ci.yml", ".github/workflows/ci.yml"),
        ("a//b///c.py", "a/b/c.py"),
        ("", ""),
    ],
)
def test_normalize_glob_path_preserves_dotfiles_and_normalizes_separators(raw: str, expected: str) -> None:
    assert normalize_glob_path(raw) == expected


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        # **/dir/** matches both root and nested
        ("migrations/001.py", "**/migrations/**", True),
        ("src/db/migrations/001.py", "**/migrations/**", True),
        ("src/db/migrations/versions/001.py", "**/migrations/**", True),
        ("migrations_old/001.py", "**/migrations/**", False),
        ("src/migrations.py", "**/migrations/**", False),
        # alembic
        ("alembic/env.py", "**/alembic/**", True),
        (r"alembic\versions\0065.py", "**/alembic/**", True),
        ("db/alembic/versions/0065.py", "**/alembic/**", True),
        # **/*.ext matches root and nested
        ("uv.lock", "**/*.lock", True),
        ("sub/uv.lock", "**/*.lock", True),
        ("a/b/poetry.lock", "**/*.lock", True),
        ("uv.lock.bak", "**/*.lock", False),
        ("schema.sql", "**/*.sql", True),
        ("db/init/schema.sql", "**/*.sql", True),
        # .orin/** and .github/workflows/**
        (".orin/state.json", ".orin/**", True),
        (".orin/a/b.json", ".orin/**", True),
        (".orin_other/a.json", ".orin/**", False),
        (".github/workflows/ci.yml", ".github/workflows/**", True),
        (".github/workflows/sub/release.yml", ".github/workflows/**", True),
        ("other/.github/workflows/ci.yml", ".github/workflows/**", False),
        # **/.env*
        (".env", "**/.env*", True),
        ("./.env", "**/.env*", True),
        (".env.local", "**/.env*", True),
        ("sub/.env.prod", "**/.env*", True),
        ("deploy/my.env", "**/.env*", False),
        # **/*.min.*
        ("app.min.js", "**/*.min.*", True),
        ("dist/app.min.js", "**/*.min.*", True),
        ("static/css/site.min.css", "**/*.min.*", True),
        ("static/min.js", "**/*.min.*", False),
        # **/Dockerfile*
        ("Dockerfile", "**/Dockerfile*", True),
        ("Dockerfile.prod", "**/Dockerfile*", True),
        ("docker/Dockerfile.dev", "**/Dockerfile*", True),
        # Windows backslashes
        (r"src\auth\login.py", "**/auth/**", True),
        (r"src\security\tokens.py", "**/security/**", True),
        (r"node_modules\pkg\index.js", "**/node_modules/**", True),
        (r".\frontend\package-lock.json", "**/package-lock.json", True),
        # Single * does not cross /
        ("foo.py", "*.py", True),
        ("dir/foo.py", "*.py", False),
        # ? matches one non-slash char
        ("a/x/c", "a/?/c", True),
        ("a/xy/c", "a/?/c", False),
        ("a//c", "a/?/c", False),
        # Middle /**/
        ("a/b", "a/**/b", True),
        ("a/x/b", "a/**/b", True),
        ("a/x/y/b", "a/**/b", True),
        ("a/xb", "a/**/b", False),
        ("ax/b", "a/**/b", False),
        # Character classes
        ("file1.py", "file[0-9].py", True),
        ("filea.py", "file[0-9].py", False),
        ("filea.py", "file[!0-9].py", True),
        # Empty inputs
        ("", "**/*.py", False),
        ("foo.py", "", False),
    ],
)
def test_match_glob_cases(path: str, pattern: str, expected: bool) -> None:
    assert match_glob(path, pattern) is expected


def test_match_glob_case_sensitivity() -> None:
    # Default is deterministic POSIX case-sensitive across OSes (even Windows)
    assert match_glob("Dockerfile", "**/Dockerfile*") is True
    assert match_glob("dockerfile", "**/Dockerfile*") is False
    assert match_glob("dockerfile", "**/Dockerfile*", case_sensitive=False) is True
    assert match_glob(r"SRC\AUTH\Login.py", "**/auth/**", case_sensitive=False) is True


def test_matches_any_glob() -> None:
    assert matches_any_glob("src/auth/login.py", ()) is False
    assert matches_any_glob("src/auth/login.py", ("**/*.sql", "**/auth/**")) is True
    assert matches_any_glob(r"src\utils\math.py", ("**/*.sql", "**/auth/**")) is False

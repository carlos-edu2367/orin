"""Red team round 6: parent memory files of the CLI cwd (N5)."""
from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Iterator

import pytest

from agentos.providers.claude_cli.command import ensure_isolated_cwd
from agentos.providers.claude_cli.errors import CliSecurityAbort

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX ownership and symlinks")


@pytest.fixture
def home_dir() -> Iterator[Path]:
    base = Path.home() / ".cache" / f"orin-redteam-r6-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.mark.parametrize("name", ["CLAUDE.md", "CLAUDE.local.md"])
def test_memory_file_in_a_temp_parent_is_refused(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_text("planted")
    with pytest.raises(CliSecurityAbort):
        ensure_isolated_cwd(tmp_path / "a" / "cwd")


@posix_only
def test_symlinked_memory_file_in_a_temp_parent_is_refused(tmp_path: Path) -> None:
    target = tmp_path.parent / f"target-{uuid.uuid4().hex[:6]}.md"
    target.write_text("planted")
    try:
        (tmp_path / "CLAUDE.md").symlink_to(target)
        with pytest.raises(CliSecurityAbort):
            ensure_isolated_cwd(tmp_path / "cwd")
    finally:
        target.unlink(missing_ok=True)


def test_users_own_memory_file_outside_temp_is_accepted_by_design(home_dir: Path) -> None:
    (home_dir / "CLAUDE.md").write_text("my notes")
    assert ensure_isolated_cwd(home_dir / "workdir").is_dir()


@posix_only
@pytest.mark.skipif(not hasattr(os, "getuid") or os.getuid() != 0, reason="needs root to chown")
def test_foreign_memory_file_outside_temp_is_refused(home_dir: Path) -> None:
    f = home_dir / "CLAUDE.md"
    f.write_text("planted")
    os.chown(f, 4242, 4242)
    with pytest.raises(CliSecurityAbort):
        ensure_isolated_cwd(home_dir / "workdir")


def test_clean_temp_parents_are_not_a_false_refusal(tmp_path: Path) -> None:
    assert ensure_isolated_cwd(tmp_path / "x" / "cwd").is_dir()


@posix_only
def test_memory_file_reached_through_a_symlinked_parent_into_temp_is_refused(tmp_path: Path, home_dir: Path) -> None:
    """root = <home>/link/cwd where <home>/link -> a temp dir holding CLAUDE.md: the CLI's cwd is in temp."""
    planted = tmp_path / "shared"
    planted.mkdir()
    (planted / "CLAUDE.md").write_text("planted")
    (home_dir / "link").symlink_to(planted, target_is_directory=True)
    with pytest.raises(CliSecurityAbort):
        ensure_isolated_cwd(home_dir / "link" / "cwd")


@posix_only
def test_memory_file_above_the_real_cwd_reached_through_a_symlink_is_refused(tmp_path: Path, home_dir: Path) -> None:
    """Orin walks the parents of the path as given; the CLI walks the parents of its real cwd. With
    <home>/data -> <temp>/x, the CLI runs in <temp>/x/cwd and reads <temp>/CLAUDE.md, a parent Orin never checks."""
    shared = tmp_path / "x"
    shared.mkdir()
    (tmp_path / "CLAUDE.md").write_text("planted")
    (home_dir / "data").symlink_to(shared, target_is_directory=True)
    with pytest.raises(CliSecurityAbort):
        ensure_isolated_cwd(home_dir / "data" / "cwd")

from __future__ import annotations

from pathlib import Path

from agentos.pathsafety import resolve_contained


def test_a_path_inside_the_root_resolves(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "a.txt"
    target.write_text("x", encoding="utf-8")

    assert resolve_contained(target, root) == target.resolve()


def test_a_path_outside_the_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")

    assert resolve_contained(outside, root) is None


def test_a_symlink_escaping_the_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        return  # symlink creation needs a privilege this environment lacks

    assert resolve_contained(link / "secret.txt", root) is None

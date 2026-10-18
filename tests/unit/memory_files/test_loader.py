from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from agentos.memory_files import (
    LOCAL,
    MANAGED,
    PROJECT,
    USER,
    MemoryFiles,
    glob_match,
    instructions_payload,
    split_frontmatter,
)


def _mk(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    return root


def test_layers_load_in_order(tmp_path: Path) -> None:
    user = _mk(tmp_path / "home" / "CLAUDE.md", "user rules")
    managed = _mk(tmp_path / "managed" / "ORIN.md", "managed rules")
    root = _repo(tmp_path)
    _mk(root / "CLAUDE.md", "claude project")
    _mk(root / "ORIN.md", "orin project")
    _mk(root / "AGENTS.md", "agents")
    _mk(root / ".claude" / "rules" / "style.md", "style rule")
    _mk(root / "CLAUDE.local.md", "local notes")
    files = MemoryFiles(root, trusted=False, user_files=(user,), managed_files=(managed,), managed_text="policy text")
    assert [(item.memory_type, item.content) for item in files.files] == [
        (MANAGED, "managed rules"), (MANAGED, "policy text"), (USER, "user rules"), (PROJECT, "agents"),
        (PROJECT, "claude project"), (PROJECT, "orin project"), (PROJECT, "style rule"), (LOCAL, "local notes"),
    ]
    assert all(item.load_reason == "session_start" for item in files.files)


def test_walk_up_to_the_repository_root(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _mk(root / "CLAUDE.md", "top")
    sub = root / "pkg"
    _mk(sub / "CLAUDE.md", "pkg")
    assert [item.content for item in MemoryFiles(sub, trusted=False, walk_up=True).files] == ["top", "pkg"]
    assert [item.content for item in MemoryFiles(sub, trusted=False, walk_up=False).files] == ["pkg"]


def test_imports_are_relative_skip_code_and_stop_at_cycles(tmp_path: Path) -> None:
    root = tmp_path / "r"
    main = _mk(root / "CLAUDE.md", "main @docs/a.md and `@docs/ignored.md`\n```\n@docs/fenced.md\n```")
    _mk(root / "docs" / "a.md", "A @b.md")
    _mk(root / "docs" / "b.md", "B @a.md")
    _mk(root / "docs" / "ignored.md", "IGNORED")
    _mk(root / "docs" / "fenced.md", "FENCED")
    files = MemoryFiles(root, trusted=False).files
    assert [(item.content.split()[0], item.load_reason) for item in files] == [("main", "session_start"), ("A", "include"), ("B", "include")]
    assert files[1].parent == main


def test_import_depth_is_bounded(tmp_path: Path) -> None:
    root = tmp_path / "r"
    _mk(root / "CLAUDE.md", "root @d1.md")
    for level in range(1, 8):
        _mk(root / f"d{level}.md", f"level{level} @d{level + 1}.md")
    loaded = [item.content.split()[0] for item in MemoryFiles(root, trusted=False).files]
    assert loaded == ["root", "level1", "level2", "level3", "level4", "level5"]


def test_an_untrusted_project_cannot_import_outside(tmp_path: Path) -> None:
    secret = _mk(tmp_path / "outside" / "secret.md", "SECRET")
    root = tmp_path / "r"
    _mk(root / "CLAUDE.md", f"see @{secret}")
    assert "SECRET" not in [item.content for item in MemoryFiles(root, trusted=False).files]
    assert "SECRET" in [item.content for item in MemoryFiles(root, trusted=True).files]


def test_only_text_files_can_be_imported(tmp_path: Path) -> None:
    root = tmp_path / "r"
    _mk(root / "CLAUDE.md", "root @.env @key.pem @notes.txt")
    _mk(root / ".env", "TOKEN=1")
    _mk(root / "key.pem", "PRIVATE")
    _mk(root / "notes.txt", "notes")
    assert [item.content for item in MemoryFiles(root, trusted=True).files] == ["root @.env @key.pem @notes.txt", "notes"]


def test_binary_and_huge_files(tmp_path: Path) -> None:
    root = tmp_path / "r"
    (root).mkdir()
    (root / "CLAUDE.md").write_bytes(b"bin\x00ary")
    _mk(root / "ORIN.md", "y" * 100_000)
    files = MemoryFiles(root, trusted=False).files
    assert [item.path.name for item in files] == ["ORIN.md"]
    assert len(files[0].content) == 64 * 1024


def test_excludes_never_hide_managed_files(tmp_path: Path) -> None:
    managed = _mk(tmp_path / "managed" / "CLAUDE.md", "managed")
    root = tmp_path / "r"
    _mk(root / "CLAUDE.md", "project")
    _mk(root / "CLAUDE.local.md", "local")
    files = MemoryFiles(root, trusted=False, managed_files=(managed,), excludes=("**/CLAUDE.local.md", "**/CLAUDE.md"))
    assert [item.content for item in files.files] == ["managed"]


def test_conditional_rules_load_with_a_matching_read(tmp_path: Path) -> None:
    root = tmp_path / "r"
    _mk(root / ".claude" / "rules" / "ts.md", '---\npaths:\n  - "src/**/*.ts"\n---\nUse strict TS')
    files = MemoryFiles(root, trusted=False)
    assert all("strict" not in item.content for item in files.files)
    assert files.on_file_read(root / "README.md") == []
    loaded = files.on_file_read(root / "src" / "app" / "main.ts")
    assert [(item.content, item.load_reason, item.globs) for item in loaded] == [("Use strict TS", "path_glob_match", ("src/**/*.ts",))]
    assert files.on_file_read(root / "src" / "x.ts") == []


def test_subdirectory_memory_loads_on_the_first_read_below_it(tmp_path: Path) -> None:
    root = tmp_path / "r"
    _mk(root / "pkg" / "CLAUDE.md", "pkg rules")
    _mk(root / "pkg" / "api" / "CLAUDE.md", "api rules")
    files = MemoryFiles(root, trusted=False)
    loaded = files.on_file_read(root / "pkg" / "api" / "handler.py")
    assert [(item.content, item.load_reason) for item in loaded] == [("pkg rules", "nested_traversal"), ("api rules", "nested_traversal")]
    assert files.on_file_read(root / "pkg" / "api" / "other.py") == []
    assert files.on_file_read(tmp_path / "elsewhere.py") == []


def test_frontmatter_forms() -> None:
    meta, body = split_frontmatter("---\npaths: [a/*.py, 'b/**']\nname: x\n---\nbody")
    assert meta == {"paths": ["a/*.py", "b/**"], "name": "x"} and body == "body"
    assert split_frontmatter("---\nno end\nbody") == ({}, "---\nno end\nbody")


def test_glob_semantics() -> None:
    assert glob_match("src/**/*.ts", "src/a/b.ts") and glob_match("src/**/*.ts", "src/b.ts")
    assert glob_match("*.md", "docs/x.md")
    assert not glob_match("src/*.ts", "src/a/b.ts")


def test_a_hostile_glob_cannot_stall_a_read() -> None:
    # Backtracking matchers take many seconds on patterns like this one (repository rule files supply the patterns).
    started = time.perf_counter()
    assert glob_match("*a" * 8 + "*b", "a" * 40 + "c") is False
    assert time.perf_counter() - started < 1.0


def test_render_is_bounded_and_labelled(tmp_path: Path) -> None:
    root = tmp_path / "r"
    _mk(root / "CLAUDE.md", "z" * 5000)
    files = MemoryFiles(root, trusted=False)
    text = files.render(limit=1000)
    assert text.startswith("### CLAUDE.md (project)") and text.count("z") == 1000


def test_instructions_payload_shape(tmp_path: Path) -> None:
    root = tmp_path / "r"
    _mk(root / "CLAUDE.md", "x")
    item = MemoryFiles(root, trusted=False).files[0]
    payload = instructions_payload(item, trigger=root / "a.py")
    assert payload == {"file_path": str(item.path), "memory_type": "Project", "load_reason": "session_start", "trigger_file_path": str(root / "a.py")}


def _link_dir(link: Path, target: Path) -> bool:
    try:
        if os.name == "nt":
            return subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True).returncode == 0
        link.symlink_to(target, target_is_directory=True)
        return True
    except OSError:
        return False


def test_a_linked_directory_cannot_pull_files_from_outside_an_untrusted_project(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _mk(outside / "CLAUDE.md", "OUTSIDE")
    root = tmp_path / "r"
    root.mkdir()
    if not _link_dir(root / ".claude", outside):
        pytest.skip("cannot create a directory link here")
    assert "OUTSIDE" not in [item.content for item in MemoryFiles(root, trusted=False).files]

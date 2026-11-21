"""Unit tests for per-turn change measurement and verifier thresholds (WS-A3)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.change_measure import ChangeSummary, measure_turn_changes
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.checkpoints import FileCheckpointStore
from agentos.settings.loader import EffectiveSettings, SettingsLayer


class _FakeToolset:
    def __init__(self, paths: list[str] | tuple[str, ...] = (), *, turn_id: str | None = None) -> None:
        self._changed_paths: set[str] = set(paths)
        self.turn_id = turn_id

    def changed_paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._changed_paths))


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _settings_from(*layers: tuple[str, dict[str, Any]]) -> EffectiveSettings:
    built = tuple(SettingsLayer(name, None, data) for name, data in layers)
    return EffectiveSettings(data={}, provenance={}, layers=built, errors=(), trusted=True)


def test_two_files_and_40_lines_do_not_trigger(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path)
    turn_id = "turn-2f-40l"

    f1 = tmp_path / "src" / "a.py"
    f2 = tmp_path / "src" / "b.py"
    _write_lines(f1, [f"line {i}" for i in range(20)])
    _write_lines(f2, [f"line {i}" for i in range(20)])

    store.snapshot_before_write(turn_id, "src/a.py")
    store.snapshot_before_write(turn_id, "src/b.py")

    # Add 20 lines to each file -> 40 changed lines total
    _write_lines(f1, [f"line {i}" for i in range(40)])
    _write_lines(f2, [f"line {i}" for i in range(40)])

    toolset = _FakeToolset(["src/a.py", "src/b.py"], turn_id=turn_id)
    summary = measure_turn_changes(toolset, store, tmp_path, turn_id=turn_id)

    assert isinstance(summary, ChangeSummary)
    assert summary.files == 2
    assert summary.lines == 40
    assert summary.lines_known is True
    assert summary.sensitive_hits == ()
    assert summary.changed_files == ("src/a.py", "src/b.py")
    assert summary.triggered is False
    assert summary.reason is None


def test_three_files_and_30_lines_trigger_by_files(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path)
    turn_id = "turn-3f"

    paths = ["src/a.py", "src/b.py", "src/c.py"]
    for rel in paths:
        store.snapshot_before_write(turn_id, rel)
        _write_lines(tmp_path / rel, [f"x = {i}" for i in range(10)])

    toolset = _FakeToolset(paths, turn_id=turn_id)
    summary = measure_turn_changes(toolset, store, tmp_path, turn_id=turn_id)

    assert summary.files == 3
    assert summary.lines == 30
    assert summary.lines_known is True
    assert summary.sensitive_hits == ()
    assert summary.triggered is True
    assert summary.reason == "files"


def test_single_alembic_or_migration_file_triggers_by_sensitive_path(tmp_path: Path) -> None:
    for rel in (
        "alembic/versions/0065.py",
        "src/agentos/persistence/postgres/migrations/versions/0065.py",
    ):
        ws = tmp_path / rel.replace("/", "_")
        ws.mkdir(parents=True, exist_ok=True)
        store = FileCheckpointStore(ws)
        turn_id = "turn-sens"
        store.snapshot_before_write(turn_id, rel)
        _write_lines(ws / rel, ["def upgrade():", "    pass"])

        toolset = _FakeToolset([rel], turn_id=turn_id)
        summary = measure_turn_changes(toolset, store, ws, turn_id=turn_id)

        assert summary.files == 1
        assert summary.lines == 2
        assert summary.lines_known is True
        assert summary.sensitive_hits == (rel,)
        assert summary.triggered is True
        assert summary.reason == "sensitive_path"


def test_150_lines_in_one_file_triggers_by_lines(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path)
    turn_id = "turn-150l"
    rel = "src/service.py"

    # Existing 50 lines -> replace 50 and add 50 = 150 lines (50 removed + 100 added)
    _write_lines(tmp_path / rel, [f"old_{i}" for i in range(50)])
    store.snapshot_before_write(turn_id, rel)
    _write_lines(tmp_path / rel, [f"new_{i}" for i in range(100)])

    toolset = _FakeToolset([rel], turn_id=turn_id)
    summary = measure_turn_changes(toolset, store, tmp_path, turn_id=turn_id)

    assert summary.files == 1
    assert summary.lines == 150
    assert summary.lines_known is True
    assert summary.triggered is True
    assert summary.reason == "lines"


def test_deleted_file_counts_removed_lines(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path)
    turn_id = "turn-del"
    rel = "src/obsolete.py"
    target = tmp_path / rel
    _write_lines(target, [f"line_{i}" for i in range(160)])

    store.snapshot_before_write(turn_id, rel)
    target.unlink()

    toolset = _FakeToolset([rel], turn_id=turn_id)
    summary = measure_turn_changes(toolset, store, tmp_path, turn_id=turn_id)

    assert summary.files == 1
    assert summary.lines == 160
    assert summary.lines_known is True
    assert summary.triggered is True
    assert summary.reason == "lines"


def test_ignored_files_do_not_count_in_files_lines_or_sensitive_hits(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path)
    turn_id = "turn-ignore"
    ignored = [
        "uv.lock",
        "frontend/package-lock.json",
        "dist/bundle.js",
        ".orin/state.json",
        "app.min.js",
        "dist/auth/login.min.js",
    ]
    for rel in ignored:
        store.snapshot_before_write(turn_id, rel)
        _write_lines(tmp_path / rel, [f"line {i}" for i in range(200)])

    # Add one small non-ignored file
    store.snapshot_before_write(turn_id, "src/app.py")
    _write_lines(tmp_path / "src/app.py", ["print('hi')"])

    toolset = _FakeToolset([*ignored, "src/app.py"], turn_id=turn_id)
    summary = measure_turn_changes(toolset, store, tmp_path, turn_id=turn_id)

    assert summary.files == 1
    assert summary.lines == 1
    assert summary.lines_known is True
    assert summary.sensitive_hits == ()
    assert summary.changed_files == ("src/app.py",)
    assert summary.triggered is False
    assert summary.reason is None


def test_outside_git_without_snapshot_marks_lines_unknown_and_never_triggers_by_lines(tmp_path: Path) -> None:
    ws = tmp_path / "no_git_ws"
    ws.mkdir()
    store = FileCheckpointStore(ws)

    # 1 huge file modified via Bash with NO checkpoint snapshot and NO git repo
    _write_lines(ws / "src/big.py", [f"x = {i}" for i in range(500)])
    s1 = measure_turn_changes(_FakeToolset(["src/big.py"], turn_id="t1"), store, ws, turn_id="t1")
    assert s1.files == 1
    assert s1.lines == 0
    assert s1.lines_known is False
    assert s1.triggered is False
    assert s1.reason is None

    # 3 files without snapshot outside git still trigger by files
    _write_lines(ws / "src/b.py", ["b = 1"])
    _write_lines(ws / "src/c.py", ["c = 1"])
    s3 = measure_turn_changes(
        _FakeToolset(["src/big.py", "src/b.py", "src/c.py"], turn_id="t2"),
        store,
        ws,
        turn_id="t2",
    )
    assert s3.files == 3
    assert s3.lines_known is False
    assert s3.triggered is True
    assert s3.reason == "files"

    # 1 sensitive file without snapshot outside git still triggers by sensitive_path
    _write_lines(ws / "alembic/x.py", ["pass"])
    s_sens = measure_turn_changes(_FakeToolset(["alembic/x.py"], turn_id="t3"), store, ws, turn_id="t3")
    assert s_sens.files == 1
    assert s_sens.lines_known is False
    assert s_sens.sensitive_hits == ("alembic/x.py",)
    assert s_sens.triggered is True
    assert s_sens.reason == "sensitive_path"


def test_bash_edit_in_git_repo_without_snapshot_uses_git_diff_numstat(tmp_path: Path) -> None:
    repo = tmp_path / "git_ws"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@orin.local"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Orin Test"], cwd=repo, check=True, capture_output=True)

    target = repo / "src" / "worker.py"
    _write_lines(target, [f"base_{i}" for i in range(20)])
    subprocess.run(["git", "add", "src/worker.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    # Simulate Bash modifying the tracked file directly (no checkpoint snapshot)
    _write_lines(target, [f"base_{i}" for i in range(20)] + [f"added_{i}" for i in range(155)])

    store = FileCheckpointStore(repo)
    toolset = _FakeToolset(["src/worker.py"], turn_id="turn-bash-git")
    summary = measure_turn_changes(toolset, store, repo, turn_id="turn-bash-git")

    assert summary.files == 1
    assert summary.lines == 155
    assert summary.lines_known is True
    assert summary.triggered is True
    assert summary.reason == "lines"


def test_windows_backslash_paths_normalize_and_match_sensitive_and_ignore(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path)
    turn_id = "turn-win"
    store.snapshot_before_write(turn_id, r"src\auth\login.py")
    _write_lines(tmp_path / "src" / "auth" / "login.py", ["def login(): pass"])
    _write_lines(tmp_path / "dist" / "bundle.js", ["var x = 1;"])

    toolset = _FakeToolset([r"src\auth\login.py", r"dist\bundle.js"], turn_id=turn_id)
    summary = measure_turn_changes(toolset, store, tmp_path, turn_id=turn_id)

    assert summary.files == 1
    assert summary.changed_files == ("src/auth/login.py",)
    assert summary.sensitive_hits == ("src/auth/login.py",)
    assert summary.triggered is True
    assert summary.reason == "sensitive_path"


def test_empty_sensitive_paths_disables_sensitive_trigger(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path)
    turn_id = "turn-no-sens"
    rel = "alembic/versions/0065.py"
    store.snapshot_before_write(turn_id, rel)
    _write_lines(tmp_path / rel, ["pass"])
    toolset = _FakeToolset([rel], turn_id=turn_id)

    # Via explicit kwarg
    s_kw = measure_turn_changes(toolset, store, tmp_path, turn_id=turn_id, sensitive_paths=[])
    assert s_kw.sensitive_hits == ()
    assert s_kw.triggered is False
    assert s_kw.reason is None

    # Via EffectiveSettings
    settings = _settings_from(
        ("project", {"orchestration": {"verifier": {"threshold": {"sensitivePaths": []}}}}),
    )
    s_cfg = measure_turn_changes(toolset, store, tmp_path, turn_id=turn_id, settings=settings)
    assert s_cfg.sensitive_hits == ()
    assert s_cfg.triggered is False
    assert s_cfg.reason is None


def test_measure_turn_changes_caches_single_read_in_turn_dict(tmp_path: Path, monkeypatch: Any) -> None:
    store = FileCheckpointStore(tmp_path)
    turn_id = "turn-cached"
    rel = "src/mod.py"
    store.snapshot_before_write(turn_id, rel)
    _write_lines(tmp_path / rel, ["a = 1", "b = 2"])

    calls = 0
    real_get_checkpoint = store.get_checkpoint

    def counting_get_checkpoint(tid: str) -> dict[str, Any] | None:
        nonlocal calls
        calls += 1
        return real_get_checkpoint(tid)

    monkeypatch.setattr(store, "get_checkpoint", counting_get_checkpoint)

    turn_dict: dict[str, Any] = {}
    toolset = _FakeToolset([rel], turn_id=turn_id)

    first = measure_turn_changes(toolset, store, tmp_path, turn_id=turn_id, turn=turn_dict)
    # Even if more files are added later in the dict's lifecycle, cached turn result is reused
    toolset._changed_paths.add("alembic/versions/9999.py")
    second = measure_turn_changes(toolset, store, tmp_path, turn_id=turn_id, turn=turn_dict)

    assert calls == 1
    assert second is first
    assert second.files == 1
    assert second.lines == 2


def test_agent_toolset_changed_paths_public_accessor(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path)
    store = FileCheckpointStore(workspace)
    toolset = AgentToolset(workspace, checkpoint_store=store, turn_id="turn-real")
    toolset._changed_paths.update({"src/b.py", "src/a.py"})

    assert toolset.changed_paths() == ("src/a.py", "src/b.py")


def test_mixed_snapshot_and_unknown_file_outside_git(tmp_path: Path) -> None:
    ws = tmp_path / "mixed_no_git"
    ws.mkdir()
    store = FileCheckpointStore(ws)

    # 40 known lines + 1 unknown file -> lines_known=False, does NOT trigger
    store.snapshot_before_write("t-small", "src/a.py")
    _write_lines(ws / "src/a.py", [f"x = {i}" for i in range(40)])
    _write_lines(ws / "src/b.py", [f"y = {i}" for i in range(300)])
    s_small = measure_turn_changes(
        _FakeToolset(["src/a.py", "src/b.py"], turn_id="t-small"),
        store,
        ws,
        turn_id="t-small",
    )
    assert s_small.files == 2
    assert s_small.lines == 40
    assert s_small.lines_known is False
    assert s_small.triggered is False
    assert s_small.reason is None

    # 150 known lines + 1 unknown file -> lines_known=False, STILL triggers by known lines
    store.snapshot_before_write("t-big", "src/c.py")
    _write_lines(ws / "src/c.py", [f"z = {i}" for i in range(150)])
    s_big = measure_turn_changes(
        _FakeToolset(["src/c.py", "src/b.py"], turn_id="t-big"),
        store,
        ws,
        turn_id="t-big",
    )
    assert s_big.files == 2
    assert s_big.lines == 150
    assert s_big.lines_known is False
    assert s_big.triggered is True
    assert s_big.reason == "lines"


def test_bash_untracked_file_in_git_repo_counts_lines(tmp_path: Path) -> None:
    repo = tmp_path / "git_untracked"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@orin.local"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Orin Test"], cwd=repo, check=True, capture_output=True)
    _write_lines(repo / "README.md", ["# init"])
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    # New untracked file created via Bash (no checkpoint snapshot)
    _write_lines(repo / "src" / "generated.py", [f"v = {i}" for i in range(160)])
    store = FileCheckpointStore(repo)
    summary = measure_turn_changes(
        _FakeToolset(["src/generated.py"], turn_id="t-untracked"),
        store,
        repo,
        turn_id="t-untracked",
    )
    assert summary.files == 1
    assert summary.lines == 160
    assert summary.lines_known is True
    assert summary.triggered is True
    assert summary.reason == "lines"

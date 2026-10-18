from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from agentos.settings.writer import SettingsWriteError, add_permission_rule, update_settings_file


def test_creates_file_and_parents(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "settings.json"
    result = update_settings_file(target, lambda data: data.setdefault("model", "x"))
    assert result == {"model": "x"}
    assert json.loads(target.read_text(encoding="utf-8")) == {"model": "x"}
    assert not list(target.parent.glob("*.tmp")) and not list(target.parent.glob("*.lock"))


def test_refuses_to_overwrite_invalid_json(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("{broken", encoding="utf-8")
    with pytest.raises(SettingsWriteError):
        update_settings_file(target, lambda data: data.update(model="x"))
    assert target.read_text(encoding="utf-8") == "{broken"


def test_refuses_non_object_root(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("[1]", encoding="utf-8")
    with pytest.raises(SettingsWriteError):
        update_settings_file(target, lambda data: None)


def test_mutation_error_leaves_file_untouched(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text('{"keep": true}', encoding="utf-8")

    def boom(data: dict) -> None:
        data["keep"] = False
        raise ValueError("boom")

    with pytest.raises(ValueError):
        update_settings_file(target, boom)
    assert json.loads(target.read_text(encoding="utf-8")) == {"keep": True}
    assert not (tmp_path / "settings.json.lock").exists()


def test_concurrent_writers_do_not_lose_updates(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"

    def worker(index: int) -> None:
        for step in range(10):
            add_permission_rule(target, "allow", f"Bash(task-{index}-{step})")

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    rules = json.loads(target.read_text(encoding="utf-8"))["permissions"]["allow"]
    assert len(rules) == 40 and len(set(rules)) == 40


def test_add_permission_rule_validates_and_deduplicates(tmp_path: Path) -> None:
    target = tmp_path / "settings.local.json"
    assert add_permission_rule(target, "allow", "Bash(npm test)") is True
    assert add_permission_rule(target, "allow", "Bash(npm test)") is False
    with pytest.raises(ValueError):
        add_permission_rule(target, "maybe", "Read")
    with pytest.raises(ValueError):
        add_permission_rule(target, "allow", "")
    with pytest.raises(ValueError):
        add_permission_rule(target, "allow", "Bash(a)\nBash(b)")
    with pytest.raises(ValueError):
        add_permission_rule(target, "allow", "x" * 1001)
    assert json.loads(target.read_text(encoding="utf-8")) == {"permissions": {"allow": ["Bash(npm test)"]}}


def test_stale_lock_is_recovered(tmp_path: Path) -> None:
    import os
    import time

    target = tmp_path / "settings.json"
    lock = tmp_path / "settings.json.lock"
    lock.write_text("999999", encoding="utf-8")
    old = time.time() - 120
    os.utime(lock, (old, old))
    update_settings_file(target, lambda data: data.update(ok=True))
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}


def test_refuses_to_overwrite_non_utf8_file_and_leaves_bytes_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    original = json.dumps({"model": "x"}).encode("utf-16")
    target.write_bytes(original)
    with pytest.raises(SettingsWriteError):
        update_settings_file(target, lambda data: data.setdefault("permissions", {}))
    assert target.read_bytes() == original


def _age(path: Path, seconds: float = 120) -> None:
    import os
    import time

    old = time.time() - seconds
    os.utime(path, (old, old))


def test_lock_path_that_is_a_directory_fails_fast(tmp_path: Path) -> None:
    from agentos.settings.writer import LOCK_TIMEOUT_SECONDS

    target = tmp_path / "settings.json"
    lock = tmp_path / "settings.json.lock"
    lock.mkdir()
    (lock / "README").write_text("not a lock", encoding="utf-8")
    _age(lock)
    seen: dict[str, BaseException] = {}

    def run() -> None:
        try:
            add_permission_rule(target, "allow", "Read")
        except BaseException as error:  # noqa: BLE001 - captured for the assertion below
            seen["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(LOCK_TIMEOUT_SECONDS + 1)
    assert not thread.is_alive(), "writer spun on a lock directory"
    assert isinstance(seen.get("error"), SettingsWriteError)


def test_uninspectable_lock_times_out_instead_of_spinning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path as _Path

    from agentos.settings import writer

    monkeypatch.setattr(writer, "LOCK_TIMEOUT_SECONDS", 0.3)
    lock = tmp_path / "settings.json.lock"
    lock.write_text("123", encoding="utf-8")
    _age(lock)
    original_stat = _Path.stat

    def failing_stat(self: _Path, *args, **kwargs):
        if self.name == "settings.json.lock":
            raise OSError("cannot inspect lock")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(_Path, "stat", failing_stat)
    with pytest.raises(SettingsWriteError):
        update_settings_file(tmp_path / "settings.json", lambda data: None)


def test_release_never_removes_a_lock_now_owned_by_someone_else(tmp_path: Path) -> None:
    from agentos.settings import writer

    lock = tmp_path / "settings.json.lock"
    with writer._file_lock(tmp_path / "settings.json"):
        lock.write_text("foreign-token", encoding="utf-8")  # our lock was stolen while we held it
    assert lock.read_text(encoding="utf-8") == "foreign-token"


def test_four_writers_recover_one_stale_lock_exactly_once(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    lock = tmp_path / "settings.json.lock"
    lock.write_text("999999", encoding="utf-8")
    _age(lock)
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            add_permission_rule(target, "allow", f"Bash(writer-{index})")
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    rules = json.loads(target.read_text(encoding="utf-8"))["permissions"]["allow"]
    assert len(rules) == 4 and len(set(rules)) == 4
    assert not lock.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes and symlink semantics")
def test_write_keeps_the_file_mode_of_the_target(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_text("{}", encoding="utf-8")
    os.chmod(target, 0o600)
    update_settings_file(target, lambda data: data.update(model="x"))
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes and symlink semantics")
def test_new_settings_file_is_private_by_default(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    update_settings_file(target, lambda data: data.update(env={"TOKEN": "x"}))
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes and symlink semantics")
def test_write_through_a_symlink_keeps_the_link(tmp_path: Path) -> None:
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "settings.json"
    link.symlink_to(real)
    update_settings_file(link, lambda data: data.update(model="x"))
    assert link.is_symlink()
    assert json.loads(real.read_text(encoding="utf-8")) == {"model": "x"}

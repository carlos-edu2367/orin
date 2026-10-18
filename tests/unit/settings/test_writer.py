from __future__ import annotations

import json
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

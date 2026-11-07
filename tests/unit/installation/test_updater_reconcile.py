from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.installation import versions
from agentos.installation.profile import RuntimeProfile
from agentos.installation.update_job import UpdateJob
from agentos.installation.updater import (
    PREPARED_FILE,
    READY_SUFFIX,
    STATE_FILE,
    Updater,
    point_current,
    runtime_relative_path,
)


def _install_version(root: Path, version: str) -> Path:
    version_dir = root / version
    runtime = version_dir / runtime_relative_path()
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_bytes(b"orin")
    return version_dir


def test_reconcile_after_switch_without_state_write(tmp_path: Path) -> None:
    root = tmp_path / "Orin"
    _install_version(root, "0.11.0")
    v_new = _install_version(root, "0.12.1")
    point_current(root, v_new)

    (root / PREPARED_FILE).write_text(
        json.dumps({"version": "0.12.1", "sha256": "b" * 64}),
        encoding="utf-8",
    )
    (root / STATE_FILE).write_text(
        json.dumps({"previous": "0.10.0", "current": "0.11.0"}),
        encoding="utf-8",
    )

    updater = Updater(versions_root=root, current_version="0.12.1")
    updater.reconcile()

    assert not (root / PREPARED_FILE).exists()
    state = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    assert state["current"] == "0.12.1"
    assert state["previous"] == "0.11.0"
    assert state["last_attempt"]["status"] == "updated"
    assert state["last_attempt"]["version"] == "0.12.1"

    # Idempotency: a second call must not overwrite previous with 0.12.1.
    updater.reconcile()
    state_again = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    assert state_again["current"] == "0.12.1"
    assert state_again["previous"] == "0.11.0"


def test_reconcile_keeps_newer_prepared(tmp_path: Path) -> None:
    root = tmp_path / "Orin"
    v_active = _install_version(root, "0.12.1")
    point_current(root, v_active)

    ready_dir = root / f"0.12.2{READY_SUFFIX}"
    ready_runtime = ready_dir / runtime_relative_path()
    ready_runtime.parent.mkdir(parents=True, exist_ok=True)
    ready_runtime.write_bytes(b"orin-0.12.2")

    prepared_payload = {"version": "0.12.2", "sha256": "c" * 64, "notes": "Nova"}
    (root / PREPARED_FILE).write_text(json.dumps(prepared_payload), encoding="utf-8")
    initial_state = {
        "previous": "0.11.6",
        "current": "0.12.1",
        "last_attempt": {"status": "updated", "version": "0.12.1", "at": "2026-11-06T14:47:00-0300"},
    }
    (root / STATE_FILE).write_text(json.dumps(initial_state), encoding="utf-8")

    updater = Updater(versions_root=root, current_version="0.12.1")
    updater.reconcile()

    assert (root / PREPARED_FILE).is_file()
    prepared = updater.prepared_release()
    assert prepared is not None and prepared.version == "0.12.2"
    state = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    assert state["current"] == "0.12.1"
    assert state["previous"] == "0.11.6"
    assert state["last_attempt"] == initial_state["last_attempt"]


def test_update_job_status_reconciles_interrupted_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(versions.sys, "frozen", True, raising=False)
    root = tmp_path / "Orin"
    _install_version(root, "0.11.0")
    v_new = _install_version(root, "0.12.1")
    point_current(root, v_new)

    (root / PREPARED_FILE).write_text(
        json.dumps({"version": "0.12.1", "sha256": "d" * 64}),
        encoding="utf-8",
    )
    (root / STATE_FILE).write_text(
        json.dumps({"previous": "0.10.0", "current": "0.11.0"}),
        encoding="utf-8",
    )

    profile = RuntimeProfile("installed", v_new / "resources" / "runtime", "0.12.1", None)
    job = UpdateJob(profile)
    status = job.status()

    assert not (root / PREPARED_FILE).exists()
    assert status["state"] == "idle"
    assert status["last_attempt"]["status"] == "updated"
    assert status["last_attempt"]["version"] == "0.12.1"


def test_reconcile_overwrites_stale_failed_attempt_for_active_version_and_handles_corrupt_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Orin"
    _install_version(root, "0.9.0")
    v_new = _install_version(root, "0.10.0")
    point_current(root, v_new)

    # Case A: update-state.json already has current="0.10.0" (or prepared.json was already
    # removed), but last_attempt still carries a stale "failed" for 0.10.0.
    (root / STATE_FILE).write_text(
        json.dumps(
            {
                "previous": "0.9.0",
                "current": "0.10.0",
                "last_attempt": {"status": "failed", "version": "0.10.0", "message": "Acesso negado"},
            }
        ),
        encoding="utf-8",
    )

    updater = Updater(versions_root=root, current_version="0.10.0")
    updater.reconcile()

    state = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    assert state["current"] == "0.10.0"
    assert state["previous"] == "0.9.0"
    assert state["last_attempt"]["status"] == "updated"
    assert state["last_attempt"]["version"] == "0.10.0"

    # Case B: update-state.json is corrupt JSON while prepared.json has 0.10.0-rc1 <= 0.10.0.
    (root / STATE_FILE).write_text("{corrupt-json", encoding="utf-8")
    (root / PREPARED_FILE).write_text(
        json.dumps({"version": "0.10.0-rc1", "sha256": "e" * 64}),
        encoding="utf-8",
    )
    updater.reconcile()

    assert not (root / PREPARED_FILE).exists()
    state_b = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    assert state_b["current"] == "0.10.0"
    assert state_b["previous"] is None
    assert state_b["last_attempt"]["status"] == "updated"
    assert state_b["last_attempt"]["version"] == "0.10.0"


def test_reconcile_preserves_failed_attempt_when_current_not_yet_switched(tmp_path: Path) -> None:
    root = tmp_path / "Orin"
    v_old = _install_version(root, "0.11.6")
    point_current(root, v_old)

    ready_dir = root / f"0.12.1{READY_SUFFIX}"
    ready_runtime = ready_dir / runtime_relative_path()
    ready_runtime.parent.mkdir(parents=True, exist_ok=True)
    ready_runtime.write_bytes(b"orin-0.12.1")

    (root / PREPARED_FILE).write_text(
        json.dumps({"version": "0.12.1", "sha256": "f" * 64}),
        encoding="utf-8",
    )
    (root / STATE_FILE).write_text(
        json.dumps(
            {
                "previous": "0.11.0",
                "current": "0.11.6",
                "last_attempt": {"status": "failed", "version": "0.12.1", "message": "Acesso negado"},
            }
        ),
        encoding="utf-8",
    )

    updater = Updater(versions_root=root, current_version="0.11.6")
    updater.reconcile()

    assert (root / PREPARED_FILE).is_file()
    prepared = updater.prepared_release()
    assert prepared is not None and prepared.version == "0.12.1"
    state = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    assert state["current"] == "0.11.6"
    assert state["last_attempt"]["status"] == "failed"
    assert state["last_attempt"]["version"] == "0.12.1"
    assert state["last_attempt"]["message"] == "Acesso negado"


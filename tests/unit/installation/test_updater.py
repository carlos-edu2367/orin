from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
from urllib.error import HTTPError, URLError

import pytest

from agentos.installation import updater as engine
from agentos.installation.updater import UpdateError, Updater

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the fake runtimes are POSIX shell scripts")

BASE = "https://example.test/releases"


def _runtime_script(version: str, *, fail_when_current: bool = False, output: str | None = None) -> bytes:
    guard = 'case "$0" in *current*) exit 3;; esac\n' if fail_when_current else ""
    return f'#!/bin/sh\n{guard}echo "{output or f"orin {version} (installed)"}"\n'.encode()


def _archive(version: str, *, runtime: bytes | None = None, extra: dict[str, bytes] | None = None, link: bool = False) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        def add(name: str, data: bytes, mode: int = 0o755) -> None:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
        add("resources/runtime/orin", runtime if runtime is not None else _runtime_script(version))
        add("Orin Desktop", b"#!/bin/sh\n")
        for name, data in (extra or {}).items():
            add(name, data)
        if link:
            info = tarfile.TarInfo("evil")
            info.type, info.linkname = tarfile.SYMTYPE, "/etc"
            tar.addfile(info)
    return buffer.getvalue()


class Server:
    """A fake release host: ``release.json`` plus the archive it points at."""

    def __init__(self, version: str, archive: bytes, *, sha: str | None = None, platforms: bool = True, notes: str | None = None) -> None:
        self.archive = archive
        url = f"{BASE}/download/v{version}/Orin-{version}-linux-x64.tar.gz"
        entry = {"archive_url": url, "archive_sha256": sha or hashlib.sha256(archive).hexdigest()}
        manifest = {"version": version, "archive_url": url, "archive_sha256": entry["archive_sha256"], "release_url": f"{BASE}/tag/v{version}"}
        if platforms:
            manifest["platforms"] = {"linux-x64": entry}
        if notes:
            manifest["notes"] = notes
        self.files = {f"{BASE}/latest/download/release.json": json.dumps(manifest).encode(), url: archive}
        self.calls: list[str] = []
        self.failures: list[Exception] = []

    def __call__(self, request, timeout=None):
        self.calls.append(request.full_url)
        if self.failures:
            raise self.failures.pop(0)
        if request.full_url not in self.files:
            raise HTTPError(request.full_url, 404, "not found", None, None)  # type: ignore[arg-type]
        data = self.files[request.full_url]

        class Response(io.BytesIO):
            headers = {"Content-Length": str(len(data))}
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return Response(data)


def _installed(tmp_path: Path, version: str = "0.4.0") -> Path:
    root = tmp_path / "Orin"
    runtime = root / version / "resources" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "orin").write_bytes(_runtime_script(version))
    (runtime / "orin").chmod(0o755)
    (root / "current").symlink_to(root / version)
    return root


def _updater(root: Path, server: Server, events: list, version: str = "0.4.0", **kwargs) -> Updater:
    return Updater(versions_root=root, current_version=version, emit=events.append, base_url=BASE, platform="linux-x64", opener=server, sleep=lambda _: None, **kwargs)


def test_update_downloads_verifies_activates_and_remembers_the_previous_version(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    events: list = []
    result = _updater(root, Server("0.5.0", _archive("0.5.0"), notes="Mais rápido"), events).run()

    assert result.status == "updated" and (result.previous_version, result.version) == ("0.4.0", "0.5.0")
    assert (root / "current").resolve() == (root / "0.5.0").resolve()
    assert (root / "0.4.0").is_dir()
    assert json.loads((root / "update-state.json").read_text())["previous"] == "0.4.0"
    assert not list(root.glob("*.staging")) and not list(root.glob(".download-*"))
    started = [e.step for e in events if e.phase == "start"]
    assert started == ["check", "download", "verify", "extract", "validate", "activate"]
    assert any(e.phase == "progress" and e.step == "download" and e.bytes_total for e in events)
    assert result.notes == "Mais rápido"


def test_already_current_is_a_no_op(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    server = Server("0.4.0", _archive("0.4.0"))
    result = _updater(root, server, []).run()

    assert result.status == "up_to_date"
    assert all("Orin-0.4.0" not in call for call in server.calls)


def test_check_only_reports_without_touching_disk(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    result = _updater(root, Server("0.5.0", _archive("0.5.0")), []).run(check_only=True)

    assert result.status == "available" and result.version == "0.5.0"
    assert not (root / "0.5.0").exists()


def test_hash_mismatch_discards_the_download_and_changes_nothing(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    with pytest.raises(UpdateError) as caught:
        _updater(root, Server("0.5.0", _archive("0.5.0"), sha="0" * 64), []).run()

    assert caught.value.step == "verify"
    assert (root / "current").resolve() == (root / "0.4.0").resolve()
    assert not (root / "0.5.0").exists() and not list(root.glob(".download-*"))


def test_a_version_that_fails_after_activation_is_rolled_back_automatically(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    broken = _archive("0.5.0", runtime=_runtime_script("0.5.0", fail_when_current=True))
    events: list = []
    with pytest.raises(UpdateError) as caught:
        _updater(root, Server("0.5.0", broken), events).run()

    assert caught.value.rolled_back is True
    assert "restaurada" in (caught.value.hint or "")
    assert (root / "current").resolve() == (root / "0.4.0").resolve()
    assert not (root / "0.5.0").exists()
    assert any(e.phase == "rolled_back" for e in events)
    # The old runtime still answers through the pointer users launch from.
    assert os.access(root / "current" / "resources" / "runtime" / "orin", os.X_OK)


def test_a_version_that_fails_before_activation_is_never_activated(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    garbage = _archive("0.5.0", runtime=_runtime_script("0.5.0", output="segfault"))
    with pytest.raises(UpdateError) as caught:
        _updater(root, Server("0.5.0", garbage), []).run()

    assert caught.value.step == "validate" and caught.value.rolled_back is False
    assert (root / "current").resolve() == (root / "0.4.0").resolve()
    assert not (root / "0.5.0").exists() and not list(root.glob("*.staging"))


def test_archive_with_links_or_escaping_paths_is_refused(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    with pytest.raises(UpdateError, match="atalho"):
        _updater(root, Server("0.5.0", _archive("0.5.0", link=True)), []).run()
    with pytest.raises(UpdateError, match="inseguro"):
        _updater(root, Server("0.5.0", _archive("0.5.0", extra={"../escape": b"x"})), []).run()
    assert not (tmp_path / "escape").exists()


def test_manifest_must_point_at_the_official_release_and_this_platform(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    server = Server("0.5.0", _archive("0.5.0"))
    manifest = json.loads(server.files[f"{BASE}/latest/download/release.json"])
    manifest["platforms"]["linux-x64"]["archive_url"] = "https://evil.test/Orin.tar.gz"
    server.files[f"{BASE}/latest/download/release.json"] = json.dumps(manifest).encode()
    with pytest.raises(UpdateError, match="oficial"):
        _updater(root, server, []).run()
    with pytest.raises(UpdateError, match="pacote para este sistema"):
        _updater(root, Server("0.5.0", _archive("0.5.0"), platforms=False), []).run()


def test_network_hiccups_are_retried_and_a_missing_release_is_not(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    flaky = Server("0.5.0", _archive("0.5.0"))
    flaky.failures = [URLError("boom"), URLError("boom")]
    assert _updater(root, flaky, []).run().status == "updated"

    offline = Server("0.6.0", _archive("0.6.0"))
    offline.failures = [URLError("boom")] * 3
    with pytest.raises(UpdateError, match="conexão"):
        _updater(_installed(tmp_path / "b"), offline, []).run()

    with pytest.raises(UpdateError, match="Não encontrei"):
        _updater(_installed(tmp_path / "c"), Server("0.5.0", _archive("0.5.0")), []).run("9.9.9")


def test_not_enough_disk_space_stops_before_writing_the_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _installed(tmp_path)
    monkeypatch.setattr(engine.shutil, "disk_usage", lambda _: type("U", (), {"free": 10})())
    with pytest.raises(UpdateError, match="espaço"):
        _updater(root, Server("0.5.0", _archive("0.5.0")), []).run()
    assert not list(root.glob(".download-*"))


def test_manual_rollback_returns_to_the_replaced_version(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    updater = _updater(root, Server("0.5.0", _archive("0.5.0")), [])
    updater.run()

    result = updater.rollback()

    assert result.status == "rolled_back" and result.version == "0.4.0"
    assert (root / "current").resolve() == (root / "0.4.0").resolve()
    assert updater.previous_version() == "0.5.0"


def test_rollback_without_a_previous_version_explains_itself(tmp_path: Path) -> None:
    with pytest.raises(UpdateError, match="anterior"):
        _updater(_installed(tmp_path), Server("0.5.0", _archive("0.5.0")), []).rollback()


def test_before_activate_runs_after_validation_and_a_failure_there_changes_nothing(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    order: list[str] = []
    def refuse() -> None:
        order.append("before_activate")
        raise UpdateError("não consegui encerrar", step="activate")
    with pytest.raises(UpdateError):
        _updater(root, Server("0.5.0", _archive("0.5.0")), [], before_activate=refuse).run()

    assert order == ["before_activate"]
    assert (root / "current").resolve() == (root / "0.4.0").resolve() and not (root / "0.5.0").exists()


def test_version_ordering_ranks_prereleases_below_releases() -> None:
    assert engine.version_key("0.5.0") > engine.version_key("0.5.0-rc1") > engine.version_key("0.4.9")


def test_prepare_leaves_a_verified_ready_version_and_touches_nothing_live(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    updater = _updater(root, Server("0.5.0", _archive("0.5.0"), notes="n"), [])

    result = updater.prepare()

    assert result.status == "ready" and result.downloaded_bytes > 0
    assert (root / "current").resolve() == (root / "0.4.0").resolve()
    assert (root / "0.5.0.ready").is_dir() and not (root / "0.5.0").exists()
    prepared = updater.prepared_release()
    assert prepared is not None and (prepared.version, prepared.notes) == ("0.5.0", "n")


def test_a_prepared_release_is_reused_without_downloading_again(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    server = Server("0.5.0", _archive("0.5.0"))
    _updater(root, server, []).prepare()
    server.calls.clear()

    assert _updater(root, server, []).prepare().status == "ready"
    assert all("tar.gz" not in call for call in server.calls)


def test_apply_prepared_activates_it_and_records_the_attempt(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    updater = _updater(root, Server("0.5.0", _archive("0.5.0")), [])
    updater.prepare()

    result = updater.apply_prepared()

    assert (result.status, result.version) == ("updated", "0.5.0")
    assert (root / "current").resolve() == (root / "0.5.0").resolve()
    assert not (root / "0.5.0.ready").exists() and not (root / "prepared.json").exists()
    assert updater.last_attempt()["status"] == "updated"
    assert updater.previous_version() == "0.4.0"


def test_apply_without_a_prepared_update_explains_itself(tmp_path: Path) -> None:
    with pytest.raises(UpdateError, match="preparada"):
        _updater(_installed(tmp_path), Server("0.5.0", _archive("0.5.0")), []).apply_prepared()


def test_a_prepared_version_that_fails_after_activation_rolls_back_and_is_recorded(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    updater = _updater(root, Server("0.5.0", _archive("0.5.0", runtime=_runtime_script("0.5.0", fail_when_current=True))), [])
    updater.prepare()

    with pytest.raises(UpdateError) as caught:
        updater.apply_prepared()

    assert caught.value.rolled_back
    assert (root / "current").resolve() == (root / "0.4.0").resolve()
    attempt = updater.last_attempt()
    assert attempt["status"] == "rolled_back" and attempt["version"] == "0.5.0" and attempt["restored"] == "0.4.0"
    assert updater.prepared_release() is None


def test_failing_to_stop_orin_keeps_the_prepared_update_for_a_retry(tmp_path: Path) -> None:
    root = _installed(tmp_path)
    calls = {"n": 0}
    def stop() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise UpdateError("não consegui encerrar", step="activate")
    updater = _updater(root, Server("0.5.0", _archive("0.5.0")), [], before_activate=stop)
    updater.prepare()

    with pytest.raises(UpdateError):
        updater.apply_prepared()
    assert updater.prepared_release() is not None

    assert updater.apply_prepared().status == "updated"

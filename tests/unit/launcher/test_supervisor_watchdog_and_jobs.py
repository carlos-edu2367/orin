from __future__ import annotations

import ctypes
import io
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import insert

from agentos.installation import OrinPaths, RuntimeProfile
from agentos.launcher.desktop import launch_desktop
from agentos.launcher.desktop_status import DesktopStatusWriter
from agentos.launcher.ports import PortChoice
from agentos.launcher.probes import ProbeResult, heartbeat_probe
from agentos.launcher.processes import ProcessGroup
from agentos.launcher.state import request_stop
from agentos.launcher.supervisor import (
    BACKEND_WATCHDOG_INTERVAL,
    LaunchOptions,
    Supervisor,
)
from agentos.launcher.ui import Console
from agentos.persistence.postgres.migrate import upgrade
from agentos.persistence.postgres.schema import runtime_heartbeats
from agentos.persistence.sqlite import create_local_engine


def _make_paths_and_profile(tmp_path: Path) -> tuple[OrinPaths, RuntimeProfile]:
    paths = OrinPaths(
        tmp_path / "config",
        tmp_path / "data",
        tmp_path / "logs",
        tmp_path / "cache",
        tmp_path / "run",
    ).ensure()
    (tmp_path / "desktop").mkdir(exist_ok=True)
    profile = RuntimeProfile(kind="development", root=tmp_path, repository=tmp_path, version="0.12.28")
    return paths, profile


def test_launch_desktop_closes_parent_log_handle(tmp_path: Path) -> None:
    paths, profile = _make_paths_and_profile(tmp_path)
    exe = tmp_path / "OrinFakeElectron.exe"
    exe.write_bytes(b"MZ")
    status = DesktopStatusWriter(paths, restart_command=("orin",))
    opened_files: list[object] = []
    real_open = open

    def tracking_open(*args, **kwargs):
        fh = real_open(*args, **kwargs)
        opened_files.append(fh)
        return fh

    fake_proc = MagicMock()
    with (
        patch.dict("os.environ", {"ORIN_ELECTRON_EXECUTABLE": str(exe)}),
        patch("builtins.open", side_effect=tracking_open),
        patch("subprocess.Popen", return_value=fake_proc),
    ):
        launch_desktop(paths, profile, status)

    assert len(opened_files) == 1
    assert opened_files[0].closed is True  # type: ignore[attr-defined]


def test_supervisor_restarts_hung_backend_in_place_preserving_shutdown_order(tmp_path: Path) -> None:
    paths, profile = _make_paths_and_profile(tmp_path)
    console = Console(io.StringIO(), quiet=True, colour=False)
    sup = Supervisor(paths, profile, console, options=LaunchOptions(open_browser=False))
    sup.choice = PortChoice(port=49200, requested=49200, moved=False)
    sup.environment = MagicMock()
    sup.environment.for_port.return_value = {}
    sup.environment.database_url = f"sqlite:///{tmp_path / 'test.db'}"

    old_backend = MagicMock(name="old_backend")
    old_backend.name = "backend"
    old_backend.pid = 1111
    old_backend.exited.return_value = None

    worker = MagicMock(name="worker")
    worker.name = "worker"
    worker.pid = 2222
    worker.exited.return_value = None

    scheduler = MagicMock(name="scheduler")
    scheduler.name = "scheduler"
    scheduler.pid = 2223
    scheduler.exited.return_value = None

    new_backend = MagicMock(name="new_backend")
    new_backend.name = "backend"
    new_backend.pid = 3333
    new_backend.exited.return_value = None

    sup.children = [old_backend, worker, scheduler]

    def fake_spawn(svc: str, *, replace_at: int | None = None):
        assert svc == "backend"
        assert replace_at == 0
        sup.children[replace_at] = new_backend
        return new_backend

    with (
        patch.object(sup, "_spawn", side_effect=fake_spawn),
        patch("agentos.launcher.supervisor.http_probe", return_value=ProbeResult(True, "ok")),
    ):
        ok = sup._restart_child("backend", "healthz timed out 3 times")

    assert ok is True
    old_backend.stop.assert_called_once()
    worker.stop.assert_not_called()
    scheduler.stop.assert_not_called()
    # Order MUST remain [backend, worker, scheduler] so _stop_children stops backend LAST!
    assert [c.name for c in sup.children] == ["backend", "worker", "scheduler"]
    assert sup.children[0] is new_backend


def test_supervisor_circuit_breaker_trips_on_fourth_attempt_and_heartbeat_requires_fresh_timestamp(
    tmp_path: Path,
) -> None:
    paths, profile = _make_paths_and_profile(tmp_path)
    console = Console(io.StringIO(), quiet=True, colour=False)
    sup = Supervisor(paths, profile, console, options=LaunchOptions(open_browser=False))
    sup.choice = PortChoice(port=49200, requested=49200, moved=False)
    dsn = f"sqlite:///{tmp_path / 'hb.db'}"
    eng = create_local_engine(dsn)
    upgrade(eng)
    stale_ts = datetime.now(UTC) - timedelta(seconds=2)
    with eng.begin() as c:
        c.execute(
            insert(runtime_heartbeats).values(
                component="chat-worker",
                updated_at=stale_ts,
            )
        )
    eng.dispose()

    # Stale heartbeat from 2s BEFORE restart must fail minimum_updated_at check!
    restart_ts = datetime.now(UTC)
    probe_res = heartbeat_probe(dsn, ("chat-worker",), minimum_updated_at=restart_ts)
    assert probe_res.ready is False
    assert probe_res.ok is False

    sup.environment = MagicMock()
    sup.environment.for_port.return_value = {}
    sup.environment.database_url = dsn

    def make_child() -> MagicMock:
        c = MagicMock()
        c.name = "backend"
        c.exited.return_value = None
        return c

    sup.children = [make_child()]

    def replace_spawn(_s: str, *, replace_at: int | None = None):
        c = make_child()
        if replace_at is not None:
            sup.children[replace_at] = c
        else:
            sup.children.append(c)
        return c

    with (
        patch.object(sup, "_spawn", side_effect=replace_spawn),
        patch("agentos.launcher.supervisor.http_probe", return_value=ProbeResult(True, "ok")),
    ):
        assert sup._restart_child("backend", "hang 1") is True
        assert sup._restart_child("backend", "hang 2") is True
        assert sup._restart_child("backend", "hang 3") is True
        # 4th attempt inside 120s window trips circuit breaker
        assert sup._restart_child("backend", "hang 4") is False


def test_supervisor_supervise_probes_healthz_every_5s_and_clean_stop_exits_zero(tmp_path: Path) -> None:
    paths, profile = _make_paths_and_profile(tmp_path)
    console = Console(io.StringIO(), quiet=True, colour=False)
    sup = Supervisor(paths, profile, console, options=LaunchOptions(open_browser=False))
    sup.choice = PortChoice(port=49200, requested=49200, moved=False)
    sup.environment = MagicMock()
    sup.environment.for_port.return_value = {}
    sup.environment.database_url = f"sqlite:///{tmp_path / 'sup.db'}"

    backend = MagicMock()
    backend.name = "backend"
    backend.pid = 1001
    backend.exited.return_value = None

    worker = MagicMock()
    worker.name = "worker"
    worker.pid = 1002
    worker.exited.return_value = None

    sup.children = [backend, worker]

    fake_clock = [100.0]
    probe_times: list[float] = []
    restarts: list[tuple[str, str]] = []

    def fake_http_probe(url: str, *, timeout: float = 2.0, expect=None) -> ProbeResult:
        probe_times.append(fake_clock[0])
        return ProbeResult(False, "timed out")

    def fake_restart(service: str, reason: str) -> bool:
        restarts.append((service, reason))
        sup._backend_health_failures = 0
        sup._backend_next_health_check = fake_clock[0] + BACKEND_WATCHDOG_INTERVAL
        # Request clean stop after first auto-heal
        request_stop(paths)
        return True

    def fake_wait(seconds: float) -> bool:
        fake_clock[0] += 1.0
        return sup._stop.is_set()

    lock = MagicMock()
    with (
        patch("agentos.launcher.supervisor.monotonic", side_effect=lambda: fake_clock[0]),
        patch("agentos.launcher.supervisor.http_probe", side_effect=fake_http_probe),
        patch.object(sup, "_restart_child", side_effect=fake_restart),
        patch.object(sup._stop, "wait", side_effect=fake_wait),
        patch.object(sup, "shutdown"),
    ):
        exit_code = sup._supervise(lock)

    assert exit_code == 0
    # Probes must happen strictly at t=105, t=110, t=115 (5.0s spacing, never 0.5s/1.0s)
    assert probe_times == [105.0, 110.0, 115.0]
    assert len(restarts) == 1
    assert restarts[0][0] == "backend"
    # Living worker is NEVER restarted
    worker.stop.assert_not_called()


def test_supervisor_clean_stop_during_dead_child_restart_exits_zero_without_desktop_failed(tmp_path: Path) -> None:
    paths, profile = _make_paths_and_profile(tmp_path)
    console = Console(io.StringIO(), quiet=True, colour=False)
    sup = Supervisor(paths, profile, console, options=LaunchOptions(open_browser=False))
    sup.choice = PortChoice(port=49200, requested=49200, moved=False)
    sup.desktop_status = DesktopStatusWriter(paths, restart_command=("orin",))
    sup.desktop_status.ready("http://127.0.0.1:49200")

    dead_backend = MagicMock()
    dead_backend.name = "backend"
    dead_backend.pid = 1001
    dead_backend.exited.return_value = 1
    sup.children = [dead_backend]

    def stop_on_restart(_service: str, _reason: str) -> bool:
        request_stop(paths)
        return False

    lock = MagicMock()
    with (
        patch.object(sup, "_restart_child", side_effect=stop_on_restart),
        patch.object(sup, "shutdown"),
    ):
        exit_code = sup._supervise(lock)

    assert exit_code == 0
    assert sup.desktop_status.snapshot.mode != "error"


def test_supervisor_prepare_sweeps_stale_workspace_locks(tmp_path: Path) -> None:
    paths, profile = _make_paths_and_profile(tmp_path)
    lock_dir = paths.run / "workspace-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    stale_turn = lock_dir / "deadbeef.turn.99999999.1234"
    stale_turn.write_text("99999999 111", encoding="utf-8")
    keep_lock = lock_dir / "orin.lock"
    keep_lock.write_text("1234", encoding="utf-8")

    console = Console(io.StringIO(), quiet=True, colour=False)
    sup = Supervisor(paths, profile, console, options=LaunchOptions(open_browser=False))
    with (
        patch("agentos.launcher.supervisor.load_environment") as mock_env,
        patch("agentos.launcher.supervisor.select_port", return_value=PortChoice(port=49200, requested=49200, moved=False)),
    ):
        mock_env.return_value = MagicMock(created_configuration=None, sources=())
        sup._prepare()

    assert not stale_turn.exists()
    assert keep_lock.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows 64-bit Job Object layout test")
def test_win64_job_object_struct_size_and_breakaway_flags() -> None:
    from agentos.launcher.processes import JOBOBJECT_EXTENDED_LIMIT_INFORMATION

    if sys.maxsize > 2**32:
        assert ctypes.sizeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION) == 144

    g_root = ProcessGroup(allow_breakaway=True)
    g_strict = ProcessGroup(allow_breakaway=False)
    try:
        assert g_root._job is not None
        assert g_strict._job is not None
    finally:
        g_root.close()
        g_strict.close()
    assert g_root._job is None
    assert g_strict._job is None


@pytest.mark.anyio
async def test_backend_shutdown_preserves_omniroute_during_supervisor_auto_heal_and_stops_on_full_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentos.bootstrap.production as prod_mod
    from agentos.launcher.state import InstanceLock, stop_requested

    paths, profile = _make_paths_and_profile(tmp_path)
    monkeypatch.setattr(prod_mod, "orin_paths", lambda: paths)

    # 1. Verify Supervisor.shutdown() sets request_stop(self.paths) BEFORE stopping children
    console = Console(io.StringIO(), quiet=True, colour=False)
    sup = Supervisor(paths, profile, console, options=LaunchOptions(open_browser=False))
    observed_stop_during_child_stop: list[bool] = []

    fake_backend = MagicMock()
    fake_backend.name = "backend"
    fake_backend.pid = 4321
    fake_backend.process = MagicMock()
    fake_backend.stop.side_effect = lambda: observed_stop_during_child_stop.append(stop_requested(paths))
    sup.children = [fake_backend]
    sup.shutdown()
    assert observed_stop_during_child_stop == [True]
    # And after shutdown finishes, stop_requested is cleared
    assert stop_requested(paths) is False

    # 2. Verify Supervisor._rollback() also sets request_stop(self.paths) during child stop and clears it after
    sup2 = Supervisor(paths, profile, console, options=LaunchOptions(open_browser=False))
    observed_rollback_stop: list[bool] = []
    fake_backend_rb = MagicMock()
    fake_backend_rb.name = "backend"
    fake_backend_rb.pid = 4322
    fake_backend_rb.process = MagicMock()
    fake_backend_rb.stop.side_effect = lambda: observed_rollback_stop.append(stop_requested(paths))
    sup2.children = [fake_backend_rb]
    sup2._rollback()
    assert observed_rollback_stop == [True]
    assert stop_requested(paths) is False

    # 3. Verify production.py _stop_owned_omniroute skips manager.stop() when InstanceLock is held
    #    and stop_requested(paths) is False (isolated backend auto-heal), but calls manager.stop()
    #    when stop_requested(paths) is True or when InstanceLock is free (standalone uvicorn).
    dsn = f"sqlite:///{tmp_path / 'prod_omni.db'}"
    settings = prod_mod.ProductionSettings(
        DATABASE_URL=dsn,
        AGENTOS_ENV="development",
        LOCALHOST_TRUST_ENABLED=False,
    )
    fake_manager = MagicMock()
    services = prod_mod.unavailable_production_services()
    services.omniroute_runtime = fake_manager
    app = prod_mod.create_production_app(settings, services=services, probe=MagicMock())

    shutdown_handlers = list(app.router.on_shutdown)
    assert len(shutdown_handlers) >= 1

    with InstanceLock(paths.instance_lock) as lock:
        assert lock.acquire() is True
        # Case A: Supervisor lock held, stop_requested is False (isolated backend restart)
        for handler in shutdown_handlers:
            await handler()
        fake_manager.stop.assert_not_called()

        # Case B: Supervisor lock held, stop_requested is True (full Supervisor shutdown)
        request_stop(paths)
        for handler in shutdown_handlers:
            await handler()
        fake_manager.stop.assert_called_once()

    # Case C: Standalone Uvicorn (InstanceLock is free, stop_requested is False)
    from agentos.launcher.state import clear_stop_request

    clear_stop_request(paths)
    fake_manager.stop.reset_mock()
    for handler in shutdown_handlers:
        await handler()
    fake_manager.stop.assert_called_once()


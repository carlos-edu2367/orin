from __future__ import annotations

import asyncio
import gc
import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from agentos.api import ApiServices, AuthenticatedPrincipal, InMemorySecurityService, create_app
from agentos.bootstrap.production import DependencyProbe, ProductionSettings, create_production_app
from agentos.omniroute.process_manager import OmniRouteProcessManager, OmniRouteRuntimeSettingsStore
from agentos.provider_catalog.models import ProviderCatalogContext, ProviderModelRecord
from agentos.provider_catalog.opencode import OpenCodeBridgeProcessManager
from agentos.provider_catalog.service import ProviderModelCatalogService

SLOW_SECONDS = 0.8


def _headers(key: str = "k-1") -> dict[str, str]:
    return {"Authorization": "Bearer pat", "Idempotency-Key": key}


async def _measure_fast_request_under_slow_load(app, slow_call, fast_call, base_url: str = "http://test") -> float:
    transport = ASGITransport(app=app)
    gc.collect()
    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        started = time.monotonic()
        async with AsyncClient(transport=transport, base_url=base_url) as client:
            slow = asyncio.ensure_future(slow_call(client))
            await asyncio.sleep(SLOW_SECONDS / 4)
            fast_response = await fast_call(client)
            assert fast_response.status_code < 500
            fast_elapsed = time.monotonic() - started
            await slow
            return fast_elapsed
    finally:
        if gc_was_enabled:
            gc.enable()


def test_dependency_probe_reuses_engine_thread_safely_and_recovers_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    created_engines: list[Any] = []
    disposed_engines: list[Any] = []
    should_fail = False
    barrier = threading.Barrier(8)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement):
            if should_fail:
                raise RuntimeError("db unavailable")
            return 1

    class FakeEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            disposed_engines.append(self)

    def fake_create_local_engine(url: str):
        time.sleep(0.02)
        eng = FakeEngine()
        created_engines.append(eng)
        return eng

    monkeypatch.setattr("agentos.bootstrap.production.create_local_engine", fake_create_local_engine)
    settings = ProductionSettings(DATABASE_URL=f"sqlite:///{(tmp_path / 'probe.db').as_posix()}")
    probe = DependencyProbe.from_settings(settings)

    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait(timeout=5.0)
        ok = probe.ready()
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert results == [True] * 8
    assert len(created_engines) == 1
    assert len(disposed_engines) == 0

    # Now simulate a transient failure: the shared engine must be disposed and recreated on next call.
    should_fail = True
    assert probe.ready() is False
    assert len(disposed_engines) == 1

    should_fail = False
    assert probe.ready() is True
    assert len(created_engines) == 2

    # Also verify DependencyProbe.from_engine reuses an existing Engine without disposing it.
    explicit_engine = FakeEngine()
    engine_probe = DependencyProbe.from_engine(explicit_engine)  # type: ignore[arg-type]
    assert engine_probe.ready() is True
    assert explicit_engine not in disposed_engines


def test_readyz_runs_dependency_probe_in_threadpool_without_blocking_event_loop(tmp_path: Path) -> None:
    def slow_postgres() -> bool:
        time.sleep(SLOW_SECONDS)
        return True

    settings = ProductionSettings(DATABASE_URL=f"sqlite:///{(tmp_path / 'readyz.db').as_posix()}")
    app = create_production_app(settings, probe=DependencyProbe(postgres=slow_postgres))

    async def slow_call(client: AsyncClient):
        return await client.get("/readyz")

    async def fast_call(client: AsyncClient):
        return await client.get("/healthz")

    fast_elapsed = asyncio.run(_measure_fast_request_under_slow_load(app, slow_call, fast_call))
    assert fast_elapsed < SLOW_SECONDS / 2, f"/readyz blocked the event loop ({fast_elapsed:.3f}s)"


def test_omniroute_readopts_owned_process_from_owner_file_and_rejects_reused_pid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = OmniRouteRuntimeSettingsStore(tmp_path / "runtime.json")
    owner_file = tmp_path / "omniroute-owner.json"

    class FakeProc:
        pid = 424242

        def poll(self):
            return None

        def terminate(self):
            pass

    killed_pids: list[tuple[int, object]] = []
    exact_filetime = 133761000000000001  # > 2^53: would lose precision if coerced to float!

    monkeypatch.setattr(
        "agentos.omniroute.process_manager.process_created",
        lambda pid: exact_filetime if pid == 424242 else None,
        raising=False,
    )
    monkeypatch.setattr(
        "agentos.omniroute.process_manager.same_process",
        lambda pid, created: pid == 424242 and created == exact_filetime,
        raising=False,
    )

    def fake_kill_verified(pid: int, created: object) -> bool:
        if pid == 424242 and created == exact_filetime:
            killed_pids.append((pid, created))
            return True
        return False

    monkeypatch.setattr("agentos.omniroute.process_manager.kill_verified", fake_kill_verified, raising=False)

    manager1 = OmniRouteProcessManager(
        settings,
        command=("omniroute",),
        health=lambda: False,
        start_process=lambda cmd: FakeProc(),
        wait_ready=lambda: True,
        owner_file=owner_file,
    )
    started = manager1.start()
    assert started == {"state": "ready", "ownership": "agentos"}
    assert owner_file.is_file()
    owner_payload = json.loads(owner_file.read_text(encoding="utf-8"))
    assert owner_payload == {"pid": 424242, "created": exact_filetime}
    assert isinstance(owner_payload["created"], int)

    # Simulate backend restart: a fresh manager instance with self._process = None re-adopts via owner_file.
    manager2 = OmniRouteProcessManager(
        settings,
        command=("omniroute",),
        health=lambda: True,
        start_process=lambda cmd: pytest.fail("should not spawn when already owned and healthy"),
        wait_ready=lambda: True,
        owner_file=owner_file,
    )
    assert manager2.status() == {"state": "ready", "ownership": "agentos"}
    stopped = manager2.stop()
    assert stopped == {"state": "stopped", "ownership": None}
    assert killed_pids == [(424242, exact_filetime)]
    assert not owner_file.exists()

    # Adversarial PID reuse check: if owner_file has a stale creation timestamp, manager must NOT claim or kill it.
    owner_file.write_text(json.dumps({"pid": 424242, "created": exact_filetime - 1}), encoding="utf-8")
    manager3 = OmniRouteProcessManager(
        settings,
        command=("omniroute",),
        health=lambda: True,
        start_process=lambda cmd: FakeProc(),
        wait_ready=lambda: True,
        owner_file=owner_file,
    )
    assert manager3.status() == {"state": "ready", "ownership": "external"}
    assert manager3.stop() == {"state": "external", "ownership": "external"}
    assert len(killed_pids) == 1  # No extra kill call!

    # Adversarial corrupt owner_file & bool-as-int check: must not crash status()/stop() and should be cleaned up.
    for bad_content in ("{corrupt json", json.dumps({"pid": True, "created": True}), json.dumps({"pid": 424242, "created": ""})):
        owner_file.write_text(bad_content, encoding="utf-8")
        manager4 = OmniRouteProcessManager(
            settings,
            command=("omniroute",),
            health=lambda: False,
            start_process=lambda cmd: FakeProc(),
            wait_ready=lambda: True,
            owner_file=owner_file,
        )
        assert manager4.status() == {"state": "stopped", "ownership": None}
        assert not owner_file.exists()


def test_omniroute_status_does_not_wait_for_in_flight_start(tmp_path: Path) -> None:
    settings = OmniRouteRuntimeSettingsStore(tmp_path / "runtime.json")
    owner_file = tmp_path / "omniroute-owner.json"
    wait_entered = threading.Event()
    release_wait = threading.Event()

    class FakeProc:
        pid = 515151

        def poll(self):
            return None

        def terminate(self):
            pass

    def slow_wait_ready() -> bool:
        wait_entered.set()
        release_wait.wait(timeout=5.0)
        return True

    manager = OmniRouteProcessManager(
        settings,
        command=("omniroute",),
        health=lambda: False,
        start_process=lambda cmd: FakeProc(),
        wait_ready=slow_wait_ready,
        owner_file=owner_file,
    )

    start_thread = threading.Thread(target=manager.start)
    start_thread.start()
    try:
        assert wait_entered.wait(timeout=2.0), "start() did not enter wait_ready()"
        t0 = time.monotonic()
        status = manager.status()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.15, f"status() blocked on start() lock ({elapsed:.3f}s)"
        assert status == {"state": "starting", "ownership": "agentos"}
    finally:
        release_wait.set()
        start_thread.join(timeout=2.0)


def test_provider_catalog_skips_opencode_auto_refresh_when_cached_and_backs_off_failures() -> None:
    refresh_calls = 0

    class FailingOpenCodeUpstream:
        def fetch(self, api_key: str | None = None, *, base_url: str | None = None):
            nonlocal refresh_calls
            refresh_calls += 1
            raise RuntimeError("bridge offline")

    cached_items: list[ProviderModelRecord] = []

    class FakeRepo:
        def credential(self, context: ProviderCatalogContext, provider: str):
            return {"enabled": True, "api_key": "", "base_url": None, "updated_at": None}

        def list(self, context: ProviderCatalogContext, provider: str, favorites_only: bool = False):
            return list(cached_items)

        def replace_discovered(self, context: ProviderCatalogContext, provider: str, entries, *, base_url=None):
            return list(entries)

    repo = FakeRepo()
    service = ProviderModelCatalogService(repo, {"opencode": FailingOpenCodeUpstream()})
    ctx = ProviderCatalogContext(user_id="user-1", purpose="test")

    # 1. When cached_items is non-empty, list() must NOT auto-refresh opencode.
    cached_items.append(
        ProviderModelRecord(
            provider="opencode",
            model_id="opencode/gpt-5",
            display_name="GPT-5",
            context_window=128000,
            capabilities=(),
            input_modalities=("text",),
            output_modalities=("text",),
            pricing=None,
            refreshed_at=datetime.now(UTC),
        )
    )
    listed = service.list(ctx, "opencode")
    assert len(listed) == 1
    assert refresh_calls == 0

    # 2. When cached_items is empty, list() attempts auto-refresh once and then backs off for 60s on failure.
    cached_items.clear()
    assert service.list(ctx, "opencode") == []
    assert refresh_calls == 1

    # Immediate subsequent list() calls within the 60s window must not retry the failing upstream.
    assert service.list(ctx, "opencode") == []
    assert service.list(ctx, "opencode") == []
    assert refresh_calls == 1

    # 3. favorites_only=True must never trigger auto-refresh even if backoff is cleared.
    service._auto_refresh_backoff.clear()
    assert service.list(ctx, "opencode", favorites_only=True) == []
    assert refresh_calls == 1


def test_opencode_ensure_running_kills_and_cleans_up_process_on_startup_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminated: list[int] = []

    class HungProcess:
        pid = 636363

        def __init__(self) -> None:
            self._returncode: int | None = None

        def poll(self) -> int | None:
            return self._returncode

        def terminate(self) -> None:
            terminated.append(self.pid)
            self._returncode = -15

        def wait(self, timeout: float | None = None) -> int:
            return -15

        def kill(self) -> None:
            terminated.append(self.pid)
            self._returncode = -9

    monkeypatch.setattr("agentos.provider_catalog.opencode.shutil.which", lambda name: "opencode")
    monkeypatch.setattr("agentos.provider_catalog.opencode.subprocess.Popen", lambda *a, **kw: HungProcess())
    monkeypatch.setattr("agentos.provider_catalog.opencode.time.sleep", lambda s: None)

    clock = {"now": 100.0}

    def fake_monotonic() -> float:
        clock["now"] += 5.0
        return clock["now"]

    monkeypatch.setattr("agentos.provider_catalog.opencode.time.monotonic", fake_monotonic)

    manager = OpenCodeBridgeProcessManager()
    monkeypatch.setattr(manager, "_is_healthy", lambda url: False)

    with pytest.raises(RuntimeError, match="OpenCode local bridge could not be started"):
        manager.ensure_running("http://127.0.0.1:43111")

    assert 43111 not in manager._processes
    assert terminated, "timed-out OpenCode bridge process was not terminated"


def test_slow_conversation_and_settings_and_rate_limit_do_not_block_fast_request(tmp_path: Path) -> None:
    class SlowRateLimitSecurity(InMemorySecurityService):
        def check_rate_limit(self, principal, *, action: str = "", origin: str | None = None) -> None:
            if action == "conversations.list":
                time.sleep(SLOW_SECONDS)
            super().check_rate_limit(principal, action=action, origin=origin)

    class FakeConversations:
        def list(self, user_id: str, project_id: str | None = None):
            return []

        def get(self, conversation_id: str, user_id: str):
            if conversation_id == "conv-slow":
                time.sleep(SLOW_SECONDS)
            return None

    class FakePlugins:
        def list(self, user_id: str):
            return []

    class SlowOmniRoute:
        def status(self):
            time.sleep(SLOW_SECONDS)
            return {"state": "stopped", "ownership": None}

        def auto_start(self, user_id: str) -> bool:
            return False

    security = SlowRateLimitSecurity()
    security.add_pat("pat", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    app = create_app(
        ApiServices(
            security=security,
            conversation_application=FakeConversations(),  # type: ignore[arg-type]
            plugins=FakePlugins(),  # type: ignore[arg-type]
            omniroute_runtime=SlowOmniRoute(),  # type: ignore[arg-type]
        )
    )

    # 1. Slow rate-limit / conversation read vs fast /v1/plugins
    async def slow_conv_list(client: AsyncClient):
        return await client.get("/v1/conversations", headers=_headers())

    async def fast_plugins(client: AsyncClient):
        return await client.get("/v1/plugins", headers=_headers())

    elapsed1 = asyncio.run(_measure_fast_request_under_slow_load(app, slow_conv_list, fast_plugins))
    assert elapsed1 < SLOW_SECONDS / 2, f"slow check_rate_limit/list_conversations blocked event loop ({elapsed1:.3f}s)"

    # 2. Slow omniroute status vs fast /v1/plugins
    async def slow_omniroute(client: AsyncClient):
        return await client.get("/v1/providers/omniroute/runtime", headers=_headers())

    elapsed2 = asyncio.run(_measure_fast_request_under_slow_load(app, slow_omniroute, fast_plugins))
    assert elapsed2 < SLOW_SECONDS / 2, f"slow omniroute_runtime_status blocked event loop ({elapsed2:.3f}s)"

    # 3. Slow settings/effective folder_of lookup vs fast /v1/plugins
    async def slow_settings_effective(client: AsyncClient):
        return await client.get("/v1/settings/effective?conversation_id=conv-slow", headers=_headers())

    elapsed3 = asyncio.run(_measure_fast_request_under_slow_load(app, slow_settings_effective, fast_plugins))
    assert elapsed3 < SLOW_SECONDS / 2, f"slow settings/effective blocked event loop ({elapsed3:.3f}s)"

    # 4. Slow SSE conversation_events pre-stream DB check vs fast /v1/plugins
    async def slow_sse_precheck(client: AsyncClient):
        return await client.get("/v1/conversations/conv-slow/events", headers=_headers())

    elapsed4 = asyncio.run(_measure_fast_request_under_slow_load(app, slow_sse_precheck, fast_plugins))
    assert elapsed4 < SLOW_SECONDS / 2, f"slow conversation_events SSE precheck blocked event loop ({elapsed4:.3f}s)"


def test_slow_rewind_conversation_turn_runs_in_worker_thread_without_blocking_event_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agentos.agentic.workspace import ConversationWorkspace
    from agentos.code_mode.file_history import FileHistory

    main_thread_id = threading.get_ident()
    rewind_thread_ids: list[int] = []

    class SlowRewindConversations:
        def get(self, conversation_id: str, user_id: str) -> dict[str, object]:
            rewind_thread_ids.append(threading.get_ident())
            time.sleep(SLOW_SECONDS)
            return {
                "conversation_id": conversation_id,
                "title": "T",
                "state": "idle",
                "provider": "openai",
                "model_id": "m",
                "messages": [],
                "turns": [],
                "activities": [],
                "activity_cursor": "0",
            }

        def fork(self, conversation_id: str, user_id: str, *, before_message_id=None, title=None) -> dict[str, object]:
            return {
                "conversation_id": "conv-fork",
                "title": "T (ramo)",
                "prompt": "p",
                "messages": 1,
                "project_id": None,
            }

    class FakePlugins:
        def list(self, user_id: str):
            return []

    security = InMemorySecurityService()
    security.add_pat("pat", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    app = create_app(
        ApiServices(
            security=security,
            conversation_application=SlowRewindConversations(),  # type: ignore[arg-type]
            plugins=FakePlugins(),  # type: ignore[arg-type]
            workspace_root=tmp_path / "workspaces",
            file_history_root=tmp_path / "history",
        )
    )

    workspace = ConversationWorkspace(tmp_path / "workspaces", "conv-rewind")
    target = workspace.root / "app.py"
    target.write_bytes(b"original\n")
    history = FileHistory(tmp_path / "history", "conv-rewind")
    history.snapshot_before_write("um-1", target, turn_id="turn-1")
    target.write_bytes(b"modified\n")

    async def slow_rewind(c: AsyncClient):
        return await c.post(
            "/v1/conversations/conv-rewind/messages/um-1/rewind",
            headers=_headers("c-rewind"),
            json={"mode": "code"},
        )

    async def fast_call(c: AsyncClient):
        return await c.get("/v1/plugins", headers=_headers("c-fast"))

    fast_elapsed = asyncio.run(_measure_fast_request_under_slow_load(app, slow_rewind, fast_call))
    assert fast_elapsed < SLOW_SECONDS / 2, f"rewind_conversation_message blocked event loop ({fast_elapsed:.3f}s)"
    assert rewind_thread_ids, "rewind was not executed"
    assert all(tid != main_thread_id for tid in rewind_thread_ids), "rewind lock/check ran on the event-loop thread!"


def test_omniroute_generation_guard_and_toctou_probe_and_stub_settings_isolation(tmp_path: Path) -> None:
    # 1C: Stub settings (not OmniRouteRuntimeSettingsStore) with owner_file=None must NOT touch live Orin run dir.
    class StubSettings:
        def auto_start(self, user_id: str) -> bool:
            return False

        def set_auto_start(self, user_id: str, value: bool) -> bool:
            return value

        def any_enabled(self) -> bool:
            return False

    stub_mgr = OmniRouteProcessManager(
        StubSettings(),  # type: ignore[arg-type]
        command=("omniroute",),
        health=lambda: False,
        start_process=lambda cmd: None,  # type: ignore[return-value]
        wait_ready=lambda: False,
    )
    assert stub_mgr._owner_file is None

    # 1A: Concurrent stop() while start() is inside _wait_ready() must NOT mark _last_failure or return "failed"/"ready".
    settings = OmniRouteRuntimeSettingsStore(tmp_path / "runtime.json")
    owner_file = tmp_path / "omniroute-owner.json"
    wait_entered = threading.Event()
    release_wait = threading.Event()

    class StoppableProc:
        pid = 717171

        def __init__(self) -> None:
            self._rc: int | None = None

        def poll(self) -> int | None:
            return self._rc

        def terminate(self) -> None:
            self._rc = 0

    proc_instance = StoppableProc()

    def wait_then_return_false() -> bool:
        wait_entered.set()
        release_wait.wait(timeout=5.0)
        return False

    mgr = OmniRouteProcessManager(
        settings,
        command=("omniroute",),
        health=lambda: False,
        start_process=lambda cmd: proc_instance,
        wait_ready=wait_then_return_false,
        owner_file=owner_file,
    )

    start_result: dict[str, object] = {}

    def run_start() -> None:
        start_result.update(mgr.start())

    t = threading.Thread(target=run_start)
    t.start()
    assert wait_entered.wait(timeout=2.0)
    # Stop while start() is waiting in _wait_ready()
    stop_res = mgr.stop()
    assert stop_res == {"state": "stopped", "ownership": None}
    release_wait.set()
    t.join(timeout=2.0)

    assert start_result == {"state": "stopped", "ownership": None}
    assert mgr.status() == {"state": "stopped", "ownership": None}

    # 1B: TOCTOU around _healthy(): if an owned process answers _healthy() -> True and dies right before _owned_alive()
    # is checked, _probe_state() must re-probe and report "stopped" (not false "external").
    health_calls = 0
    dying_proc = StoppableProc()

    def dying_health() -> bool:
        nonlocal health_calls
        health_calls += 1
        if health_calls == 1:
            # Process answers the first health check and immediately exits before lock re-acquisition
            dying_proc._rc = 1
            return True
        return False

    toctou_mgr = OmniRouteProcessManager(
        settings,
        command=("omniroute",),
        health=dying_health,
        start_process=lambda cmd: dying_proc,
        wait_ready=lambda: True,
        owner_file=owner_file,
    )
    toctou_mgr._process = dying_proc
    assert toctou_mgr.status() == {"state": "stopped", "ownership": None}
    assert health_calls == 2


def test_provider_catalog_failed_manual_refresh_preserves_backoff_and_singleflights_auto_refresh() -> None:
    fetch_calls = 0
    fetch_entered = threading.Event()
    release_fetch = threading.Event()

    class SlowFailingOpenCodeUpstream:
        def fetch(self, api_key: str | None = None, *, base_url: str | None = None):
            nonlocal fetch_calls
            fetch_calls += 1
            fetch_entered.set()
            release_fetch.wait(timeout=5.0)
            raise RuntimeError("bridge offline")

    class FakeRepo:
        def credential(self, context: ProviderCatalogContext, provider: str):
            return {"enabled": True, "api_key": "", "base_url": None, "updated_at": None}

        def list(self, context: ProviderCatalogContext, provider: str, favorites_only: bool = False):
            return []

        def replace_discovered(self, context: ProviderCatalogContext, provider: str, entries, *, base_url=None):
            return list(entries)

    service = ProviderModelCatalogService(FakeRepo(), {"opencode": SlowFailingOpenCodeUpstream()})
    ctx = ProviderCatalogContext(user_id="user-1", purpose="test")

    # Concurrent list() calls while cache is empty must single-flight the auto-refresh instead of stampeding N x 12s.
    barrier = threading.Barrier(4)
    results: list[list[Any]] = []
    res_lock = threading.Lock()

    def worker() -> None:
        barrier.wait(timeout=5.0)
        items = service.list(ctx, "opencode")
        with res_lock:
            results.append(items)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for th in threads:
        th.start()
    assert fetch_entered.wait(timeout=2.0)
    time.sleep(0.05)
    release_fetch.set()
    for th in threads:
        th.join(timeout=5.0)

    assert len(results) == 4
    assert fetch_calls == 1, f"expected single-flight auto-refresh, got {fetch_calls} upstream calls"

    # A failing manual refresh() must NOT clear the active _auto_refresh_backoff window!
    with pytest.raises(RuntimeError, match="provider catalog refresh failed"):
        service.refresh(ctx, "opencode")
    assert fetch_calls == 2

    # Subsequent list() must still be protected by the active backoff window (no 3rd fetch call).
    assert service.list(ctx, "opencode") == []
    assert fetch_calls == 2


def test_opencode_ensure_running_cleans_up_prior_hung_process_and_cleans_up_on_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminated_pids: list[int] = []
    next_pid = 800000

    class FakeProc:
        def __init__(self) -> None:
            nonlocal next_pid
            next_pid += 1
            self.pid = next_pid
            self._rc: int | None = None

        def poll(self) -> int | None:
            return self._rc

        def terminate(self) -> None:
            terminated_pids.append(self.pid)
            self._rc = -15

        def wait(self, timeout: float | None = None) -> int:
            return -15

        def kill(self) -> None:
            terminated_pids.append(self.pid)
            self._rc = -9

    monkeypatch.setattr("agentos.provider_catalog.opencode.shutil.which", lambda name: "opencode")
    monkeypatch.setattr("agentos.provider_catalog.opencode.subprocess.Popen", lambda *a, **kw: FakeProc())
    monkeypatch.setattr("agentos.provider_catalog.opencode.time.sleep", lambda s: None)

    manager = OpenCodeBridgeProcessManager()
    hung_prior = FakeProc()
    manager._processes[43112] = (hung_prior, None)  # type: ignore[assignment]

    def boom_is_healthy(url: str) -> bool:
        if hung_prior.pid not in terminated_pids:
            # First health check sees the prior hung process is unhealthy
            return False
        raise ValueError("unexpected probe crash")

    monkeypatch.setattr(manager, "_is_healthy", boom_is_healthy)

    with pytest.raises(ValueError, match="unexpected probe crash"):
        manager.ensure_running("http://127.0.0.1:43112")

    # Both the prior hung process AND the newly spawned process that crashed during readiness must be cleaned up!
    assert hung_prior.pid in terminated_pids
    assert len(terminated_pids) == 2
    assert 43112 not in manager._processes


def test_production_startup_hooks_and_local_spa_offload_sync_io_to_threadpool(tmp_path: Path) -> None:
    from agentos.bootstrap.production import unavailable_production_services

    spa_dir = tmp_path / "spa"
    (spa_dir / "assets").mkdir(parents=True)
    (spa_dir / "index.html").write_text('<!doctype html><html><head><meta name="agentos-auth-mode" content=""></head><body>Orin</body></html>', encoding="utf-8")

    omniroute_threads: list[int] = []

    class SlowOmniRouteManager:
        def start_if_any_enabled_in_background(self) -> dict[str, object]:
            omniroute_threads.append(threading.get_ident())
            time.sleep(SLOW_SECONDS)
            return {"state": "starting", "ownership": "agentos"}

        def stop(self) -> dict[str, object]:
            return {"state": "stopped", "ownership": None}

    settings = ProductionSettings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'prod_hooks.db').as_posix()}",
        WEB_DIST_DIR=str(spa_dir),
        LOCALHOST_TRUST_ENABLED=True,
    )
    services = unavailable_production_services()
    services.omniroute_runtime = SlowOmniRouteManager()  # type: ignore[assignment]
    app = create_production_app(
        settings,
        services=services,
        probe=DependencyProbe(postgres=lambda: True),
    )

    async def exercise() -> None:
        loop_thread = threading.get_ident()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            # Run startup hooks manually and verify _start_configured_omniroute does not block the event loop
            startup_coros = [handler() for handler in app.router.on_startup if asyncio.iscoroutinefunction(handler)]
            slow_task = asyncio.ensure_future(asyncio.gather(*startup_coros))
            await asyncio.sleep(SLOW_SECONDS / 4)
            t0 = time.monotonic()
            spa_resp = await client.get("/")
            spa_elapsed = time.monotonic() - t0
            assert spa_resp.status_code == 200
            assert "Orin" in spa_resp.text
            assert spa_elapsed < SLOW_SECONDS / 2, f"startup hook blocked event loop ({spa_elapsed:.3f}s)"
            await slow_task
            assert omniroute_threads and all(tid != loop_thread for tid in omniroute_threads)

    asyncio.run(exercise())


def test_subrouter_endpoints_offload_principal_for_and_conversation_root_to_threadpool(tmp_path: Path) -> None:
    import ast

    # 1. Static AST invariant check across all API and production bootstrap modules:
    # No async def handler may invoke synchronous auth/rate-limit/workspace helpers directly on the event loop.
    api_dir = Path(__file__).resolve().parents[3] / "src" / "agentos" / "api"
    prod_file = Path(__file__).resolve().parents[3] / "src" / "agentos" / "bootstrap" / "production.py"
    targets = sorted(api_dir.glob("*.py")) + [prod_file]
    forbidden_sync_helpers = {
        "principal_for",
        "check_rate_limit",
        "exclusive_folder",
        "conversation_record",
        "conversation_workspace",
        "conversation_root",
        "folder_of",
    }
    violations: list[str] = []
    for path in targets:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                stack = list(node.body)
                while stack:
                    cur = stack.pop(0)
                    if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        continue
                    if isinstance(cur, ast.Call):
                        fn = cur.func
                        name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else None)
                        if name in forbidden_sync_helpers:
                            violations.append(f"{path.name}:{cur.lineno} in async def {node.name} calls {name}(...)")
                        if name == "run_in_threadpool":
                            continue
                    stack.extend(ast.iter_child_nodes(cur))

    assert not violations, f"Synchronous helpers called on event loop: {violations}"

    # 2. Runtime concurrency check: slow principal_for / delete_conversation / list_tasks in conversation_routes
    # must run on worker threads and never block fast /v1/plugins requests.
    class SlowSubrouterSecurity(InMemorySecurityService):
        def check_rate_limit(self, principal, *, action: str = "", origin: str | None = None) -> None:
            if action == "DELETE:/v1/conversations/conv-del":
                time.sleep(SLOW_SECONDS)
            super().check_rate_limit(principal, action=action, origin=origin)

    class FakeConversations:
        def get(self, conversation_id: str, user_id: str):
            if conversation_id == "conv-tasks-slow":
                time.sleep(SLOW_SECONDS)
            return {"conversation_id": conversation_id, "project_id": None}

        def delete(self, conversation_id: str, user_id: str) -> None:
            return None

    class FakePlugins:
        def list(self, user_id: str):
            return []

    security = SlowSubrouterSecurity()
    security.add_pat("pat", AuthenticatedPrincipal("user-1", "credential-1", frozenset({"api"})))
    app = create_app(
        ApiServices(
            security=security,
            conversation_application=FakeConversations(),  # type: ignore[arg-type]
            plugins=FakePlugins(),  # type: ignore[arg-type]
            workspace_root=tmp_path / "workspaces",
        )
    )

    async def slow_delete(client: AsyncClient):
        return await client.delete("/v1/conversations/conv-del", headers=_headers("del-1"))

    async def slow_tasks(client: AsyncClient):
        return await client.get("/v1/conversations/conv-tasks-slow/tasks", headers=_headers("tasks-1"))

    async def fast_plugins(client: AsyncClient):
        return await client.get("/v1/plugins", headers=_headers("fast-1"))

    elapsed_del = asyncio.run(_measure_fast_request_under_slow_load(app, slow_delete, fast_plugins))
    assert elapsed_del < SLOW_SECONDS / 2, f"DELETE /v1/conversations/{{id}} blocked event loop ({elapsed_del:.3f}s)"

    elapsed_tasks = asyncio.run(_measure_fast_request_under_slow_load(app, slow_tasks, fast_plugins))
    assert elapsed_tasks < SLOW_SECONDS / 2, f"GET /v1/conversations/{{id}}/tasks blocked event loop ({elapsed_tasks:.3f}s)"


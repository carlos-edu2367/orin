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

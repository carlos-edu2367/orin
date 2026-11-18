from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import gc
from pathlib import Path
import threading
import time
import weakref

import httpx
import pytest
from sqlalchemy import insert, text

from agentos.agentic.background_agents import BackgroundAgentEntry, BackgroundAgentRegistry
from agentos.agentic.events import AgentActivityEvent
from agentos.conversations.chat import PostgresChatStore
from agentos.persistence.postgres.agentic_activity import PostgresAgenticActivityStore
from agentos.persistence.postgres.schema import conversation_activity_events, metadata
from agentos.persistence.sqlite import create_local_engine
from agentos.providers.claude_cli.provider import ClaudeCliProvider
from agentos.retrieval.chunking import HeuristicChunker
from agentos.retrieval.embeddings.ollama import EmbeddingUnavailable
from agentos.retrieval.factory import _OLLAMA_EMBEDDER_CACHE, build_embedder
from agentos.retrieval.indexer import ProjectIndexer
from agentos.retrieval.models import EmbedderIdentity
from agentos.retrieval.store import SqliteChunkStore
from agentos.workers import publisher
from agentos.workers.chat import ChatWorker


def test_sqlite_engine_applies_wal_memory_and_cache_pragmas(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'pragmas.db').as_posix()}")
    try:
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar_one().lower() == "wal"
            assert int(conn.execute(text("PRAGMA busy_timeout")).scalar_one()) == 30000
            assert int(conn.execute(text("PRAGMA wal_autocheckpoint")).scalar_one()) == 4000
            assert int(conn.execute(text("PRAGMA journal_size_limit")).scalar_one()) == 67108864
            assert int(conn.execute(text("PRAGMA temp_store")).scalar_one()) == 2  # MEMORY
            assert int(conn.execute(text("PRAGMA cache_size")).scalar_one()) == -32768
    finally:
        engine.dispose()


def test_poll_once_throttles_worker_heartbeats_instead_of_writing_every_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'hb.db').as_posix()}")
    metadata.create_all(engine)
    try:
        store = PostgresChatStore(engine)
        heartbeat_calls: list[str] = []
        orig_hb = store.heartbeat

        def counting_hb(component: str) -> None:
            heartbeat_calls.append(component)
            orig_hb(component)

        monkeypatch.setattr(store, "heartbeat", counting_hb)
        fake_now = [2.0]  # Simulate freshly booted OS (< 10s monotonic)
        monkeypatch.setattr("agentos.workers.chat.time.monotonic", lambda: fake_now[0])
        monkeypatch.setattr("agentos.workers.publisher.time.monotonic", lambda: fake_now[0])

        worker = ChatWorker(store)
        runner = publisher.ConcurrentTurnRunner(worker, max_workers=2)
        try:
            next_sweep = 100.0
            # First tick writes immediately even when monotonic < 10s
            _, next_sweep = publisher.poll_once(runner, next_sweep)
            assert len(heartbeat_calls) == 1

            # Rapid 0.35s ticks within the 10s window do NOT hammer SQLite
            for _ in range(15):
                fake_now[0] += 0.35
                _, next_sweep = publisher.poll_once(runner, next_sweep)
            assert len(heartbeat_calls) == 1

            # Once 10s have elapsed, heartbeat refreshes again
            fake_now[0] += 10.0
            _, next_sweep = publisher.poll_once(runner, next_sweep)
            assert len(heartbeat_calls) == 2
        finally:
            runner.shutdown(wait=True)
    finally:
        engine.dispose()


def test_concurrent_heartbeats_and_activity_writes_do_not_raise_sqlite_busy(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'concurrent_wal.db').as_posix()}")
    metadata.create_all(engine)
    try:
        activity_store = PostgresAgenticActivityStore(engine, cursor_secret="secret")
        store = PostgresChatStore(engine, activity_store=activity_store)
        receipt = store.create(
            user_id="u1",
            message="stress wal",
            provider="openrouter",
            model_id="m1",
            idempotency_key="wal-stress-1",
        )
        barrier = threading.Barrier(8)

        def writer(idx: int) -> None:
            barrier.wait(timeout=5.0)
            for step in range(15):
                store.heartbeat("chat-worker" if idx % 2 == 0 else "scheduled-chat-worker")
                ev = AgentActivityEvent(
                    event_id=f"evt-{idx}-{step}",
                    conversation_id=receipt.conversation_id,
                    turn_id=receipt.turn_id,
                    execution_id=f"exe-{receipt.turn_id}",
                    user_id="u1",
                    workspace_id=None,
                    agent_id="main",
                    parent_agent_id=None,
                    event_type="tool.progressed",
                    sequence=idx * 100 + step + 10,
                    summary=f"step {idx}-{step}",
                    payload={"idx": idx, "step": step},
                    visibility="public",
                    created_at=datetime.now(UTC),
                )
                assert activity_store.record_event(ev) is True
                # Idempotent replay of exact same event returns False without error
                assert activity_store.record_event(ev) is False

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(writer, i) for i in range(8)]
            for fut in futures:
                fut.result()
    finally:
        engine.dispose()


def test_claude_cli_provider_gc_releases_instance_and_deletes_temp_prompt_file() -> None:
    provider = ClaudeCliProvider(model="claude-sonnet-4-5")
    prompt_path = provider._ensure_prompt_file("Forensic test prompt")
    assert prompt_path.is_file()

    ref = weakref.ref(provider)
    del provider
    gc.collect()

    assert ref() is None, "ClaudeCliProvider must not be pinned in memory by atexit"
    assert not prompt_path.exists(), "Temp prompt file must be unlinked on provider GC"


def test_build_embedder_caches_ollama_instance_and_preserves_404_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORIN_RETRIEVAL_EMBEDDER", "ollama")
    monkeypatch.setenv("ORIN_RETRIEVAL_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("ORIN_RETRIEVAL_MODEL", "missing-forensic-model")
    _OLLAMA_EMBEDDER_CACHE.clear()

    e1 = build_embedder()
    e2 = build_embedder()
    assert e1 is e2

    calls = 0

    def fake_post(url: str, json: object) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, request=httpx.Request("POST", url))

    monkeypatch.setattr(e1._client, "post", fake_post)  # noqa: SLF001

    with pytest.raises(EmbeddingUnavailable):
        e1.embed(["first attempt"])
    assert calls == 1

    # Subsequent embedder lookups reuse cooldown instead of hitting HTTP 404 again
    e3 = build_embedder()
    with pytest.raises(EmbeddingUnavailable):
        e3.embed(["second attempt during cooldown"])
    assert calls == 1
    _OLLAMA_EMBEDDER_CACHE.clear()


def test_project_indexer_prunes_denied_and_gitignored_dirs_without_walking_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class DummyEmbedder:
        @property
        def identity(self) -> EmbedderIdentity:
            return EmbedderIdentity(embedder_id="dummy", model="m", dim=2)

        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("heavy_renders/\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("def ok():\n    return 42\n", encoding="utf-8")

    nm = root / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    for i in range(50):
        (nm / f"dep_{i}.js").write_text(f"export const x = {i};\n", encoding="utf-8")

    renders = root / "heavy_renders" / "ep002"
    renders.mkdir(parents=True)
    for i in range(20):
        (renders / f"chunk_{i}.txt").write_text("ignored render log\n", encoding="utf-8")

    store = SqliteChunkStore(tmp_path / "idx.db", DummyEmbedder().identity)
    indexer = ProjectIndexer(root=root, store=store, chunker=HeuristicChunker(), embedder=DummyEmbedder())

    resolved_paths: list[str] = []
    orig_resolve_abs = indexer._resolve_absolute

    def spy_resolve_abs(item: Path):
        resolved_paths.append(item.relative_to(root).as_posix())
        return orig_resolve_abs(item)

    monkeypatch.setattr(indexer, "_resolve_absolute", spy_resolve_abs)
    indexer.scan()

    assert set(store.known_files()) == {".gitignore", "src/main.py"}
    assert set(resolved_paths) == {".gitignore", "src/main.py"}


def test_background_agent_registry_retires_conversation_executor_when_idle() -> None:
    reg = BackgroundAgentRegistry()
    reg.begin_turn("conv-a")
    pool_1 = reg.executor_for("conv-a")
    assert "conv-a" in reg._executors  # noqa: SLF001

    entry = BackgroundAgentEntry(
        agent_id="agent:conv-a:1",
        conversation_id="conv-a",
        name="antigravity",
        output_file=".orin/agent-outputs/1.md",
        status="running",
        started_at=time.monotonic(),
        finished_at=None,
        summary="",
        cancel=threading.Event(),
        future=None,
    )
    reg.register(entry)

    # Turn ends while background subagent is still running -> executor stays alive
    reg.end_turn("conv-a")
    assert reg._executors.get("conv-a") is pool_1  # noqa: SLF001

    # Once background subagent finishes and no turn is active -> executor is retired
    reg.finish("agent:conv-a:1", "succeeded", "done", conversation_id="conv-a")
    assert "conv-a" not in reg._executors  # noqa: SLF001


def test_sqlite_overview_json_extract_matches_expected_aggregation(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'ov.db').as_posix()}")
    metadata.create_all(engine)
    try:
        activity_store = PostgresAgenticActivityStore(engine, cursor_secret="secret")
        store = PostgresChatStore(engine, activity_store=activity_store)
        receipt = store.create(
            user_id="u1",
            message="test overview",
            provider="openrouter",
            model_id="m1",
            idempotency_key="ov-1",
        )
        now = datetime.now(UTC)
        with engine.begin() as c:
            c.execute(
                insert(conversation_activity_events),
                [
                    {
                        "event_id": "t-1",
                        "conversation_id": receipt.conversation_id,
                        "user_id": "u1",
                        "workspace_id": None,
                        "turn_id": receipt.turn_id,
                        "execution_id": f"exe-{receipt.turn_id}",
                        "agent_id": "agent-a",
                        "parent_agent_id": None,
                        "event_type": "tool.finished",
                        "sequence": 10,
                        "summary": "ok",
                        "payload": {"tool_name": "read_file", "tool_kind": "fs", "status": "succeeded", "big": "x" * 5000},
                        "visibility": "public",
                        "created_at": now,
                    },
                    {
                        "event_id": "t-2",
                        "conversation_id": receipt.conversation_id,
                        "user_id": "u1",
                        "workspace_id": None,
                        "turn_id": receipt.turn_id,
                        "execution_id": f"exe-{receipt.turn_id}",
                        "agent_id": "agent-b",
                        "parent_agent_id": None,
                        "event_type": "tool.finished",
                        "sequence": 11,
                        "summary": "fail",
                        "payload": {"tool_name": "read_file", "tool_kind": "fs", "status": "failed"},
                        "visibility": "public",
                        "created_at": now,
                    },
                ],
            )
        ov = store.overview(receipt.conversation_id, "u1")
        assert ov["tools"] == [
            {"tool_name": "read_file", "kind": "fs", "count": 2, "failures": 1, "agent_id": "agent-a"}
        ]
    finally:
        engine.dispose()


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows Job Object test")
def test_windows_job_kills_grandchild_when_parent_cli_exits_early(tmp_path: Path) -> None:
    import subprocess
    import sys

    from agentos.hooks.processes import _WindowsJob
    from agentos.launcher.state import process_is_alive

    pid_file = tmp_path / "grandchild.pid"
    parent_script = tmp_path / "parent_cli.py"
    parent_script.write_text(
        f"""
import subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open(r"{pid_file}", "w", encoding="utf-8") as f:
    f.write(str(child.pid))
sys.exit(1)
""",
        encoding="utf-8",
    )

    job = _WindowsJob(kill_on_close=True)
    proc = subprocess.Popen([sys.executable, str(parent_script)])
    grandchild_pid = 0
    try:
        raw_handle = getattr(proc, "_handle", None)
        job.assign(proc.pid, root_handle=int(raw_handle) if raw_handle is not None else None)
        assert proc.wait(timeout=10.0) == 1
        grandchild_pid = int(pid_file.read_text(encoding="utf-8").strip())
        assert process_is_alive(grandchild_pid) is True
    finally:
        job.terminate()
        job.close()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and process_is_alive(grandchild_pid):
        time.sleep(0.05)
    assert process_is_alive(grandchild_pid) is False

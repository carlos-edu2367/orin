"""Red Team regression suite for Orin 0.13 Package B (RT-B-01..RT-B-06, RT-B-09..RT-B-14)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import stat
import subprocess
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from agentos.agentic.agent_progress import ProgressTracker
from agentos.agentic.agent_run_persistence import AgentRunRecorder
from agentos.agentic.background_agents import BACKGROUND_AGENTS, fail_orphaned_running_agents
from agentos.agentic.session import TurnSession
from agentos.agentic.settings import AgentRuntimeSettingsStore
from agentos.agentic.subagent_worktree import _worktree_settings
from agentos.agentic.task_board import TaskBoard
from agentos.agentic.tools._shared import AgentToolError
from agentos.api.gateway import ApiServices, create_app
from agentos.api.security import AuthenticatedPrincipal, InMemorySecurityService
from agentos.api.settings_routes import AgentFile, write_agent
from agentos.code_mode.agent_worktree import AgentWorktreeManager
from agentos.code_mode.merge_queue import MergeQueue
from agentos.conversations.agent_controls import AgentControlStore
from agentos.conversations.chat import ChatApplication, PostgresChatStore
from agentos.persistence.postgres.agent_runs import AgentRunStore
from agentos.persistence.postgres.agent_worktrees import AgentWorktreeStore
from agentos.persistence.postgres.conversation_agents import ConversationAgentStore
from agentos.persistence.postgres.schema import (
    agent_runs,
    agent_worktrees,
    conversation_agent_controls,
    conversation_messages,
    metadata,
    task_board_items,
)
from agentos.persistence.postgres.task_board import TaskBoardStore
from agentos.persistence.sqlite import create_local_engine
from agentos.settings.keys_worktree import DEFAULT_WORKTREE_LINK_DIRS
from agentos.settings.loader import EffectiveSettings, SettingsLayer, UNTRUSTED_IGNORED_FROM_PROJECT, load_effective_settings
from agentos.workers.publisher import auto_resume_interrupted_agents
from tests.unit.agentic.test_parallel_and_background_subagents import (
    TURN,
    _RecordingStore,
    _parallel_tool_calls_batch,
    _text_batch,
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def _init_git_repo(repo: Path, *, autocrlf: str = "false") -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "redteam@orin.test")
    _git(repo, "config", "user.name", "RedTeam")
    _git(repo, "config", "core.autocrlf", autocrlf)
    (repo / "app.py").write_bytes(b"line1\r\nline2\r\n" if autocrlf == "true" else b"line1\nline2\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "initial")


def _create_dir_link(link_path: Path, target_dir: Path) -> bool:
    """Create an NTFS junction on Windows or a directory symlink on POSIX."""
    if os.name == "nt":
        import _winapi  # type: ignore[import-not-found]

        _winapi.CreateJunction(str(target_dir), str(link_path))
        return True
    os.symlink(str(target_dir), str(link_path), target_is_directory=True)
    return True


def _setup_api_client(tmp_path: Path, *, workspace_root: Path | None = None):
    db_path = tmp_path / "redteam_api.db"
    engine = create_local_engine(f"sqlite:///{db_path.as_posix()}")
    metadata.create_all(engine)
    store = PostgresChatStore(engine)
    chat_app = ChatApplication(store)
    security = InMemorySecurityService()
    security.add_pat(
        "pat-u1",
        AuthenticatedPrincipal(user_id="user-1", credential_ref="pat-1", scopes=frozenset({"api"})),
    )
    security.add_pat(
        "pat-u2",
        AuthenticatedPrincipal(user_id="user-2", credential_ref="pat-2", scopes=frozenset({"api"})),
    )
    app = create_app(
        ApiServices(
            security=security,
            conversation_application=chat_app,
            agentic_runtime=AgentRuntimeSettingsStore(tmp_path / "runtime.json"),
            workspace_root=workspace_root or (tmp_path / "workspaces"),
        )
    )
    return engine, store, chat_app, TestClient(app)


# ---------------------------------------------------------------------------
# RT-B-01: NTFS junction / symlink exfiltration inside worktree
# ---------------------------------------------------------------------------


def test_rt_b_01_worktree_junction_or_symlink_cannot_exfiltrate_outside_files(tmp_path: Path) -> None:
    outside_dir = tmp_path / "outside_secrets"
    outside_dir.mkdir()
    secret_file = outside_dir / "secret_outside.txt"
    secret_file.write_text("TOP_SECRET_CREDENTIAL_VALUE", encoding="utf-8")

    repo = tmp_path / "repo"
    _init_git_repo(repo)

    manager = AgentWorktreeManager(repo)
    wt = manager.create_for_agent("agent-exfil-1", repo, link_dirs=())
    try:
        # Subagent plants a junction/symlink directory inside the worktree pointing outside.
        assert _create_dir_link(wt.path / "steal_dir", outside_dir)
        assert (wt.path / "steal_dir" / "secret_outside.txt").is_file()

        assert manager.has_agent_changes(wt.path) is False

        mq = MergeQueue(repo, manager=manager)
        patch_bytes, files = mq._build_worktree_patch(wt)
        assert files == []
        assert b"TOP_SECRET_CREDENTIAL_VALUE" not in patch_bytes
        assert b"steal_dir" not in patch_bytes

        # Also verify that even if the subagent stages/commits a symlink (mode 120000) or gitlink (160000),
        # _build_worktree_patch never emits mode 120000 or 160000 into the parent patch.
        _git(
            wt.path,
            "update-index",
            "--add",
            "--cacheinfo",
            "120000",
            _git(wt.path, "hash-object", "-w", "--stdin").stdout.strip()
            if False
            else subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=wt.path,
                input=str(secret_file),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip(),
            "evil_link.txt",
        )
        _git(wt.path, "commit", "-m", "plant symlink mode 120000")
        patch_bytes2, files2 = mq._build_worktree_patch(wt)
        assert b"120000" not in patch_bytes2
        assert "evil_link.txt" not in files2

        # Enqueue merge and verify outside secret never lands in parent workspace.
        res = mq.enqueue(wt.agent_id)
        assert not (repo / "steal_dir").exists()
        assert not (repo / "evil_link.txt").exists()
        assert res.state in {"empty", "applied"}
    finally:
        manager.discard_worktree("agent-exfil-1")


# ---------------------------------------------------------------------------
# RT-B-02: core.autocrlf=true & rollback preserving parent .git/index
# ---------------------------------------------------------------------------


def test_rt_b_02_autocrlf_true_applies_cleanly_and_rollback_preserves_parent_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo_crlf"
    _init_git_repo(repo, autocrlf="true")

    # Ensure working copy on disk has CRLF as Git for Windows does.
    (repo / "app.py").write_bytes(b"line1\r\nline2\r\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "--allow-empty", "-m", "ensure crlf")

    manager = AgentWorktreeManager(repo)
    wt = manager.create_for_agent("agent-crlf-ok", repo, link_dirs=())
    (wt.path / "app.py").write_bytes(b"line1\r\nline2\r\nline3_from_child\r\n")

    mq = MergeQueue(repo, manager=manager)
    ok = mq.enqueue("agent-crlf-ok")
    assert ok.state == "applied", ok
    assert "line3_from_child" in (repo / "app.py").read_text(encoding="utf-8")

    # Commit the clean state so we can test rollback after mergeCheckCommand failure.
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "after first merge")

    stage_before = _git(repo, "ls-files", "--stage").stdout
    status_before = _git(repo, "status", "--porcelain").stdout

    wt2 = manager.create_for_agent("agent-crlf-fail", repo, link_dirs=())
    (wt2.path / "app.py").write_bytes(b"line1\r\nBROKEN_LINE\r\n")
    mq_fail = MergeQueue(
        repo,
        manager=manager,
        merge_check_command='python -c "import sys; sys.exit(1)"' if os.name != "nt" else "exit /b 1",
        command_authorizer=lambda _cmd: (True, "ok"),
    )
    res_fail = mq_fail.enqueue("agent-crlf-fail")
    try:
        assert res_fail.state == "check_failed"
        stage_after = _git(repo, "ls-files", "--stage").stdout
        status_after = _git(repo, "status", "--porcelain").stdout
        assert stage_after == stage_before
        assert "D  app.py" not in status_after
        assert status_after == status_before
        assert "BROKEN_LINE" not in (repo / "app.py").read_text(encoding="utf-8")
    finally:
        manager.discard_worktree("agent-crlf-fail")


# ---------------------------------------------------------------------------
# RT-B-03: 68-char real production agent_id across stores, schema & migrations
# ---------------------------------------------------------------------------


def test_rt_b_03_68_char_agent_id_no_truncation_and_schema_varchar_255(tmp_path: Path) -> None:
    # Real ConversationAgentStore format: "agent:chat_<32hex>:<24hex>" = 68 chars
    conv_id = "chat_" + ("a" * 32)
    agent_id = f"agent:{conv_id}:" + ("b" * 24)
    assert len(agent_id) == 68

    # Schema columns must be String(255)
    assert agent_runs.c.agent_id.type.length == 255
    assert agent_worktrees.c.agent_id.type.length == 255
    assert task_board_items.c.owner_agent_id.type.length == 255
    assert conversation_agent_controls.c.sender_agent_id.type.length == 255

    engine = create_local_engine(f"sqlite:///{(tmp_path / 'rt_b_03.db').as_posix()}")
    metadata.create_all(engine)
    try:
        # 1. AgentRunStore
        run_store = AgentRunStore(engine)
        run = run_store.upsert(
            agent_id=agent_id,
            user_id="user-1",
            conversation_id=conv_id,
            turn_id="turn-1",
            subagent_type="general",
            state="running",
        )
        assert run.agent_id == agent_id
        assert run_store.get(agent_id) is not None
        assert run_store.get(agent_id).agent_id == agent_id

        # 2. AgentWorktreeStore
        wt_store = AgentWorktreeStore(engine)
        wt_rec = wt_store.upsert(
            agent_id=agent_id,
            workspace_root=str(tmp_path),
            base_commit="0" * 40,
            branch="orin/agent-test",
        )
        assert wt_rec.agent_id == agent_id
        assert wt_store.get(agent_id) is not None
        assert wt_store.get(agent_id).agent_id == agent_id

        # 3. TaskBoardStore & TaskBoard & TaskClaim
        tb_store = TaskBoardStore(engine)
        board = TaskBoard(conversation_id=conv_id, store=tb_store)
        item = board.create(title="Long ID task")
        claimed = board.claim(item.item_id, agent_id)
        assert claimed is not None
        assert claimed.owner_agent_id == agent_id
        renewed = board.renew(item.item_id, agent_id)
        assert renewed is not None
        assert renewed.owner_agent_id == agent_id
        completed = board.update(item.item_id, status="completed", acting_agent_id=agent_id)
        assert completed is not None
        assert completed.owner_agent_id == agent_id

        # 4. AgentControlStore sender_agent_id
        ctrl_store = AgentControlStore(engine)
        ctrl_id = ctrl_store.post_message(
            "user-1",
            conv_id,
            agent_id,
            "hello",
            sender_kind="parent",
            sender_agent_id=agent_id,
        )
        ctrl = ctrl_store.get(ctrl_id)
        assert ctrl is not None
        assert ctrl["sender_agent_id"] == agent_id
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# RT-B-04: TaskBoard ownership/lease enforcement & status transitions
# ---------------------------------------------------------------------------


def test_rt_b_04_task_board_enforces_ownership_clears_lease_on_pending_and_guards_in_progress(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'rt_b_04.db').as_posix()}")
    metadata.create_all(engine)
    try:
        for mode in ("sql", "memory"):
            board = (
                TaskBoard(conversation_id=f"conv-04-{mode}", engine=engine)
                if mode == "sql"
                else TaskBoard(conversation_id=f"conv-04-{mode}")
            )
            dep = board.create(title="Dependency task", item_id=f"dep-{mode}")
            t1 = board.create(title="Work item", item_id=f"item-{mode}")

            # 1. TaskCreate(status="in_progress") without owner must be rejected
            with pytest.raises(ValueError):
                board.create(title="Bad orphan in_progress", status="in_progress")

            # 2. Agent A claims t1
            claimed_a = board.claim(t1.item_id, "agent-A")
            assert claimed_a is not None
            assert claimed_a.owner_agent_id == "agent-A"
            assert claimed_a.lease_expires_at is not None

            # 3. Another non-owner subagent B cannot mutate or complete A's in_progress task
            with pytest.raises(ValueError):
                board.update(t1.item_id, status="completed", acting_agent_id="agent-B")
            with pytest.raises(ValueError):
                board.update(t1.item_id, status="pending", acting_agent_id="agent-B")
            with pytest.raises(ValueError):
                board.update(t1.item_id, title="Hijacked", acting_agent_id="agent-B")

            # Still owned by agent-A
            still_a = board.get(t1.item_id)
            assert still_a is not None
            assert still_a.status == "in_progress"
            assert still_a.owner_agent_id == "agent-A"

            # 4. Returning task to 'pending' (by owner or main) clears owner_agent_id and lease_expires_at
            released = board.update(t1.item_id, status="pending", acting_agent_id="agent-A")
            assert released is not None
            assert released.status == "pending"
            assert released.owner_agent_id is None
            assert released.lease_expires_at is None

            # Now agent-B can legitimately claim it!
            claimed_b = board.claim(t1.item_id, "agent-B")
            assert claimed_b is not None
            assert claimed_b.owner_agent_id == "agent-B"

            # 5. TaskUpdate(status="in_progress") on a blocked task must NOT bypass blocked_by!
            blocked_task = board.create(
                title="Blocked task",
                item_id=f"blk-{mode}",
                blocked_by=[dep.item_id],
            )
            with pytest.raises(ValueError):
                board.update(blocked_task.item_id, status="in_progress", acting_agent_id="agent-B")
            assert board.get(blocked_task.item_id).status == "pending"

            # And on an unblocked pending task, TaskUpdate(status="in_progress") delegates to claim (sets owner & lease)
            free_task = board.create(title="Free task", item_id=f"free-{mode}")
            started = board.update(free_task.item_id, status="in_progress", acting_agent_id="agent-B")
            assert started is not None
            assert started.status == "in_progress"
            assert started.owner_agent_id == "agent-B"
            assert started.lease_expires_at is not None
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# RT-B-05: Real subagent resume without fake user chat message + failure rollback + queued orphan sweep
# ---------------------------------------------------------------------------


def test_rt_b_05_resume_no_fake_user_message_reverts_on_dispatch_failure_and_sweeps_queued(
    tmp_path: Path, monkeypatch
) -> None:
    runs_dir = tmp_path / "agent-runs"
    monkeypatch.setenv("ORIN_AGENT_RUNS_DIR", str(runs_dir))

    engine, store, chat_app, client = _setup_api_client(tmp_path)
    BACKGROUND_AGENTS.clear_for_tests()
    try:
        c1 = chat_app.create(
            user_id="user-1",
            message="initial user message",
            provider="openai",
            model_id="gpt-4o",
            idempotency_key="idem-rt-b-05",
        )
        conv_id = c1.conversation_id

        with engine.connect() as conn:
            msg_count_before = len(
                conn.execute(
                    select(conversation_messages.c.message_id).where(
                        conversation_messages.c.conversation_id == conv_id
                    )
                ).all()
            )

        agents = ConversationAgentStore(engine, conversation_id=conv_id, user_id="user-1")
        rec = agents.create("bg-worker", "general", parent_agent_id=f"agent:{conv_id}:main")
        aid = str(rec["agent_id"])
        agents.set_state(aid, "interrupted")

        recorder = AgentRunRecorder(engine, runs_dir=runs_dir)
        recorder.start(
            agent_id=aid,
            user_id="user-1",
            conversation_id=conv_id,
            turn_id=c1.turn_id,
            parent_agent_id=f"agent:{conv_id}:main",
            subagent_type="general",
            provider="openai",
            model_id="gpt-4o",
            task="Finish background job",
            state="interrupted",
            initial_history=[{"role": "user", "content": "prior"}],
        )

        # 1. Dispatch failure reverts state back to 'interrupted' (never stuck in 'queued')
        def _failing_dispatcher(_run, *, notice: str = ""):
            raise RuntimeError("worker unreachable")

        chat_app.dispatch_agent_resume = _failing_dispatcher  # type: ignore[attr-defined]
        fail_res = client.post(
            f"/v1/conversations/{conv_id}/agents/{aid}/resume",
            headers={"Authorization": "Bearer pat-u1"},
        )
        assert fail_res.status_code == 503
        assert AgentRunStore(engine).get(aid).state == "interrupted"
        assert agents.get(aid)["state"] == "interrupted"

        # 2. Normal POST /resume succeeds WITHOUT injecting a fake user message into conversation_messages
        delattr(chat_app, "dispatch_agent_resume")
        ok_res = client.post(
            f"/v1/conversations/{conv_id}/agents/{aid}/resume",
            headers={"Authorization": "Bearer pat-u1"},
        )
        assert ok_res.status_code == 202, ok_res.text
        assert AgentRunStore(engine).get(aid).state == "queued"

        with engine.connect() as conn:
            msg_count_after = len(
                conn.execute(
                    select(conversation_messages.c.message_id).where(
                        conversation_messages.c.conversation_id == conv_id
                    )
                ).all()
            )
        assert msg_count_after == msg_count_before, "POST /resume must NOT inject a fake user message"

        # 3. Boot orphan sweep ALSO sweeps 'queued' rows back to 'interrupted'
        swept = fail_orphaned_running_agents(engine)
        assert aid in swept
        assert AgentRunStore(engine).get(aid).state == "interrupted"

        # 4. auto_resume_interrupted_agents reverts to 'interrupted' if handler fails (and does not lock auto_resumed)
        def _boom(_run):
            raise RuntimeError("auto-resume boom")

        resumed_fail = auto_resume_interrupted_agents(
            engine,
            runs_dir=runs_dir,
            settings_loader=lambda _uid: {"orchestration": {"background": {"autoResume": True}}},
            resume_handler=_boom,
        )
        assert resumed_fail == []
        assert AgentRunStore(engine).get(aid).state == "interrupted"
        assert not recorder.has_auto_resumed(aid)

        # 5. Now with a succeeding handler, auto_resume runs cleanly without creating fake user messages
        called: list[str] = []
        resumed_ok = auto_resume_interrupted_agents(
            engine,
            runs_dir=runs_dir,
            settings_loader=lambda _uid: {"orchestration": {"background": {"autoResume": True}}},
            resume_handler=lambda r: called.append(r.agent_id),
        )
        assert resumed_ok == [aid]
        assert called == [aid]

        # 6. ChatWorker.resume_queued_subagents() drains queued runs by invoking TurnSession.resume_interrupted_subagent directly
        from agentos.workers.chat import ChatWorker

        worker = ChatWorker(store)
        resumed_via_session: list[str] = []

        class _FakeSession:
            _agent_runs_dir = runs_dir

            def resume_interrupted_subagent(self, target_aid: str, *, run_in_background: bool = True):
                assert run_in_background is True
                resumed_via_session.append(target_aid)
                AgentRunStore(engine).touch(target_aid, state="running")
                return SimpleNamespace(status="succeeded", summary="resumed", content="resumed")

        monkeypatch.setattr(worker, "_build_session_for", lambda _turn: _FakeSession())
        drained = worker.resume_queued_subagents(runs_dir=runs_dir)
        assert drained == [aid]
        assert resumed_via_session == [aid]
        assert AgentRunStore(engine).get(aid).state == "running"
    finally:
        BACKGROUND_AGENTS.clear_for_tests()
        engine.dispose()


# ---------------------------------------------------------------------------
# RT-B-06: Cross-turn ProgressTracker + stall/autoCancel + subtree metrics
# ---------------------------------------------------------------------------


def test_rt_b_06_progress_tracker_persists_across_turns_and_preserves_subtree_metrics(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'rt_b_06.db').as_posix()}")
    metadata.create_all(engine)
    runs_dir = tmp_path / "agent-runs"
    BACKGROUND_AGENTS.clear_for_tests()

    now_mono = [1000.0]
    now_wall = [datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)]
    started_evt = threading.Event()
    release_evt = threading.Event()

    class _SlowChildProvider:
        def stream(self, _request):
            started_evt.set()
            release_evt.wait(timeout=5.0)
            return iter(_text_batch("done"))

    try:
        effective = EffectiveSettings(
            data={"orchestration": {"stall": {"minutes": 1, "autoCancel": True}}},
            provenance={},
            layers=(),
            errors=(),
            trusted=True,
        )
        agents = ConversationAgentStore(engine, conversation_id=TURN["conversation_id"], user_id=TURN["user_id"])
        child = _SlowChildProvider()

        session_turn1 = TurnSession(
            turn={**TURN, "turn_id": "turn-1"},
            store=_RecordingStore(),
            agents_store=agents,
            memory_store=None,
            provider_factory=lambda: child,
            workspace_root=tmp_path,
            child_provider_factory=lambda _m: child,
        )
        session_turn1.effective_settings = effective
        session_turn1._agent_runs_dir = runs_dir
        session_turn1._progress_clock = lambda: now_mono[0]
        session_turn1._progress_wall_clock = lambda: now_wall[0]

        launched = session_turn1._run_subagent(
            subagent_type="general-purpose",
            task="Background job crossing turns",
            run_in_background=True,
        )
        assert launched.status == "succeeded"
        agent_id = str(launched.payload["agent_id"])
        assert started_evt.wait(timeout=5.0)

        # Simulate tool activity and grandchild usage inside Turn 1 tracker
        t1_tracker = session_turn1._progress_tracker()
        t1_tracker.on_tool_start(agent_id, "read_file")
        t1_tracker.start("grandchild-1", conversation_id=TURN["conversation_id"], parent_agent_id=agent_id)
        t1_tracker.on_usage("grandchild-1", input_tokens=500, output_tokens=200, cost_usd=0.05)

        # Turn 2 creates a brand-new TurnSession for the same conversation
        session_turn2 = TurnSession(
            turn={**TURN, "turn_id": "turn-2"},
            store=_RecordingStore(),
            agents_store=agents,
            memory_store=None,
            provider_factory=lambda: child,
            workspace_root=tmp_path,
            child_provider_factory=lambda _m: child,
        )
        session_turn2.effective_settings = effective
        session_turn2._agent_runs_dir = runs_dir
        session_turn2._progress_clock = lambda: now_mono[0]
        session_turn2._progress_wall_clock = lambda: now_wall[0]

        # Still active in Turn 2: current_tool and subtree tokens must be visible!
        snap_active = session_turn2._progress_payload_for(agent_id, fallback_status="running", check_stall=True)
        assert snap_active["current_tool"] == "read_file"
        assert snap_active["input_tokens"] >= 500
        assert snap_active["output_tokens"] >= 200
        assert snap_active["cost_usd"] >= 0.05

        # Advance clock past stall threshold (2 minutes > 1 minute) -> Turn 2 detects stall & autoCancels!
        now_mono[0] += 120.0
        now_wall[0] = now_wall[0] + timedelta(seconds=120)
        snap_stalled = session_turn2._progress_payload_for(agent_id, fallback_status="running", check_stall=True)
        assert snap_stalled["stalled"] is True
        assert snap_stalled["state"] == "cancelled"

        # Let thread unwind and verify run_recorder preserved subtree tokens/cost and cancelled state!
        release_evt.set()
        bg = BACKGROUND_AGENTS.get(TURN["conversation_id"], agent_id)
        if bg is not None:
            bg.future.result(timeout=5.0)
        run_row = AgentRunStore(engine).get(agent_id)
        assert run_row is not None
        assert run_row.state == "cancelled"
        assert run_row.input_tokens >= 500
        assert run_row.output_tokens >= 200
        assert (run_row.cost_usd or 0.0) >= 0.05
    finally:
        release_evt.set()
        BACKGROUND_AGENTS.clear_for_tests()
        engine.dispose()


# ---------------------------------------------------------------------------
# RT-B-09: orchestration.worktree.linkDirs ignored from untrusted project
# ---------------------------------------------------------------------------


def test_rt_b_09_worktree_link_dirs_ignored_from_untrusted_project(tmp_path: Path) -> None:
    assert "orchestration.worktree.linkDirs" in UNTRUSTED_IGNORED_FROM_PROJECT

    project_dir = tmp_path / "untrusted_repo"
    (project_dir / ".orin").mkdir(parents=True)
    (project_dir / ".orin" / "settings.json").write_text(
        '{"orchestration": {"worktree": {"linkDirs": [".claude", "secrets"]}}}',
        encoding="utf-8",
    )

    untrusted = load_effective_settings(
        project_dir,
        sources=("project",),
        trusted=False,
    )
    fake_session = SimpleNamespace(effective_settings=untrusted, _workspace_trusted=lambda: False)
    _enabled, link_dirs, _cmd = _worktree_settings(fake_session)
    assert link_dirs == DEFAULT_WORKTREE_LINK_DIRS
    assert ".claude" not in link_dirs
    assert "secrets" not in link_dirs

    # Defense-in-depth: even if EffectiveSettings was constructed manually with project layer
    manual_untrusted = EffectiveSettings(
        data={"orchestration": {"worktree": {"linkDirs": ["secrets"]}}},
        provenance={"orchestration.worktree.linkDirs": "project"},
        layers=(SettingsLayer(name="project", path=project_dir / ".orin" / "settings.json", data={}),),
        errors=(),
        trusted=False,
    )
    fake_session2 = SimpleNamespace(effective_settings=manual_untrusted, _workspace_trusted=lambda: False)
    _en2, link_dirs2, _cmd2 = _worktree_settings(fake_session2)
    assert link_dirs2 == DEFAULT_WORKTREE_LINK_DIRS


# ---------------------------------------------------------------------------
# RT-B-10: POST .../worktrees/{agent_id}/discard ownership & running guard
# ---------------------------------------------------------------------------


def test_rt_b_10_discard_worktree_enforces_conversation_ownership_and_rejects_running(tmp_path: Path) -> None:
    shared_repo = tmp_path / "shared_repo"
    _init_git_repo(shared_repo)

    engine, store, chat_app, client = _setup_api_client(tmp_path, workspace_root=tmp_path / "ws")
    BACKGROUND_AGENTS.clear_for_tests()
    try:
        # Two conversations belonging to user-1, sharing the exact same workspace root!
        c1 = chat_app.create(
            user_id="user-1",
            message="c1",
            provider="openai",
            model_id="gpt-4o",
            idempotency_key="idem-wt-1",
        )
        c2 = chat_app.create(
            user_id="user-1",
            message="c2",
            provider="openai",
            model_id="gpt-4o",
            idempotency_key="idem-wt-2",
        )
        from agentos.local_workspace import PostgresLocalWorkspaceStore

        lw = PostgresLocalWorkspaceStore(engine)
        lw.set_root(c1.conversation_id, "user-1", str(shared_repo))
        lw.set_root(c2.conversation_id, "user-1", str(shared_repo))

        wt_store = AgentWorktreeStore(engine)
        manager = AgentWorktreeManager(shared_repo, store=wt_store)
        wt = manager.create_for_agent("agent-c1-running", shared_repo, link_dirs=(), store=wt_store)

        AgentRunStore(engine).upsert(
            agent_id="agent-c1-running",
            user_id="user-1",
            conversation_id=c1.conversation_id,
            turn_id=c1.turn_id,
            subagent_type="general",
            state="running",
            worktree_path=str(wt.path),
        )

        # 1. Cross-conversation discard on the same workspace -> 404
        res_other_conv = client.post(
            f"/v1/conversations/{c2.conversation_id}/worktrees/agent-c1-running/discard",
            headers={"Authorization": "Bearer pat-u1"},
        )
        assert res_other_conv.status_code == 404, res_other_conv.text
        assert wt.path.is_dir()

        # 2. Discard while subagent is still running -> 409 Conflict
        res_running = client.post(
            f"/v1/conversations/{c1.conversation_id}/worktrees/agent-c1-running/discard",
            headers={"Authorization": "Bearer pat-u1"},
        )
        assert res_running.status_code == 409, res_running.text
        assert wt.path.is_dir()

        # 3. Once subagent completes/fails, owner conversation can discard -> 200
        AgentRunStore(engine).touch("agent-c1-running", state="failed")
        res_ok = client.post(
            f"/v1/conversations/{c1.conversation_id}/worktrees/agent-c1-running/discard",
            headers={"Authorization": "Bearer pat-u1"},
        )
        assert res_ok.status_code == 200, res_ok.text
        assert not wt.path.exists()
    finally:
        BACKGROUND_AGENTS.clear_for_tests()
        engine.dispose()


# ---------------------------------------------------------------------------
# RT-B-11: _snapshot_parent_files & _rollback_parent preserve st_mode & symlinks
# ---------------------------------------------------------------------------


def test_rt_b_11_rollback_preserves_st_mode_and_parent_symlinks(tmp_path: Path) -> None:
    repo = tmp_path / "repo_mode"
    _init_git_repo(repo)
    script = repo / "run.sh"
    script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    # On POSIX test executable bit 0o755; on Windows test read-only bit 0o444 so os.chmod changes are observable.
    target_mode = 0o444 if os.name == "nt" else 0o755
    os.chmod(script, target_mode)
    original_mode = stat.S_IMODE(os.lstat(script).st_mode)

    target_real = repo / "real.txt"
    target_real.write_text("target content", encoding="utf-8")
    link_file = repo / "link.txt"
    created_symlink = False
    try:
        os.symlink("real.txt", link_file)
        created_symlink = link_file.is_symlink()
    except OSError:
        created_symlink = False

    mq = MergeQueue(repo)
    rel_paths = ["run.sh", *(["link.txt"] if created_symlink else [])]
    snap = mq._snapshot_parent_files(repo, rel_paths)
    idx_snap = mq._snapshot_parent_index(repo, rel_paths)

    # Snapshot must capture structured metadata (mode & symlink_target), not raw bytes only.
    assert hasattr(snap["run.sh"], "mode")
    assert snap["run.sh"].mode == original_mode

    # Mutate files on disk as if a patch altered mode / replaced symlink
    os.chmod(script, 0o644)
    script.write_text("#!/bin/sh\necho mutated\n", encoding="utf-8")
    if created_symlink:
        link_file.unlink()
        link_file.write_text("overwritten regular file", encoding="utf-8")

    try:
        mq._rollback_parent(repo, rel_paths, snap, idx_snap)
        assert script.read_text(encoding="utf-8") == "#!/bin/sh\necho ok\n"
        assert stat.S_IMODE(os.lstat(script).st_mode) == original_mode
        if created_symlink:
            assert link_file.is_symlink()
            assert os.readlink(link_file) == "real.txt"
            assert target_real.read_text(encoding="utf-8") == "target content"
    finally:
        # Make writable again so pytest tmp_path cleanup succeeds on Windows
        if script.exists():
            os.chmod(script, 0o644)


# ---------------------------------------------------------------------------
# RT-B-12: TaskBoard rejects self-dependency and cycles (A -> B -> A)
# ---------------------------------------------------------------------------


def test_rt_b_12_task_board_rejects_self_dependency_and_cycles(tmp_path: Path) -> None:
    engine = create_local_engine(f"sqlite:///{(tmp_path / 'rt_b_12.db').as_posix()}")
    metadata.create_all(engine)
    try:
        for mode in ("sql", "memory"):
            board = (
                TaskBoard(conversation_id=f"conv-12-{mode}", engine=engine)
                if mode == "sql"
                else TaskBoard(conversation_id=f"conv-12-{mode}")
            )
            # Self-dependency in create
            with pytest.raises(ValueError):
                board.create(title="Self", item_id=f"self-{mode}", blocked_by=[f"self-{mode}"])

            a = board.create(title="Task A", item_id=f"A-{mode}")
            b = board.create(title="Task B", item_id=f"B-{mode}", blocked_by=[a.item_id])
            c = board.create(title="Task C", item_id=f"C-{mode}", blocked_by=[b.item_id])

            # Self-dependency in update(blocked_by=...)
            with pytest.raises(ValueError):
                board.update(a.item_id, blocked_by=[a.item_id])
            with pytest.raises(ValueError):
                board.update(a.item_id, add_blocked_by=[a.item_id])
            with pytest.raises(ValueError):
                board.update(a.item_id, add_blocks=[a.item_id])

            # Direct cycle A -> B -> A via blocked_by / add_blocked_by
            with pytest.raises(ValueError):
                board.update(a.item_id, blocked_by=[b.item_id])
            with pytest.raises(ValueError):
                board.update(a.item_id, add_blocked_by=[b.item_id])

            # Transitive 3-node cycle A -> C -> B -> A via add_blocked_by and add_blocks
            with pytest.raises(ValueError):
                board.update(a.item_id, add_blocked_by=[c.item_id])
            with pytest.raises(ValueError):
                board.update(c.item_id, add_blocks=[a.item_id])

            # Original valid graph remained intact
            assert board.get(a.item_id).blocked_by == ()
            assert board.get(b.item_id).blocked_by == (a.item_id,)
            assert board.get(c.item_id).blocked_by == (b.item_id,)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# RT-B-13: Concurrent POST /resume atomic race + autoCancel keeps 'cancelled'
# ---------------------------------------------------------------------------


def test_rt_b_13_concurrent_resume_is_atomic_and_autocancel_not_overwritten_by_failed(
    tmp_path: Path, monkeypatch
) -> None:
    runs_dir = tmp_path / "agent-runs"
    monkeypatch.setenv("ORIN_AGENT_RUNS_DIR", str(runs_dir))

    engine, store, chat_app, client = _setup_api_client(tmp_path)
    try:
        c1 = chat_app.create(
            user_id="user-1",
            message="concurrent resume",
            provider="openai",
            model_id="gpt-4o",
            idempotency_key="idem-rt-b-13",
        )
        conv_id = c1.conversation_id
        agents = ConversationAgentStore(engine, conversation_id=conv_id, user_id="user-1")
        rec = agents.create("worker-race", "general", parent_agent_id=f"agent:{conv_id}:main")
        aid = str(rec["agent_id"])
        agents.set_state(aid, "interrupted")

        recorder = AgentRunRecorder(engine, runs_dir=runs_dir)
        recorder.start(
            agent_id=aid,
            user_id="user-1",
            conversation_id=conv_id,
            turn_id=c1.turn_id,
            subagent_type="general",
            state="interrupted",
        )

        barrier = threading.Barrier(2, timeout=5)

        def _hit_resume() -> int:
            barrier.wait()
            res = client.post(
                f"/v1/conversations/{conv_id}/agents/{aid}/resume",
                headers={"Authorization": "Bearer pat-u1"},
            )
            return res.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(_hit_resume)
            f2 = pool.submit(_hit_resume)
            statuses = sorted([f1.result(timeout=10), f2.result(timeout=10)])

        assert statuses == [202, 409], f"Expected exactly one 202 and one 409, got {statuses}"

        # Part 2: ProgressTracker.finish(state="failed") must NOT overwrite state="cancelled" from stall.autoCancel
        t = [100.0]
        tracker = ProgressTracker(clock=lambda: t[0], recorder=recorder)
        aid2 = "agent-autocancel-13"
        recorder.start(
            agent_id=aid2,
            user_id="user-1",
            conversation_id=conv_id,
            turn_id=c1.turn_id,
            subagent_type="general",
            state="running",
        )
        tracker.start(aid2, conversation_id=conv_id)
        t[0] += 600.0
        stalled = tracker.check_stall(aid2, stall_minutes=1.0, auto_cancel=True)
        assert stalled is not None
        assert stalled.state == "cancelled"
        assert stalled.stalled is True

        # Simulates _execute_subagent finally calling finish(aid2, state="failed")
        after_finish = tracker.finish(aid2, state="failed")
        assert after_finish is not None
        assert after_finish.state == "cancelled"
        assert after_finish.stalled is True
        assert AgentRunStore(engine).get(aid2).state == "cancelled"
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# RT-B-14: Saving agent with default isolation ('none') omits isolation in frontmatter
# ---------------------------------------------------------------------------


def test_rt_b_14_write_agent_omits_default_isolation_none_from_frontmatter(tmp_path: Path) -> None:
    target_none = write_agent(
        tmp_path,
        "clean-agent",
        AgentFile(
            scope="project",
            description="Clean agent without worktree isolation",
            prompt="Help cleanly.",
            isolation="none",
        ),
    )
    text_none = target_none.read_text(encoding="utf-8")
    assert "isolation:" not in text_none

    target_wt = write_agent(
        tmp_path,
        "isolated-agent",
        AgentFile(
            scope="project",
            description="Agent with worktree isolation",
            prompt="Run isolated.",
            isolation="worktree",
        ),
    )
    text_wt = target_wt.read_text(encoding="utf-8")
    assert "isolation: worktree" in text_wt or 'isolation: "worktree"' in text_wt

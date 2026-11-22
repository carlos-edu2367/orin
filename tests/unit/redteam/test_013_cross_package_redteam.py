"""Cross-package holistic Red Team regression suite for Orin 0.13.0 (RT-FINAL-01..RT-FINAL-05)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any

from sqlalchemy import create_engine, text

from agentos.agentic.change_measure import measure_turn_changes
from agentos.agentic.session import TurnSession
from agentos.agentic.subagent_memory import build_subagent_memory_context, subagent_memory_turn_state
from agentos.agentic.subagent_worktree import (
    ActiveSubagentWorktree,
    finalize_subagent_worktree,
    prepare_subagent_worktree,
)
from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.agent_worktree import AgentWorktreeManager
from agentos.code_mode.file_history import FileHistory
from agentos.code_mode.merge_queue import MergeQueue
from agentos.memory.agent_scope import AgentMemoryScope
from agentos.memory.provenance import infer_source_kind, session_memory_turn_state
from agentos.persistence.postgres.agent_memory import PostgresAgentMemoryStore
from agentos.persistence.postgres.migrate import downgrade, upgrade
from agentos.persistence.sqlite import create_local_engine
from agentos.settings.loader import EffectiveSettings, SettingsLayer


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


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "redteam@orin.test")
    _git(repo, "config", "user.name", "RedTeam")
    _git(repo, "config", "core.autocrlf", "false")
    lines = [f"line_{i}\n" for i in range(1, 26)]
    (repo / "app.py").write_text("".join(lines), encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "initial")


def _settings(values: dict[str, Any]) -> EffectiveSettings:
    return EffectiveSettings(
        data=values,
        provenance={},
        layers=(SettingsLayer(name="user", path=None, data=values),),
        errors=(),
        trusted=True,
    )


# ---------------------------------------------------------------------------
# RT-FINAL-01 (HIGH): MergeQueue with unstaged tracked + copied untracked
# files from parent must apply 3-way cleanly without touching parent .git/index
# ---------------------------------------------------------------------------


def test_rt_final_01_merge_queue_handles_parent_unstaged_and_untracked_without_dirtying_index(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    # 1. Parent has unstaged modification in tracked app.py AND an untracked new_feature.py
    parent_lines = (repo / "app.py").read_text(encoding="utf-8").splitlines(keepends=True)
    parent_lines[0] = "line_1_parent_unstaged\n"
    (repo / "app.py").write_text("".join(parent_lines), encoding="utf-8")

    (repo / "new_feature.py").write_text(
        "def greet():\n    return 'hello from parent'\n\ndef tail():\n    return 1\n",
        encoding="utf-8",
    )

    # Snapshot parent's real .git/index before worktree creation / merge
    index_path = repo / ".git" / "index"
    index_bytes_before = index_path.read_bytes()
    staged_before = _git(repo, "diff", "--cached", "--name-only").stdout

    mgr = AgentWorktreeManager(repo)
    wt = mgr.create_for_agent("ag-rt01", repo, link_dirs=())

    meta = AgentWorktreeManager.read_agent_meta(wt.path)
    assert "new_feature.py" in (meta.get("copied_files_blobs") or {})

    # 2. Subagent edits both app.py (far line) and new_feature.py inside worktree
    wt_lines = (wt.path / "app.py").read_text(encoding="utf-8").splitlines(keepends=True)
    assert wt_lines[0] == "line_1_parent_unstaged\n"
    wt_lines[20] = "line_21_child_edit\n"
    (wt.path / "app.py").write_text("".join(wt_lines), encoding="utf-8")

    wt_feat = (wt.path / "new_feature.py").read_text(encoding="utf-8")
    (wt.path / "new_feature.py").write_text(
        wt_feat.replace("return 1", "return 42"),
        encoding="utf-8",
    )

    # 3. MergeQueue applies 3-way cleanly
    result = MergeQueue(repo, manager=mgr).enqueue("ag-rt01")
    assert result.state == "applied", f"Expected applied, got {result}"
    assert set(result.files) == {"app.py", "new_feature.py"}

    merged_app = (repo / "app.py").read_text(encoding="utf-8")
    assert "line_1_parent_unstaged" in merged_app
    assert "line_21_child_edit" in merged_app

    merged_feat = (repo / "new_feature.py").read_text(encoding="utf-8")
    assert "return 42" in merged_feat
    assert "hello from parent" in merged_feat

    # 4. Parent's real .git/index must remain completely untouched
    assert index_path.read_bytes() == index_bytes_before
    assert _git(repo, "diff", "--cached", "--name-only").stdout == staged_before


# ---------------------------------------------------------------------------
# RT-FINAL-02 (HIGH): MergeQueue + FileHistory + FileCheckpointStore +
# VerificationGate + K18 propagation (including Bash edits)
# ---------------------------------------------------------------------------


def test_rt_final_02_worktree_merge_snapshots_parent_history_checkpoint_and_propagates_bash_changes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    # Make app.py dirty BEFORE the turn (e.g. 20 lines modified vs HEAD)
    dirty_before = "".join(f"dirty_pre_turn_{i}\n" for i in range(1, 26))
    (repo / "app.py").write_text(dirty_before, encoding="utf-8")

    history_dir = tmp_path / "file_history"
    fh = FileHistory(history_dir, "conv-rt02")
    turn_id = "turn-rt02"
    user_msg_id = "msg-rt02"

    parent_ws = ConversationWorkspace(repo)
    parent_tools = AgentToolset(
        parent_ws,
        turn_id=turn_id,
        file_history=fh,
        user_message_id=user_msg_id,
    )

    recorded: list[tuple[Any, str, dict[str, Any]]] = []

    class _FakeSession:
        def __init__(self) -> None:
            self.workspace = parent_ws
            self.file_history = fh
            self.toolset = parent_tools
            self._main_toolset_ref = parent_tools
            self.turn = {"turn_id": turn_id, "user_message_id": user_msg_id, "conversation_id": "conv-rt02"}
            self.effective_settings = _settings({"orchestration": {"worktree": {"enabled": True}}})
            self.store = None
            self.agents_store = None

        def _workspace_trusted(self) -> bool:
            return True

        def _record(self, event_type: Any, message: str, data: dict[str, Any], **_kw: Any) -> None:
            recorded.append((event_type, message, data))

    session = _FakeSession()
    active_wt = prepare_subagent_worktree(
        session,
        agent_id="ag-rt02",
        parent_agent_id="main",
        requested_isolation="worktree",
        definition=None,
    )
    assert isinstance(active_wt, ActiveSubagentWorktree)

    # Verify TurnSession._toolset passes file_history=None when workspace_override is set
    sub_tools = TurnSession._toolset(
        SimpleNamespace(
            workspace=parent_ws,
            memory=None,
            effective_settings=session.effective_settings,
            turn=session.turn,
            file_history=fh,
            _create_agent=lambda *a, **k: None,
            _ask_agent=lambda *a, **k: None,
            _ask_agents=lambda *a, **k: None,
            _run_subagent=lambda *a, **k: None,
            _check_subagent=lambda *a, **k: None,
            close_background_subagents=lambda: None,
            emit_lifecycle=lambda *a, **k: None,
            lifecycle=None,
            _hook_extra_env=lambda: {},
            cancelled=lambda *a, **k: False,
            skills=None,
            skill_library=None,
            main_agent_id="main",
            skill_load_recorder=None,
            search_client=None,
            retrieval=None,
            _retrieval_bundle=None,
            permission_mode="auto",
            browser=None,
            browser_capability=None,
            tool_policy=None,
            _visual_reader=None,
            mcp_provider=None,
            mcp_service=None,
            plugin_service=None,
            enable_terminal=True,
            host_folders=None,
            _web_answer=None,
            _read_file_state={},
            _todos_state=[],
            project_store=None,
            chat_store=None,
            local_workspaces=None,
            provider_catalog=None,
            on_session_watch=None,
            media_policy=None,
            media_client=None,
            _turn_scheduler=lambda: None,
            _schedule_context=lambda: None,
            _push_notification=None,
            _notification_budget=None,
            _disk_skills=None,
            _skill_hooks=None,
            _allowed_requested_model=lambda *a, **k: None,
            _lsp_after_write=None,
            _stop_background_subagent=None,
            _send_team_message=None,
            _team_roster=lambda: [],
            _parent_message_tool_enabled=lambda: True,
            _has_living_children=lambda *a, **k: False,
            _send_agent_message=None,
            _note_queued_input_claimed=None,
            _in_plan_mode=False,
            _approval_dialogue_pairs=lambda: [],
            _record=lambda *a, **k: None,
            _task_board_for_turn=lambda: None,
            _shared_task_board=lambda: None,
            _browser_for=lambda *a, **k: None,
            _code_mode_grants=lambda *a, **k: {},
            _effective_permission_mode=lambda *a, **k: "auto",
            _effective_code_mode_autonomy=lambda *a, **k: "full",
            _effective_code_mode_work_kind=lambda *a, **k: "general",
            _code_mode_active=lambda: True,
        ),
        workspace_override=active_wt.workspace,
        subagents=False,
        share_read_state=False,
    )
    assert sub_tools._file_history is None

    # Simulate subagent editing app.py back to HEAD-like lines via Bash (so sub_tools._changed_paths stays empty!)
    child_content = "".join(f"line_{i}\n" for i in range(1, 26))
    (active_wt.worktree.path / "app.py").write_text(child_content, encoding="utf-8")
    assert not sub_tools._changed_paths

    # Finalize worktree -> MergeQueue applies to parent, snapshotting parent's checkpoint_store & file_history
    wt_payload, _note = finalize_subagent_worktree(
        session,
        active_wt,
        agent_id="ag-rt02",
        parent_agent_id="main",
        parent_tools=parent_tools,
    )
    assert wt_payload is not None
    assert wt_payload["merge_state"] == "applied"
    assert (repo / "app.py").read_text(encoding="utf-8") == child_content

    # (a) FileHistory has parent path only — NEVER .orin/worktrees/...
    fh_messages = fh.messages()
    assert len(fh_messages) == 1
    recorded_files = fh_messages[0]["files"]
    assert any(Path(p).resolve() == (repo / "app.py").resolve() for p in recorded_files)
    assert not any(".orin/worktrees" in p.replace("\\", "/") for p in recorded_files)

    # (b) parent_tools._checkpoint_store contains pre-merge snapshot for app.py (K18 & measure_turn_changes)
    cp = parent_tools._checkpoint_store.get_checkpoint(turn_id)
    assert cp is not None
    assert "app.py" in (cp.get("files_by_path") or {})

    # (c) Propagate applied files as session._execute_subagent_run_unlocked does and verify measure_turn_changes
    wt_applied_files = tuple((wt_payload.get("merge_detail") or {}).get("files") or ())
    assert "app.py" in wt_applied_files
    parent_tools._changed_paths.update(wt_applied_files)
    parent_tools._change_events += len(wt_applied_files)

    # Because parent_tools._checkpoint_store captured the dirty_before state, measure_turn_changes
    # sees 50 changed lines (25 removed + 25 added) even though the post-merge file matches HEAD!
    measured = measure_turn_changes(
        parent_tools,
        parent_tools._checkpoint_store,
        repo,
        turn_id=turn_id,
    )
    assert measured.files == 1
    assert "app.py" in measured.changed_files
    assert measured.lines >= 25

    # Rewinding FileHistory restores parent's pre-merge dirty content and NEVER recreates .orin/worktrees/...
    fh.rewind(user_msg_id, roots=[repo])
    assert (repo / "app.py").read_text(encoding="utf-8") == dirty_before
    wt_root_dir = repo / ".orin" / "worktrees"
    assert not (wt_root_dir / active_wt.worktree.slug).exists()


def test_rt_final_02_conflict_does_not_pollute_parent_checkpoint_or_file_history(tmp_path: Path) -> None:
    repo = tmp_path / "repo_conflict"
    _init_git_repo(repo)

    fh = FileHistory(tmp_path / "fh_conflict", "conv-c")
    turn_id = "turn-rt02-conflict"
    user_msg_id = "msg-rt02-conflict"
    parent_ws = ConversationWorkspace(repo)
    parent_tools = AgentToolset(
        parent_ws,
        turn_id=turn_id,
        file_history=fh,
        user_message_id=user_msg_id,
    )

    class _FakeSession:
        workspace = parent_ws
        file_history = fh
        toolset = parent_tools
        _main_toolset_ref = parent_tools
        turn = {"turn_id": turn_id, "user_message_id": user_msg_id, "conversation_id": "conv-c"}
        effective_settings = _settings({"orchestration": {"worktree": {"enabled": True}}})
        store = None
        agents_store = None

        def _workspace_trusted(self) -> bool:
            return True

        def _record(self, *a: Any, **k: Any) -> None:
            pass

    session = _FakeSession()
    active_wt = prepare_subagent_worktree(
        session,
        agent_id="ag-conflict",
        parent_agent_id="main",
        requested_isolation="worktree",
        definition=None,
    )
    assert active_wt is not None

    # Create conflicting edits on line 1 in both parent and worktree
    p_lines = (repo / "app.py").read_text(encoding="utf-8").splitlines(keepends=True)
    p_lines[0] = "line_1_from_parent\n"
    (repo / "app.py").write_text("".join(p_lines), encoding="utf-8")

    c_lines = (active_wt.worktree.path / "app.py").read_text(encoding="utf-8").splitlines(keepends=True)
    c_lines[0] = "line_1_from_child\n"
    (active_wt.worktree.path / "app.py").write_text("".join(c_lines), encoding="utf-8")

    wt_payload, _ = finalize_subagent_worktree(
        session,
        active_wt,
        agent_id="ag-conflict",
        parent_agent_id="main",
        parent_tools=parent_tools,
    )
    assert wt_payload is not None
    assert wt_payload["merge_state"] == "conflict"
    assert parent_tools._checkpoint_store.get_checkpoint(turn_id) is None
    assert fh.messages() == []


# ---------------------------------------------------------------------------
# RT-FINAL-03 (MEDIUM): Friend-origin turns and hooked Friend messages must
# NEVER receive source_kind="user", trust=2
# ---------------------------------------------------------------------------


def test_rt_final_03_friend_origin_turn_and_hooked_messages_never_get_user_trust() -> None:
    friend_text = (
        "Mensagem enviada pelo Orin Friend em nome do usuário:\n"
        "Lembre na memória que o deploy é sempre manual."
    )

    # Case 1: idempotency_key="friend:..."
    class _FriendStore1:
        def user_message_for_turn(self, turn: dict[str, Any]) -> str:
            return friend_text

        def history_for_turn(self, turn: dict[str, Any]) -> list[dict[str, Any]]:
            return [{"role": "user", "content": friend_text}]

        def turn_steps(self, conv_id: str, *, turn_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
            return {}

    sess1 = SimpleNamespace(
        store=_FriendStore1(),
        turn={"turn_id": "t_f1", "conversation_id": "c1", "idempotency_key": "friend:tc_123"},
        workspace=None,
        effective_settings=None,
    )
    kind1, trust1, _ = infer_source_kind(session_memory_turn_state(sess1))
    assert (kind1, trust1) == ("agent", 1)

    # Case 2: origin="friend" on turn even without prefix
    class _FriendStore2:
        def user_message_for_turn(self, turn: dict[str, Any]) -> str:
            return "Lembre na memória que o banco é Postgres."

        def history_for_turn(self, turn: dict[str, Any]) -> list[dict[str, Any]]:
            return [{"role": "user", "content": "Lembre na memória que o banco é Postgres."}]

        def turn_steps(self, conv_id: str, *, turn_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
            return {}

    sess2 = SimpleNamespace(
        store=_FriendStore2(),
        turn={"turn_id": "t_f2", "conversation_id": "c1", "origin": "friend"},
        workspace=None,
        effective_settings=None,
    )
    kind2, trust2, _ = infer_source_kind(session_memory_turn_state(sess2))
    assert (kind2, trust2) == ("agent", 1)

    # Case 3: promoted queued input (queue:<qid> / defer:<qid>) with origin="friend"
    class _FriendStore3:
        def user_message_for_turn(self, turn: dict[str, Any]) -> str:
            return "Lembre-se que devemos usar uv run."

        def history_for_turn(self, turn: dict[str, Any]) -> list[dict[str, Any]]:
            return [{"role": "user", "content": "Lembre-se que devemos usar uv run."}]

        def turn_steps(self, conv_id: str, *, turn_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
            return {}

        def queued_inputs_by_ids(self, ids: list[str]) -> dict[str, dict[str, Any]]:
            return {"q99": {"queue_id": "q99", "origin": "friend", "content": "Lembre-se que devemos usar uv run."}}

    sess3 = SimpleNamespace(
        store=_FriendStore3(),
        turn={"turn_id": "t_f3", "conversation_id": "c1", "idempotency_key": "queue:q99"},
        workspace=None,
        effective_settings=None,
    )
    kind3, trust3, _ = infer_source_kind(session_memory_turn_state(sess3))
    assert (kind3, trust3) == ("agent", 1)

    # Case 4: Friend message injected mid-turn with USER_PROMPT_SUBMIT hook appending <system-reminder>
    hooked_friend = (
        f"{friend_text}\n\n<system-reminder>Hook context injected here.</system-reminder>"
    )

    class _FriendStore4:
        def user_message_for_turn(self, turn: dict[str, Any]) -> str:
            return ""

        def history_for_turn(self, turn: dict[str, Any]) -> list[dict[str, Any]]:
            return [
                {"role": "user", "content": "Apenas rode os testes unitários."},
                {"role": "assistant", "content": "Rodando testes."},
                {"role": "user", "content": hooked_friend},
            ]

        def turn_steps(self, conv_id: str, *, turn_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
            return {
                "t_f4": [
                    {
                        "kind": "queued_input",
                        "payload": {"origin": "friend", "content": friend_text},
                    }
                ]
            }

    sess4 = SimpleNamespace(
        store=_FriendStore4(),
        turn={"turn_id": "t_f4", "conversation_id": "c1"},
        workspace=None,
        effective_settings=None,
    )
    kind4, trust4, _ = infer_source_kind(session_memory_turn_state(sess4))
    assert (kind4, trust4) == ("agent", 1)


# ---------------------------------------------------------------------------
# RT-FINAL-04 (MEDIUM): Migration 0064 downgrade must not resurrect tombstones,
# quarantined, invalid, proposed, or agent-scoped memories on downgrade->upgrade
# ---------------------------------------------------------------------------


def test_rt_final_04_migration_0064_downgrade_does_not_resurrect_non_active_or_agent_memories(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rt04.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        upgrade(engine, "head")
        now = datetime(2026, 11, 21, 12, 0, 0, tzinfo=UTC)
        later = now + timedelta(minutes=5)

        insert_sql = text(
            "INSERT INTO agent_memories ("
            "memory_id, user_id, conversation_id, scope_type, scope_id, project_id, "
            "fact, tags, kind, confidence, emotional_valence, salience, source, "
            "hit_count, superseded_by, created_at, updated_at, "
            "status, agent_key, text_hash, valid_from, invalid_at, source_kind, source_ref, trust"
            ") VALUES ("
            ":memory_id, 'u1', 'c1', :scope_type, 'u1', NULL, "
            ":fact, '[]', 'fact', 0.9, 0.0, 0.5, 'mechanical', "
            "0, :superseded_by, :created_at, :updated_at, "
            ":status, :agent_key, :text_hash, :valid_from, :invalid_at, 'agent', 'turn:1', 1"
            ")"
        )
        rows = [
            {
                "memory_id": "mem_active",
                "scope_type": "user",
                "fact": "Projeto usa Python 3.13.",
                "superseded_by": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "status": "active",
                "agent_key": None,
                "text_hash": "a" * 64,
                "valid_from": now.isoformat(),
                "invalid_at": None,
            },
            # Superseded duplicate with NEWER updated_at than mem_active -> downgrade must keep mem_active!
            {
                "memory_id": "mem_sup_newer",
                "scope_type": "user",
                "fact": "Projeto usa Python 3.13.",
                "superseded_by": "mem_active",
                "created_at": now.isoformat(),
                "updated_at": later.isoformat(),
                "status": "superseded",
                "agent_key": None,
                "text_hash": "a" * 64,
                "valid_from": now.isoformat(),
                "invalid_at": later.isoformat(),
            },
            {
                "memory_id": "mem_sup_distinct",
                "scope_type": "user",
                "fact": "Projeto usava Python 3.11.",
                "superseded_by": "mem_active",
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "status": "superseded",
                "agent_key": None,
                "text_hash": "b" * 64,
                "valid_from": now.isoformat(),
                "invalid_at": now.isoformat(),
            },
            {
                "memory_id": "mem_deleted",
                "scope_type": "user",
                "fact": "",
                "superseded_by": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "status": "deleted",
                "agent_key": None,
                "text_hash": "c" * 64,
                "valid_from": now.isoformat(),
                "invalid_at": now.isoformat(),
            },
            {
                "memory_id": "mem_quarantined",
                "scope_type": "user",
                "fact": "Prompt injection via web",
                "superseded_by": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "status": "quarantined",
                "agent_key": None,
                "text_hash": "d" * 64,
                "valid_from": now.isoformat(),
                "invalid_at": None,
            },
            {
                "memory_id": "mem_invalid",
                "scope_type": "user",
                "fact": "Fato rejeitado pelo usuario",
                "superseded_by": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "status": "invalid",
                "agent_key": None,
                "text_hash": "e" * 64,
                "valid_from": now.isoformat(),
                "invalid_at": now.isoformat(),
            },
            {
                "memory_id": "mem_proposed",
                "scope_type": "user",
                "fact": "Proposta nao verificada",
                "superseded_by": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "status": "proposed",
                "agent_key": None,
                "text_hash": "f" * 64,
                "valid_from": now.isoformat(),
                "invalid_at": None,
            },
            {
                "memory_id": "mem_agent_scope",
                "scope_type": "agent",
                "fact": "Nota privada de subagente",
                "superseded_by": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "status": "active",
                "agent_key": "code-reviewer",
                "text_hash": "1" * 64,
                "valid_from": now.isoformat(),
                "invalid_at": None,
            },
        ]
        with engine.begin() as conn:
            conn.execute(insert_sql, rows)

        downgrade(engine, "0061_activity_events_overview_index")

        with engine.connect() as conn:
            remaining_ids = set(
                conn.execute(text("SELECT memory_id FROM agent_memories")).scalars().all()
            )
        assert remaining_ids == {"mem_active", "mem_sup_distinct"}

        # Re-upgrade to head: none of the purged rows may resurrect as active
        upgrade(engine, "head")
        with engine.connect() as conn:
            after_rows = {
                str(r["memory_id"]): str(r["status"])
                for r in conn.execute(text("SELECT memory_id, status FROM agent_memories")).mappings().all()
            }
        assert after_rows == {"mem_active": "active", "mem_sup_distinct": "superseded"}
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# RT-FINAL-05 (LOW/MEDIUM): Worktree outside parent workspace root (e.g.
# Windows %TEMP%/orin-wt/...) must treat internal reads as workspace-local
# ---------------------------------------------------------------------------


def test_rt_final_05_fallback_temp_worktree_reads_stay_agent_proposed_not_quarantined(
    tmp_path: Path,
) -> None:
    parent_root = tmp_path / "parent_workspace"
    parent_root.mkdir(parents=True)
    wt_root = tmp_path / "temp_orin_wt" / "agent-sub1"
    wt_root.mkdir(parents=True)
    wt_file = wt_root / "module.py"
    wt_file.write_text("VALUE = 123\n", encoding="utf-8")

    engine = create_local_engine(f"sqlite:///{(tmp_path / 'rt05.db').as_posix()}")
    try:
        upgrade(engine, "head")
        mem_store = PostgresAgentMemoryStore(engine, "u-rt05")
        settings = _settings(
            {
                "memory": {
                    "agent": {"enabled": True},
                    "quarantine": {"enabled": True},
                }
            }
        )
        parent_ws = ConversationWorkspace(parent_root)
        wt_ws = ConversationWorkspace(wt_root)

        fake_session = SimpleNamespace(
            workspace=parent_ws,
            memory=mem_store,
            effective_settings=settings,
            turn={"turn_id": "t-rt05", "conversation_id": "c-rt05", "project_id": "proj-1", "user_id": "u-rt05"},
            store=None,
        )
        sub_view, _ = build_subagent_memory_context(
            fake_session,
            "reviewer",
            "reviewer",
            SimpleNamespace(name="reviewer", origin="custom", memory="project"),
        )
        assert sub_view is not None
        scope = sub_view.agent_scope

        toolset = AgentToolset(
            wt_ws,
            memory=sub_view,
            turn_id="t-rt05",
            conversation_id="c-rt05",
        )
        toolset._effective_settings = settings
        toolset._memory_turn_state_provider = lambda: subagent_memory_turn_state(fake_session, scope)

        # Simulate subagent reading a file inside its own %TEMP%/orin-wt worktree
        toolset._read_file_state[str(wt_file.resolve())] = {"mtime": 1.0}
        toolset._turn_tool_history = [{"tool_name": "read_file", "path": str(wt_file.resolve())}]

        result = toolset.remember(
            fact="Modulo usa VALUE = 123 como constante padrao.",
            scope="agent",
            kind="fact",
        )
        payload = result["payload"]
        assert payload["memory_id"]
        assert payload["source_kind"] == "agent"
        assert payload["status"] == "proposed"
        saved_id = payload["memory_id"]
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT status, source_kind, trust FROM agent_memories WHERE memory_id = :mid"),
                {"mid": saved_id},
            ).mappings().one()
        assert row["source_kind"] == "agent"
        assert row["trust"] == 1
        assert row["status"] == "proposed"
    finally:
        engine.dispose()

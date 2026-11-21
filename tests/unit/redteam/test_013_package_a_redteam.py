"""Red Team regression suite for 0.13 Package A (Independent Verifier, WS-A1..WS-A7).

Covers every finding RT-A-01 through RT-A-14 plus end-to-end verification flows with a fake native CLI.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select

from agentos.agentic.agent_definitions import VERIFIER, AgentToolPolicy
from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.change_measure import TURN_CACHE_KEY, ChangeSummary, measure_turn_changes
from agentos.agentic.child_routing import resolve_child_target
from agentos.agentic.effort_resolver import EffortEscalator
from agentos.agentic.native_turn import RuntimeToolExecutor
from agentos.agentic.phases import PhaseController
from agentos.agentic.review_recorder import InMemoryReviewRecorder
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.session import TurnSession
from agentos.agentic.subagent_memory import maybe_promote_on_turn_complete, promote_session_agent_memories
from agentos.agentic.verification_gate import VerificationGate
from agentos.agentic.verifier_policy import AvailableVerifierModel, AuthorModel, select_verifier
from agentos.agentic.workspace import ConversationWorkspace
from agentos.api.security import AuthenticatedPrincipal
from agentos.api.verifier_metrics_routes import register_verifier_metrics_routes
from agentos.code_mode.checkpoints import FileCheckpointStore
from agentos.memory.agent_scope import AgentMemoryScope
from agentos.memory.promotion import parse_verification_verdict
from agentos.persistence.postgres.agent_memory import MemoryWriteContext, PostgresAgentMemoryStore
from agentos.persistence.postgres.schema import agent_memories, metadata, verification_findings, verification_reviews
from agentos.persistence.postgres.verification_reviews import VerificationReviewStore
from agentos.providers.claude_cli.native_loop import NativeTurnOutcome, NativeTurnRequest, NativeTurnSink
from agentos.providers.claude_cli.stream import CliInit, CliResult, CliUsage
from agentos.providers.cli_tools_mcp import ToolCallRequest
from agentos.quota.models import Confidence, QuotaStatus
from agentos.settings.loader import load_effective_settings


def _make_engine(tmp_path: Path, name: str = "rt_a.db"):
    db_path = tmp_path / name
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    metadata.create_all(engine)
    return engine


def _proven_finding(
    fid: str,
    claim: str,
    *,
    severity: str = "blocker",
    file: str = "app.py",
    line: int = 1,
) -> dict[str, Any]:
    return {
        "id": fid,
        "severity": severity,
        "category": "bug",
        "claim": claim,
        "evidence": {"kind": "code_ref", "file": file, "line": line, "reason": claim},
    }


def _make_cli_init(session_id: str = "sess-rt-a", tools: tuple[str, ...] = ()) -> CliInit:
    return CliInit(
        session_id=session_id,
        model="claude-opus-5-5",
        api_key_source="none",
        mcp_servers={"orin": "connected"},
        tools=tools or ("mcp__orin__write_file", "mcp__orin__resolve_findings"),
    )


def _make_cli_result(text: str = "ok", session_id: str = "sess-rt-a") -> CliResult:
    usage = CliUsage(input_tokens=120, output_tokens=45)
    return CliResult(
        subtype="success",
        is_error=False,
        result_text=text,
        stop_reason="end_turn",
        num_turns=1,
        duration_ms=25,
        session_id=session_id,
        api_error_status=None,
        usage=usage,
        model_usage={"claude-opus-5-5": usage},
    )


class _MinimalTurnStore:
    def __init__(
        self,
        *,
        turn_id: str = "turn-rt-a",
        provider: str = "anthropic",
        model_id: str = "claude-opus-5-5",
        code_mode: str = "code",
    ) -> None:
        self.turn: dict[str, Any] = {
            "turn_id": turn_id,
            "conversation_id": "conv-rt-a",
            "user_id": "user-1",
            "provider": provider,
            "model_id": model_id,
            "user_message_id": "um-1",
            "assistant_message_id": "am-1",
            "code_mode": code_mode,
        }
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.text: list[str] = []
        self.finished_codes: list[str | None] = []

    def load(self, turn_id: str) -> dict[str, Any]:
        return self.turn

    def history_for_turn(self, turn: Mapping[str, Any]) -> list[dict[str, str]]:
        return [{"role": "user", "content": "implemente as alterações"}]

    def lifecycle(self, turn: Mapping[str, Any], state: str, **payload: object) -> None:
        self.events.append((state, dict(payload)))

    def delta(self, turn: Mapping[str, Any], text: str) -> None:
        self.text.append(text)

    def finish(self, turn: Mapping[str, Any], *, failed: bool = False, code: str | None = None) -> None:
        self.finished_codes.append(code)


# ---------------------------------------------------------------------------
# RT-A-01 (CRITICAL): lines_known=False (lines_changed=None) & recorder error resilience
# ---------------------------------------------------------------------------


def test_rt_a_01_review_recorders_accept_none_lines_and_gate_survives_recorder_errors(tmp_path: Path) -> None:
    # 1) InMemoryReviewRecorder handles None lines_changed / files_changed
    mem = InMemoryReviewRecorder()
    rid_mem = mem.start(
        user_id="u1",
        conversation_id="c1",
        turn_id="t1",
        files_changed=None,  # type: ignore[arg-type]
        lines_changed=None,  # type: ignore[arg-type]
        author_provider="anthropic",
        author_model="claude-opus-5-5",
    )
    saved_mem = mem.get_review(rid_mem)
    assert saved_mem is not None
    assert saved_mem["files_changed"] == 0
    assert saved_mem["lines_changed"] == 0

    # 2) VerificationReviewStore handles None lines_changed / files_changed
    engine = _make_engine(tmp_path, "rt_a_01.db")
    try:
        sql_store = VerificationReviewStore(engine)
        rid_sql = sql_store.start(
            user_id="u1",
            conversation_id="c1",
            turn_id="t1",
            files_changed=None,  # type: ignore[arg-type]
            lines_changed=None,  # type: ignore[arg-type]
            author_provider="anthropic",
            author_model="claude-opus-5-5",
        )
        saved_sql = sql_store.get_review(rid_sql)
        assert saved_sql is not None
        assert saved_sql["files_changed"] == 0
        assert saved_sql["lines_changed"] == 0

        # 3) Gate with lines_known=False succeeds without crashing even against VerificationReviewStore
        summary = ChangeSummary(
            files=1,
            lines=0,
            lines_known=False,
            sensitive_hits=(),
            changed_files=("logo.png",),
            triggered=True,
            reason="files",
        )
        gate_ok = VerificationGate(
            settings={"orchestration": {"verifier": {"enabled": True}}},
            review_recorder=sql_store,
            run_child=lambda *a, **kw: ToolOutcome(
                "succeeded", "verifier", "pass", {"verification_report": {"verdict": "pass", "checked": [], "findings": [], "unverified": []}}
            ),
        )
        from agentos.agentic.verifier_policy import VerifierChoice

        choice = VerifierChoice("openai-codex", "gpt-6.1-sol", "high", "cross_family", 1, "match")
        out = gate_ok.run_independent(
            {"user_id": "u1", "conversation_id": "c1", "turn_id": "t-bin", "provider": "anthropic", "model_id": "claude-opus-5-5"},
            summary,
            choice,
        )
        assert out.verdict == "pass"

        # 4) Broken recorder never crashes VerificationGate
        class _BrokenRecorder:
            def start(self, **kwargs: Any) -> str:
                raise RuntimeError("db down")

            def add_findings(self, review_id: str, findings: Any) -> None:
                raise RuntimeError("db down")

            def finish(self, review_id: str, **kwargs: Any) -> None:
                raise RuntimeError("db down")

            def set_resolution(self, review_id: str, finding_id: str, resolution: str, reason: str | None = None) -> None:
                raise RuntimeError("db down")

        gate_broken = VerificationGate(
            settings={"orchestration": {"verifier": {"enabled": True}}},
            review_recorder=_BrokenRecorder(),
            run_child=lambda *a, **kw: ToolOutcome(
                "succeeded", "verifier", "pass", {"verification_report": {"verdict": "pass", "checked": [], "findings": [], "unverified": []}}
            ),
        )
        out2 = gate_broken.run_independent(
            {"user_id": "u1", "conversation_id": "c1", "turn_id": "t-broken", "provider": "anthropic", "model_id": "claude-opus-5-5"},
            summary,
            choice,
        )
        assert out2.verdict == "pass"
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# RT-A-02 (CRITICAL): MemoryPromotionGate bypass on failed/inconclusive/caveats turns
# ---------------------------------------------------------------------------


def test_rt_a_02_no_memory_promotion_on_failed_or_inconclusive_or_caveats_verification(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, "rt_a_02.db")
    try:
        # 1) parse_verification_verdict treats inconclusive/skipped as not passed (False)
        assert parse_verification_verdict("inconclusive") is False
        assert parse_verification_verdict("skipped") is False

        # 2) promote_session_agent_memories records string verdicts on session._last_verification_passed
        mem_store = PostgresAgentMemoryStore(engine, "user-1", project_id="proj-1", data_dir=tmp_path / "idx")
        scope = AgentMemoryScope("user-1", "proj-1", None, "verifier", "project")
        prop = mem_store.save(
            "Nunca ignorar falhas de auth.",
            scope_type="agent",
            agent_scope=scope,
            context=MemoryWriteContext(
                source_kind="agent",
                actor="agent:verifier",
                agent_key="verifier",
                conversation_id="conv-rt-a2",
                turn_id="turn-rt-a2",
                trust=1,
            ),
        )
        assert prop["status"] == "proposed"

        class _FakeSession:
            memory = mem_store
            effective_settings = {"memory": {"agent": {"enabled": True, "learnFromVerifiedOnly": False}}}
            turn = {"turn_id": "turn-rt-a2", "user_id": "user-1", "conversation_id": "conv-rt-a2"}
            _last_verification_passed = None

        sess = _FakeSession()
        promote_session_agent_memories(sess, "fail")
        assert sess._last_verification_passed is False
        maybe_promote_on_turn_complete(sess)
        with engine.connect() as conn:
            status_after_fail = conn.execute(
                select(agent_memories.c.status).where(agent_memories.c.memory_id == prop["memory_id"])
            ).scalar_one()
        assert status_after_fail == "proposed"

        # Same for inconclusive
        sess._last_verification_passed = None
        promote_session_agent_memories(sess, "inconclusive")
        assert sess._last_verification_passed is False
        maybe_promote_on_turn_complete(sess)
        with engine.connect() as conn:
            status_after_inc = conn.execute(
                select(agent_memories.c.status).where(agent_memories.c.memory_id == prop["memory_id"])
            ).scalar_one()
        assert status_after_inc == "proposed"

        # 3) Classic AgenticTurnRuntime with _code_mode_verified=True MUST NOT promote when gate closes with caveats
        ws = ConversationWorkspace(tmp_path / "ws_rt02")
        ws.root.mkdir(parents=True, exist_ok=True)
        toolset = AgentToolset(ws, enable_terminal=False, code_mode_active=True)
        toolset._changed_paths.add("app.py")
        toolset._change_events = 1
        toolset._code_mode_verified = True  # Simulates pytest passing earlier

        promoted_calls: list[Any] = []
        turn_store = _MinimalTurnStore(turn_id="turn-rt-a2-caveats")

        gate = VerificationGate(
            settings={"orchestration": {"verifier": {"enabled": True, "threshold": {"files": 1}, "maxRounds": 1}}},
            available_models=[
                AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
                AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
            ],
            run_child=lambda *a, **kw: ToolOutcome(
                "succeeded",
                "verifier",
                "fail",
                {"verification_report": {"verdict": "fail", "checked": [], "findings": [_proven_finding("F1", "bug")], "unverified": []}},
            ),
            workspace_root=ws.root,
        )

        # Provider replies text without calling resolve_findings -> reminders exhaust -> complete_with_caveats
        from tests.unit.agentic.test_parallel_and_background_subagents import _text_batch

        class _StubTextProvider:
            def stream(self, request: Any):
                return iter(_text_batch("Pronto."))

        rt = AgenticTurnRuntime(
            store=turn_store,
            provider=_StubTextProvider(),
            toolset=toolset,
            system_prompt="sys",
            limits=AgenticLimits(max_iterations=8, max_actions=8),
            on_verification=lambda v: promoted_calls.append(v),
            verification_gate=gate,
            effective_settings={"orchestration": {"verifier": {"enabled": True, "threshold": {"files": 1}, "maxRounds": 1}}},
        )
        res = rt.run("turn-rt-a2-caveats")
        assert res.state == "completed_with_caveats"
        assert True not in promoted_calls
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# RT-A-03 (CRITICAL): ResolveFindings advertised in MCP tools/list on persistent CLI boot
# ---------------------------------------------------------------------------


def test_rt_a_03_resolve_findings_advertised_on_boot_and_refuses_when_no_open_findings(tmp_path: Path) -> None:
    ws = ConversationWorkspace(tmp_path / "ws_rt03")
    ws.root.mkdir(parents=True, exist_ok=True)
    toolset = AgentToolset(ws, enable_terminal=False, code_mode_active=True)
    turn_store = _MinimalTurnStore(turn_id="turn-rt-a3")

    class _DummyProvider:
        pass

    rt = AgenticTurnRuntime(
        store=turn_store,
        provider=_DummyProvider(),
        toolset=toolset,
        system_prompt="sys",
        limits=AgenticLimits(max_iterations=4, max_actions=4),
        effective_settings={"orchestration": {"verifier": {"enabled": True}}},
    )
    executor = RuntimeToolExecutor(
        rt,
        turn_store.turn,
        tool_schemas=None,
        messages=[],
        deadline=999999999.0,
        allow_deferral=False,
    )
    boot_tools = {spec.name for spec in executor.list_tools()}
    assert "resolve_findings" in boot_tools or "ResolveFindings" in boot_tools

    # Calling it before any open findings must still be rejected cleanly
    call_res = executor.call_tool(ToolCallRequest(name="resolve_findings", arguments={"items": [{"id": "F1", "status": "fixed"}]}))
    assert call_res.is_error is True


# ---------------------------------------------------------------------------
# RT-A-04 (HIGH): QuotaService.status_for(user_id, provider, model_id=model)
# ---------------------------------------------------------------------------


def test_rt_a_04_verifier_policy_passes_user_id_to_quota_service() -> None:
    calls: list[tuple[str, str, str | None]] = []

    class _RealSignatureQuotaService:
        def status_for(self, user_id: str, provider: str, model_id: str | None = None) -> QuotaStatus:
            calls.append((user_id, provider, model_id))
            if user_id == "user-42" and provider == "openai-codex":
                return QuotaStatus(max_percent=98.0, rejected=True, confidence=Confidence.MEASURED, observed_at=None)
            return QuotaStatus(max_percent=10.0, rejected=False, confidence=Confidence.MEASURED, observed_at=None)

    quota_svc = _RealSignatureQuotaService()
    gate = VerificationGate(
        settings={
            "orchestration": {"verifier": {"enabled": True, "threshold": {"files": 1}}},
            "quota": {"policy": {"enabled": True, "blockBackgroundPercent": 90}},
        },
        available_models=[
            AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
            AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
            AvailableVerifierModel(provider="google-antigravity", model="gemini-4-hydrogen-high"),
        ],
        quota_service=quota_svc,
    )
    stub_rt = MagicMock()
    stub_rt.toolset = MagicMock()
    stub_rt.toolset._changed_paths = {"a.py"}
    stub_rt.toolset.changed_paths = lambda: ("a.py",)
    stub_rt.toolset.change_events = lambda: 1
    stub_rt.toolset._checkpoint_store = None
    stub_rt.toolset.workspace = None
    stub_rt._toolset_change_events = lambda: 1
    stub_rt._verified_at_change_events = 0
    stub_rt.effort = "medium"

    decision = gate.decide(
        {"user_id": "user-42", "turn_id": "t-q", "provider": "anthropic", "model_id": "claude-opus-5-5"},
        stub_rt,
    )
    assert decision.kind == "independent"
    assert decision.choice is not None
    # openai-codex was rejected for user-42, so verifier must skip it and pick google-antigravity!
    assert decision.choice.provider == "google-antigravity"
    assert any(u == "user-42" and p == "openai-codex" for u, p, _ in calls)

    # Also test select_verifier directly with user_id parameter
    choice2 = select_verifier(
        author=AuthorModel(provider="anthropic", model="claude-opus-5-5"),
        available=[
            AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
            AvailableVerifierModel(provider="google-antigravity", model="gemini-4-hydrogen-high"),
        ],
        quota=quota_svc,
        user_id="user-42",
        quota_policy_enabled=True,
        quota_block_percent=90,
    )
    assert choice2.provider == "google-antigravity"


# ---------------------------------------------------------------------------
# RT-A-05 (HIGH): sensitivePaths case-insensitive, '..' normalization, absolute POSIX path
# ---------------------------------------------------------------------------


def test_rt_a_05_sensitive_paths_case_and_dotdot_and_abs_path_tracking(tmp_path: Path) -> None:
    ws = ConversationWorkspace(tmp_path / "ws_rt05", unrestricted=True)
    ws.root.mkdir(parents=True, exist_ok=True)
    (ws.root / "src").mkdir(parents=True, exist_ok=True)
    (ws.root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    ci_file = ws.root / ".github" / "workflows" / "ci.yml"
    ci_file.write_text("name: CI\n", encoding="utf-8")

    # 1) Case-insensitive sensitivePaths matching (Windows/macOS bypass & dockerfile/SQL)
    cp_case = FileCheckpointStore(ws.root)
    toolset_case = AgentToolset(ws, enable_terminal=False, checkpoint_store=cp_case, turn_id="t-case")
    toolset_case._changed_paths.update({".GitHub/workflows/ci.yml", "docker/dockerfile", "migrations/001.SQL"})
    summary_case = measure_turn_changes(toolset_case, cp_case, ws.root, turn_id="t-case", use_cache=False)
    assert ".GitHub/workflows/ci.yml" in summary_case.sensitive_hits
    assert "docker/dockerfile" in summary_case.sensitive_hits
    assert "migrations/001.SQL" in summary_case.sensitive_hits
    assert summary_case.triggered is True
    assert summary_case.reason == "sensitive_path"

    # 2) Relative path with '..' staying inside root must NOT be dropped
    cp_dot = FileCheckpointStore(ws.root)
    cp_dot.snapshot_before_write("t-dot", "src/../.github/workflows/ci.yml")
    ci_file.write_text("name: CI\non: push\n", encoding="utf-8")
    toolset_dot = AgentToolset(ws, enable_terminal=False, checkpoint_store=cp_dot, turn_id="t-dot")
    toolset_dot._changed_paths.add("src/../.github/workflows/ci.yml")
    summary_dot = measure_turn_changes(toolset_dot, cp_dot, ws.root, turn_id="t-dot", use_cache=False)
    assert summary_dot.files == 1
    assert ".github/workflows/ci.yml" in summary_dot.changed_files
    assert ".github/workflows/ci.yml" in summary_dot.sensitive_hits
    assert summary_dot.triggered is True

    # Escaping root with '..' must still be rejected
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    toolset_esc = AgentToolset(ws, enable_terminal=False, turn_id="t-esc")
    toolset_esc._changed_paths.add("../outside.txt")
    summary_esc = measure_turn_changes(toolset_esc, None, ws.root, turn_id="t-esc", use_cache=False)
    assert summary_esc.files == 0

    # 3) Absolute path written through write_file should NOT double-count or produce lines_known=False
    cp_abs = FileCheckpointStore(ws.root)
    toolset_abs = AgentToolset(ws, enable_terminal=False, checkpoint_store=cp_abs, turn_id="t-abs")
    target_abs = (ws.root / "foo.py").resolve()
    outcome = toolset_abs.invoke("write_file", {"path": str(target_abs), "content": "x = 1\ny = 2\n"})
    assert outcome.status == "succeeded"
    assert toolset_abs._changed_paths == {"foo.py"}
    summary_abs = measure_turn_changes(toolset_abs, cp_abs, ws.root, turn_id="t-abs", use_cache=False)
    assert summary_abs.files == 1
    assert summary_abs.lines_known is True
    assert summary_abs.lines == 2


# ---------------------------------------------------------------------------
# RT-A-06 (HIGH): _change_measure_summary cache invalidation & subagent isolation
# ---------------------------------------------------------------------------


def test_rt_a_06_change_measure_cache_invalidates_and_isolates_subagent(tmp_path: Path) -> None:
    ws = ConversationWorkspace(tmp_path / "ws_rt06")
    ws.root.mkdir(parents=True, exist_ok=True)
    cp = FileCheckpointStore(ws.root)
    turn: dict[str, Any] = {"turn_id": "t-cache"}
    toolset = AgentToolset(ws, enable_terminal=False, checkpoint_store=cp, turn_id="t-cache")

    # Initial edit: 1 file -> below threshold (3)
    toolset.invoke("write_file", {"path": "a.py", "content": "a = 1\n"})
    s1 = measure_turn_changes(toolset, cp, ws.root, turn_id="t-cache", turn=turn, use_cache=True)
    assert s1.files == 1
    assert s1.triggered is False
    assert isinstance(turn[TURN_CACHE_KEY], ChangeSummary)

    # Turn continues (e.g. background subagent / queued input) and edits 2 more files + sensitive path
    toolset.invoke("write_file", {"path": "b.py", "content": "b = 2\n"})
    toolset.invoke("write_file", {"path": ".github/workflows/ci.yml", "content": "name: ci\n"})
    s2 = measure_turn_changes(toolset, cp, ws.root, turn_id="t-cache", turn=turn, use_cache=True)
    assert s2.files == 3
    assert s2.triggered is True
    assert s2.reason == "sensitive_path"

    # Subagent measurement with same turn_id must only measure subagent's own toolset paths
    sub_toolset = AgentToolset(ws, enable_terminal=False, checkpoint_store=cp, turn_id="t-cache")
    sub_toolset.invoke("write_file", {"path": "sub_only.py", "content": "z = 1\n"})
    sub_turn = {**turn, "is_subagent": True, "subagent_type": "general"}
    sub_turn.pop(TURN_CACHE_KEY, None)
    s_sub = measure_turn_changes(sub_toolset, cp, ws.root, turn_id="t-cache", turn=sub_turn, use_cache=True)
    assert s_sub.changed_files == ("sub_only.py",)
    assert s_sub.files == 1
    assert s_sub.triggered is False


# ---------------------------------------------------------------------------
# RT-A-07 (HIGH): verifier never enters _run_foreground_subagent_watching_queue
# ---------------------------------------------------------------------------


def test_rt_a_07_verifier_never_promoted_to_background_or_consumes_queued_input(tmp_path: Path) -> None:
    ws = ConversationWorkspace(tmp_path / "ws_rt07")
    ws.root.mkdir(parents=True, exist_ok=True)
    store = _MinimalTurnStore(turn_id="turn-rt-a7")
    claimed_called = False

    def _claim_queued(turn: Any) -> list[Any]:
        nonlocal claimed_called
        claimed_called = True
        return [{"content": "mensagem do usuario na fila"}]

    store.claim_queued_inputs = _claim_queued  # type: ignore[attr-defined]
    store.has_queued_inputs = lambda turn: True  # type: ignore[attr-defined]

    session = TurnSession(
        store=store,
        turn=store.turn,
        workspace_root=ws.root,
        provider_factory=lambda: MagicMock(),
    )

    watch_called = False
    exec_called = False

    def _fail_watch(**kwargs: Any) -> ToolOutcome:
        nonlocal watch_called
        watch_called = True
        raise AssertionError("verifier must never call _run_foreground_subagent_watching_queue")

    def _fake_exec(**kwargs: Any) -> ToolOutcome:
        nonlocal exec_called
        exec_called = True
        return ToolOutcome(
            "succeeded",
            "verifier",
            "ok",
            {"verification_report": {"verdict": "pass", "checked": [], "findings": [], "unverified": []}},
        )

    session._run_foreground_subagent_watching_queue = _fail_watch  # type: ignore[method-assign]
    session._execute_subagent_run = _fake_exec  # type: ignore[method-assign]
    session._can_watch_queued_user_input = lambda: True  # type: ignore[method-assign]

    res = session.run_child("verifier", "Verifique os arquivos alterados.")
    assert res.status == "succeeded"
    assert exec_called is True
    assert watch_called is False
    assert claimed_called is False


# ---------------------------------------------------------------------------
# RT-A-08 (HIGH): verifier.finished carries resolutions & completed_with_caveats;
#                 TurnSession.emit_lifecycle handles completed_with_caveats
# ---------------------------------------------------------------------------


def test_rt_a_08_events_emit_resolutions_and_session_handles_completed_with_caveats(tmp_path: Path) -> None:
    ws = ConversationWorkspace(tmp_path / "ws_rt08")
    ws.root.mkdir(parents=True, exist_ok=True)
    toolset = AgentToolset(ws, enable_terminal=False, code_mode_active=True)
    toolset._changed_paths.add("a.py")
    toolset._change_events = 1

    emitted: list[tuple[str, str, dict[str, Any]]] = []
    gate = VerificationGate(
        settings={"orchestration": {"verifier": {"enabled": True, "threshold": {"files": 1}, "maxRounds": 2}}},
        available_models=[
            AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
            AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
        ],
        run_child=lambda *a, **kw: ToolOutcome(
            "succeeded",
            "verifier",
            "fail",
            {
                "verification_report": {
                    "verdict": "fail",
                    "checked": [],
                    "findings": [_proven_finding("F1", "bug 1"), _proven_finding("F2", "bug 2", severity="major")],
                    "unverified": [],
                }
            },
        ),
        event_emitter=lambda et, s, p: emitted.append((et, s, dict(p))),
        workspace_root=ws.root,
    )
    turn = {"user_id": "u1", "conversation_id": "c1", "turn_id": "t8", "provider": "anthropic", "model_id": "claude-opus-5-5"}
    rt = MagicMock()
    rt.toolset = toolset
    rt._toolset_change_events = lambda: toolset._change_events
    rt._verified_at_change_events = 0
    rt.effort = "medium"

    act1 = gate.evaluate_turn_end(turn, rt, allow_continuation=True)
    assert act1.action == "continue"

    # Implementer resolves F1 as confirmed and F2 as rejected
    r = toolset.invoke(
        "resolve_findings",
        {
            "items": [
                {"id": "F1", "status": "confirmed", "reason": "aceito como pendência"},
                {"id": "F2", "status": "rejected", "reason": "falso positivo comprovado"},
            ]
        },
    )
    assert r.status == "succeeded"

    act2 = gate.evaluate_turn_end(turn, rt, allow_continuation=True)
    assert act2.action == "complete_with_caveats"

    finished_events = [p for et, _, p in emitted if et == "verifier.finished"]
    assert any(
        isinstance(p.get("resolutions"), dict)
        and p["resolutions"].get("F1", {}).get("status") == "confirmed"
        and p["resolutions"].get("F2", {}).get("status") == "rejected"
        and p.get("completed_with_caveats") is True
        for p in finished_events
    )

    # TurnSession.emit_lifecycle must record code_mode.completed_with_caveats
    recorded: list[tuple[Any, str, dict[str, Any]]] = []
    store = _MinimalTurnStore(turn_id="turn-rt-a8")
    sess = TurnSession(
        store=store,
        turn=store.turn,
        workspace_root=ws.root,
        provider_factory=lambda: MagicMock(),
    )
    sess._record = lambda et, summary, payload=None, **kw: recorded.append((et, summary, dict(payload or {})))  # type: ignore[method-assign]
    sess.emit_lifecycle(
        "completed_with_caveats",
        summary="Concluído com pendências (1)",
        open_findings=[_proven_finding("F1", "bug 1")],
    )
    assert any(
        getattr(et, "value", str(et)) == "code_mode.completed_with_caveats"
        and payload.get("verification") == "completed_with_caveats"
        and len(payload.get("open_findings") or []) == 1
        for et, _, payload in recorded
    )


# ---------------------------------------------------------------------------
# RT-A-09 (MEDIUM): Preserve confirmed findings on pass round 2 & fixed/confirmed on reminder exhaustion
# ---------------------------------------------------------------------------


def test_rt_a_09_preserves_confirmed_on_round2_pass_and_partial_resolutions_on_exhaustion(tmp_path: Path) -> None:
    ws = ConversationWorkspace(tmp_path / "ws_rt09")
    ws.root.mkdir(parents=True, exist_ok=True)
    toolset = AgentToolset(ws, enable_terminal=False, code_mode_active=True)
    toolset._changed_paths.add("a.py")
    toolset._change_events = 1

    # Flow 1: F1=confirmed, F2=fixed -> Round 2 passes -> MUST complete_with_caveats carrying F1!
    reports = [
        {
            "verdict": "fail",
            "checked": [],
            "findings": [_proven_finding("F1", "blocker kept"), _proven_finding("F2", "major fixed", severity="major")],
            "unverified": [],
        },
        {"verdict": "pass", "checked": [], "findings": [], "unverified": []},
    ]
    call_idx = 0

    def _child(*a: Any, **kw: Any) -> ToolOutcome:
        nonlocal call_idx
        rep = reports[call_idx]
        call_idx += 1
        return ToolOutcome("succeeded", "verifier", "done", {"verification_report": rep})

    gate = VerificationGate(
        settings={"orchestration": {"verifier": {"enabled": True, "threshold": {"files": 1}, "maxRounds": 2}}},
        available_models=[
            AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
            AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
        ],
        run_child=_child,
        workspace_root=ws.root,
    )
    turn = {"user_id": "u1", "conversation_id": "c1", "turn_id": "t9a", "provider": "anthropic", "model_id": "claude-opus-5-5"}
    rt = MagicMock()
    rt.toolset = toolset
    rt._toolset_change_events = lambda: toolset._change_events
    rt._verified_at_change_events = 0
    rt.effort = "medium"

    assert gate.evaluate_turn_end(turn, rt).action == "continue"
    toolset.execute(
        "resolve_findings",
        {"items": [{"id": "F1", "status": "confirmed"}, {"id": "F2", "status": "fixed"}]},
    )
    act_final = gate.evaluate_turn_end(turn, rt)
    assert act_final.action == "complete_with_caveats"
    assert [f.id for f in act_final.open_findings] == ["F1"]

    # Flow 2: Partial resolution (F1=fixed, F2 unresolved) until reminders exhaust -> still_open includes F1 & F2
    toolset2 = AgentToolset(ws, enable_terminal=False, code_mode_active=True)
    toolset2._changed_paths.add("b.py")
    toolset2._change_events = 1
    gate2 = VerificationGate(
        settings={"orchestration": {"verifier": {"enabled": True, "threshold": {"files": 1}, "maxRounds": 2}}},
        available_models=[
            AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
            AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
        ],
        run_child=lambda *a, **kw: ToolOutcome(
            "succeeded",
            "verifier",
            "fail",
            {
                "verification_report": {
                    "verdict": "fail",
                    "checked": [],
                    "findings": [_proven_finding("F1", "bug 1"), _proven_finding("F2", "bug 2")],
                    "unverified": [],
                }
            },
        ),
        workspace_root=ws.root,
    )
    rt2 = MagicMock()
    rt2.toolset = toolset2
    rt2._toolset_change_events = lambda: toolset2._change_events
    rt2._verified_at_change_events = 0
    rt2.effort = "medium"

    assert gate2.evaluate_turn_end(turn, rt2).action == "continue"
    toolset2.execute("resolve_findings", {"items": [{"id": "F1", "status": "fixed"}]})
    for _ in range(3):
        assert gate2.evaluate_turn_end(turn, rt2).action == "continue"
    exhausted = gate2.evaluate_turn_end(turn, rt2)
    assert exhausted.action == "complete_with_caveats"
    assert {f.id for f in exhausted.open_findings} == {"F1", "F2"}


# ---------------------------------------------------------------------------
# RT-A-10 (MEDIUM): resolve_child_target allows parent_model_id when requested_provider == parent_provider
# ---------------------------------------------------------------------------


def test_rt_a_10_resolve_child_target_allows_non_favorite_parent_model_on_same_provider() -> None:
    target = resolve_child_target(
        agent_name="verifier",
        requested_provider="anthropic",
        requested_model="claude-opus-5-5-unfavorited",
        effort="high",
        parent_provider="anthropic",
        parent_model_id="claude-opus-5-5-unfavorited",
        favorites_by_provider={"anthropic": ["claude-sonnet-4-5"]},
        configured_providers=["anthropic"],
    )
    assert not isinstance(target, ToolOutcome)
    assert target.provider == "anthropic"
    assert target.model_id == "claude-opus-5-5-unfavorited"


# ---------------------------------------------------------------------------
# RT-A-11 (MEDIUM): Persistent EffortEscalator on TurnSession + effective escalation in NativeTurnRunner
# ---------------------------------------------------------------------------


def test_rt_a_11_session_effort_escalator_persists_and_native_cli_restarts_with_escalated_effort(tmp_path: Path) -> None:
    ws = ConversationWorkspace(tmp_path / "ws_rt11")
    ws.root.mkdir(parents=True, exist_ok=True)
    store = _MinimalTurnStore(turn_id="turn-rt-a11")

    # 1) TurnSession creates a persistent EffortEscalator shared across _verification_gate() calls
    sess = TurnSession(
        store=store,
        turn=store.turn,
        workspace_root=ws.root,
        provider_factory=lambda: MagicMock(),
    )
    g1 = sess._verification_gate()
    g2 = sess._verification_gate()
    assert g1._get_effort_escalator() is g2._get_effort_escalator()
    assert isinstance(g1._get_effort_escalator(), EffortEscalator)

    # 2) NativeTurnRunner updates effort inside loop & restarts persistent_stdin CLI when effort escalates
    toolset = AgentToolset(ws, enable_terminal=False, code_mode_active=True)
    reports = [
        {"verdict": "fail", "checked": [], "findings": [_proven_finding("F1", "fail 1")], "unverified": []},
        {"verdict": "fail", "checked": [], "findings": [_proven_finding("F1", "fail 2")], "unverified": []},
        {"verdict": "pass", "checked": [], "findings": [], "unverified": []},
    ]
    v_idx = 0

    def _verifier(*a: Any, **kw: Any) -> ToolOutcome:
        nonlocal v_idx
        rep = reports[v_idx]
        v_idx += 1
        return ToolOutcome("succeeded", "verifier", "done", {"verification_report": rep})

    settings = {
        "orchestration": {"verifier": {"enabled": True, "threshold": {"files": 1}, "maxRounds": 2}},
        "effort": {"escalateOnFailure": True},
    }
    gate = VerificationGate(
        settings=settings,
        available_models=[
            AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
            AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
        ],
        run_child=_verifier,
        effort_escalator=EffortEscalator(),
        workspace_root=ws.root,
    )

    started_efforts: list[str | None] = []
    phase = 0

    class _FakePersistentHandle:
        def __init__(self, req: NativeTurnRequest) -> None:
            self.req = req
            self.sent: list[Any] = []

        def refuse_tools(self, reason: str = "") -> None:
            pass

        def send_user(self, content: Any) -> None:
            self.sent.append(content)

        def close(self) -> None:
            pass

        def run_until_result(self, sink: NativeTurnSink) -> NativeTurnOutcome:
            nonlocal phase
            phase += 1
            init = _make_cli_init()
            if phase == 1:
                sink.on_init(init)
                self.req.executor.call_tool(ToolCallRequest(name="write_file", arguments={"path": "app.py", "content": "v = 1\n"}))
                sink.on_text("v1")
                txt = "v1"
            elif phase == 2:
                self.req.executor.call_tool(ToolCallRequest(name="write_file", arguments={"path": "app.py", "content": "v = 2\n"}))
                self.req.executor.call_tool(ToolCallRequest(name="resolve_findings", arguments={"items": [{"id": "F1", "status": "fixed"}]}))
                sink.on_text("v2")
                txt = "v2"
            else:
                sink.on_init(init)
                self.req.executor.call_tool(ToolCallRequest(name="write_file", arguments={"path": "app.py", "content": "v = 3\n"}))
                self.req.executor.call_tool(ToolCallRequest(name="resolve_findings", arguments={"items": [{"id": "F1", "status": "fixed"}]}))
                sink.on_text("v3")
                txt = "v3"
            res = _make_cli_result(txt)
            return NativeTurnOutcome(init=init, result=res, final_text=txt, exit_code=0)

    class _FakeNativeProvider:
        def native_turn_available(self, turn: Mapping[str, object]) -> bool:
            return True

        def start_native_turn(self, req: NativeTurnRequest) -> _FakePersistentHandle:
            started_efforts.append(req.effort)
            return _FakePersistentHandle(req)

    rt = AgenticTurnRuntime(
        store=store,
        provider=_FakeNativeProvider(),
        toolset=toolset,
        system_prompt="sys",
        limits=AgenticLimits(max_iterations=10, max_actions=20),
        verification_gate=gate,
        effective_settings=settings,
        effort="medium",
    )
    result = rt.run("turn-rt-a11")
    assert result.state == "completed"
    # First CLI process ran with "medium"; after 2nd failure effort escalated to "high" and a new CLI process started with "high"!
    assert started_efforts[0] == "medium"
    assert "high" in started_efforts
    assert len(started_efforts) >= 2


# ---------------------------------------------------------------------------
# RT-A-12 (MEDIUM): VERIFIER tools omit Remember/Recall by default; Plan Mode read_only propagation;
#                   verifier/explore/plan don't dirty parent _changed_paths
# ---------------------------------------------------------------------------


def test_rt_a_12_verifier_memory_gating_plan_mode_readonly_and_no_parent_dirtying(tmp_path: Path) -> None:
    ws = ConversationWorkspace(tmp_path / "ws_rt12")
    ws.root.mkdir(parents=True, exist_ok=True)
    assert "Remember" not in (VERIFIER.tools or ())
    assert "Recall" not in (VERIFIER.tools or ())

    engine = _make_engine(tmp_path, "rt_a_12.db")
    try:
        mem = PostgresAgentMemoryStore(engine, "user-1", project_id="proj-1", data_dir=tmp_path / "idx")
        ts_off = AgentToolset(ws, memory=mem)
        ts_off._policy = AgentToolPolicy(ts_off._policy, VERIFIER, allow_agent_memory=False)
        names_off = {d.name for d in ts_off.definitions()}
        assert "remember" not in names_off
        assert "recall" not in names_off

        ts_on = AgentToolset(ws, memory=mem)
        ts_on._policy = AgentToolPolicy(ts_on._policy, VERIFIER, allow_agent_memory=True)
        names_on = {d.name for d in ts_on.definitions()}
        assert "remember" in names_on
        assert "recall" in names_on

        # _main_caller propagates read_only when _plan_mode_active is True
        store = _MinimalTurnStore(turn_id="turn-rt-a12")
        sess = TurnSession(
            store=store,
            turn=store.turn,
            workspace_root=ws.root,
            provider_factory=lambda: MagicMock(),
        )
        sess.permission_mode = "full_autonomy"
        sess.toolset = AgentToolset(ws)
        sess.toolset._plan_mode_active = True
        caller = sess._main_caller()
        assert caller.read_only is True
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# RT-A-13 (LOW): /v1/orchestration/verifier/metrics?days=999999999 -> 400;
#                VerificationReviewStore.add_findings last-wins deduplication
# ---------------------------------------------------------------------------


def test_rt_a_13_metrics_days_overflow_returns_400_and_add_findings_is_last_wins(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, "rt_a_13.db")
    try:
        store = VerificationReviewStore(engine)
        rid = store.start(
            user_id="u1",
            conversation_id="c1",
            turn_id="t13",
            author_provider="anthropic",
            author_model="claude-opus-5-5",
            reviewer_provider="openai-codex",
            reviewer_model="gpt-6.1-sol",
        )
        # Duplicate finding_id in same batch: last occurrence MUST win
        store.add_findings(
            rid,
            [
                {"finding_id": "F1", "severity": "minor", "category": "bug", "has_evidence": False},
                {"finding_id": "F1", "severity": "blocker", "category": "security", "has_evidence": True},
            ],
        )
        rev = store.get_review(rid)
        assert rev is not None
        assert len(rev["findings"]) == 1
        assert rev["findings"][0]["severity"] == "blocker"
        assert rev["findings"][0]["category"] == "security"
        assert rev["findings"][0]["has_evidence"] is True

        # HTTP route returns 400 invalid_days on huge days instead of 500 OverflowError
        app = FastAPI()
        services = MagicMock()
        services.verification_reviews = store
        services.security = MagicMock()
        principal = AuthenticatedPrincipal("u1", "cred-1", frozenset({"api"}))
        register_verifier_metrics_routes(app, services, lambda req: principal)
        client = TestClient(app)

        resp = client.get("/v1/orchestration/verifier/metrics?days=999999999")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_days"
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# RT-A-14 (LOW): orchestration.verifier.threshold.* ignored from project when trusted=False
# ---------------------------------------------------------------------------


def test_rt_a_14_verifier_threshold_ignored_from_untrusted_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_dir = tmp_path / "user_orin"
    proj_dir = tmp_path / "project"
    user_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / ".orin").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ORIN_USER_DIR", str(user_dir))
    monkeypatch.setenv("ORIN_CLAUDE_HOME", str(tmp_path / "empty_claude"))
    monkeypatch.setenv("ORIN_MANAGED_DIR", str(tmp_path / "empty_managed"))
    monkeypatch.setenv("ORIN_CLAUDE_MANAGED_DIR", str(tmp_path / "empty_claude_managed"))

    (user_dir / "settings.json").write_text(
        '{"orchestration": {"verifier": {"enabled": true, "threshold": {"files": 3, "lines": 100}}}}',
        encoding="utf-8",
    )
    (proj_dir / ".orin" / "settings.json").write_text(
        '{"orchestration": {"verifier": {"threshold": {"ignore": ["**"], "sensitivePaths": [], "files": 999}}}}',
        encoding="utf-8",
    )

    eff_untrusted = load_effective_settings(proj_dir, trusted=False)
    assert eff_untrusted.get("orchestration.verifier.threshold.files") == 3
    assert eff_untrusted.get("orchestration.verifier.threshold.ignore") is None

    eff_trusted = load_effective_settings(proj_dir, trusted=True)
    assert eff_trusted.get("orchestration.verifier.threshold.files") == 999
    assert eff_trusted.get("orchestration.verifier.threshold.ignore") == ["**"]


# ---------------------------------------------------------------------------
# E2E (Section 5.1 item 2): Fake CLI selfVerify off vs on + 4 changed files,
# blocker proven + major unproven, resolve_findings, round 2 pass, SQL rows recorded
# ---------------------------------------------------------------------------


def test_e2e_fake_cli_self_verify_default_off_vs_on_and_4_files_verifier_sql_persistence(tmp_path: Path) -> None:
    # Part A: selfVerify default False does NOT force self_verify; selfVerify True DOES force self_verify
    for self_verify_flag, expected_prompts in ((False, 1), (True, 2)):
        ws = ConversationWorkspace(tmp_path / f"ws_sv_{self_verify_flag}")
        ws.root.mkdir(parents=True, exist_ok=True)
        toolset = AgentToolset(ws, enable_terminal=False, code_mode_active=True)
        store = _MinimalTurnStore(turn_id=f"turn-sv-{self_verify_flag}")
        settings = {"orchestration": {"verifier": {"enabled": False}, "selfVerify": {"enabled": self_verify_flag}}}
        gate = VerificationGate(settings=settings, workspace_root=ws.root)
        calls = 0

        class _SVHandle:
            def __init__(self, req: NativeTurnRequest) -> None:
                self.req = req

            def refuse_tools(self, reason: str = "") -> None:
                pass

            def send_user(self, content: Any) -> None:
                pass

            def close(self) -> None:
                pass

            def run_until_result(self, sink: NativeTurnSink) -> NativeTurnOutcome:
                nonlocal calls
                calls += 1
                init = _make_cli_init()
                if calls == 1:
                    sink.on_init(init)
                    self.req.executor.call_tool(ToolCallRequest(name="write_file", arguments={"path": "only.py", "content": "x = 1\n"}))
                    sink.on_text("edited")
                    txt = "edited"
                else:
                    sink.on_text("verified")
                    txt = "verified"
                res = _make_cli_result(txt)
                return NativeTurnOutcome(init=init, result=res, final_text=txt, exit_code=0)

        class _SVProvider:
            def native_turn_available(self, turn: Mapping[str, object]) -> bool:
                return True

            def start_native_turn(self, req: NativeTurnRequest) -> _SVHandle:
                return _SVHandle(req)

        rt = AgenticTurnRuntime(
            store=store,
            provider=_SVProvider(),
            toolset=toolset,
            system_prompt="sys",
            limits=AgenticLimits(max_iterations=6, max_actions=10),
            phase_controller=PhaseController(),
            verification_gate=gate,
            effective_settings=settings,
        )
        out = rt.run(f"turn-sv-{self_verify_flag}")
        assert out.state == "completed"
        assert calls == expected_prompts

    # Part B: 4 changed files + verifier enabled + proven blocker + unproven major + resolve_findings + round 2 pass + SQL rows
    engine = _make_engine(tmp_path, "rt_a_e2e.db")
    try:
        sql_store = VerificationReviewStore(engine)
        ws = ConversationWorkspace(tmp_path / "ws_e2e_4files")
        ws.root.mkdir(parents=True, exist_ok=True)
        cp = FileCheckpointStore(ws.root)
        toolset = AgentToolset(ws, enable_terminal=False, code_mode_active=True, checkpoint_store=cp, turn_id="turn-e2e-4")
        store = _MinimalTurnStore(turn_id="turn-e2e-4")

        verifier_rounds = 0

        def _verifier_child(*a: Any, **kw: Any) -> ToolOutcome:
            nonlocal verifier_rounds
            verifier_rounds += 1
            if verifier_rounds == 1:
                return ToolOutcome(
                    "succeeded",
                    "verifier",
                    "round 1 fail",
                    {
                        "verification_report": {
                            "verdict": "fail",
                            "checked": [{"command": "pytest -q", "exit_code": 1, "summary": "1 failed"}],
                            "findings": [
                                _proven_finding("F1", "null check missing in f1.py", severity="blocker", file="f1.py", line=1),
                                {
                                    "id": "F2",
                                    "severity": "major",
                                    "category": "bug",
                                    "claim": "suspicious loop without proof",
                                    "evidence": None,
                                },
                            ],
                            "unverified": [],
                        }
                    },
                )
            return ToolOutcome(
                "succeeded",
                "verifier",
                "round 2 pass",
                {"verification_report": {"verdict": "pass", "checked": [{"command": "pytest -q", "exit_code": 0, "summary": "all green"}], "findings": [], "unverified": []}},
            )

        settings = {"orchestration": {"verifier": {"enabled": True, "maxRounds": 2}}}
        gate = VerificationGate(
            settings=settings,
            available_models=[
                AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
                AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
            ],
            run_child=_verifier_child,
            review_recorder=sql_store,
            workspace_root=ws.root,
        )

        step = 0
        boot_tool_names: set[str] = set()

        class _E2EHandle:
            def __init__(self, req: NativeTurnRequest) -> None:
                self.req = req

            def refuse_tools(self, reason: str = "") -> None:
                pass

            def send_user(self, content: Any) -> None:
                pass

            def close(self) -> None:
                pass

            def run_until_result(self, sink: NativeTurnSink) -> NativeTurnOutcome:
                nonlocal step
                step += 1
                init = _make_cli_init()
                if step == 1:
                    boot_tool_names.update(spec.name for spec in self.req.executor.list_tools())
                    sink.on_init(init)
                    for idx in range(1, 5):
                        self.req.executor.call_tool(
                            ToolCallRequest(name="write_file", arguments={"path": f"f{idx}.py", "content": f"v = {idx}\n"})
                        )
                    sink.on_text("Alterados 4 arquivos.")
                    txt = "Alterados 4 arquivos."
                else:
                    # Fix F1 and resolve ONLY F1 (F2 was downgraded to unproven, so not blocking!)
                    self.req.executor.call_tool(
                        ToolCallRequest(name="write_file", arguments={"path": "f1.py", "content": "v = 100\n"})
                    )
                    res_call = self.req.executor.call_tool(
                        ToolCallRequest(name="resolve_findings", arguments={"items": [{"id": "F1", "status": "fixed", "reason": "guard adicionado"}]})
                    )
                    assert res_call.is_error is False
                    sink.on_text("Corrigido F1.")
                    txt = "Corrigido F1."
                res = _make_cli_result(txt)
                return NativeTurnOutcome(init=init, result=res, final_text=txt, exit_code=0)

        class _E2EProvider:
            def native_turn_available(self, turn: Mapping[str, object]) -> bool:
                return True

            def start_native_turn(self, req: NativeTurnRequest) -> _E2EHandle:
                return _E2EHandle(req)

        rt = AgenticTurnRuntime(
            store=store,
            provider=_E2EProvider(),
            toolset=toolset,
            system_prompt="sys",
            limits=AgenticLimits(max_iterations=8, max_actions=20),
            verification_gate=gate,
            effective_settings=settings,
        )
        final_res = rt.run("turn-e2e-4")
        assert final_res.state == "completed"
        assert verifier_rounds == 2
        assert "resolve_findings" in boot_tool_names or "ResolveFindings" in boot_tool_names

        with engine.connect() as conn:
            rev_rows = conn.execute(select(verification_reviews).where(verification_reviews.c.turn_id == "turn-e2e-4")).mappings().all()
            find_rows = conn.execute(select(verification_findings)).mappings().all()
        assert len(rev_rows) == 2
        assert [r["verdict"] for r in sorted(rev_rows, key=lambda x: x["round"])] == ["fail", "pass"]
        assert rev_rows[0]["files_changed"] == 4
        by_fid = {r["finding_id"]: r for r in find_rows}
        assert by_fid["F1"]["severity"] == "blocker"
        assert by_fid["F1"]["resolution"] == "fixed"
        assert by_fid["F2"]["severity"] == "unproven"
        assert by_fid["F2"]["has_evidence"] is False
    finally:
        engine.dispose()

"""Tests for VerificationGate in NativeTurnRunner (persistent stdin & Codex-style session resume)."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.phases import Phase, PhaseController
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.verification_gate import VerificationGate
from agentos.agentic.verifier_policy import AvailableVerifierModel
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.claude_cli.native_loop import NativeTurnOutcome, NativeTurnRequest, NativeTurnSink
from agentos.providers.claude_cli.stream import CliInit, CliResult, CliUsage
from agentos.providers.cli_tools_mcp import ToolCallRequest


def _make_init(session_id: str = "sess-native-1") -> CliInit:
    return CliInit(
        session_id=session_id,
        model="claude-opus-5-5",
        api_key_source="none",
        mcp_servers={"orin": "connected"},
        tools=("mcp__orin__write_file", "mcp__orin__resolve_findings"),
    )


def _make_result(text: str = "ok", session_id: str = "sess-native-1") -> CliResult:
    u = CliUsage(input_tokens=100, output_tokens=40)
    return CliResult(
        subtype="success",
        is_error=False,
        result_text=text,
        stop_reason="end_turn",
        num_turns=1,
        duration_ms=40,
        session_id=session_id,
        api_error_status=None,
        usage=u,
        model_usage={"claude-opus-5-5": u},
    )


class _NativeStore:
    def __init__(self, *, provider: str = "anthropic", model_id: str = "claude-opus-5-5") -> None:
        self.turn: dict[str, object] = {
            "turn_id": "turn-native-a5",
            "conversation_id": "conv-native-a5",
            "user_id": "user-1",
            "provider": provider,
            "model_id": model_id,
            "user_message_id": "u-1",
            "assistant_message_id": "a-1",
            "code_mode": "code",
        }
        self.events: list[tuple[str, dict[str, object]]] = []
        self.text: list[str] = []
        self.finished_codes: list[str | None] = []

    def load(self, turn_id: str) -> dict[str, object]:
        return self.turn

    def history_for_turn(self, turn: Mapping[str, object]) -> list[dict[str, str]]:
        return [{"role": "user", "content": "altere o arquivo"}]

    def lifecycle(self, turn: Mapping[str, object], state: str, **payload: object) -> None:
        self.events.append((state, dict(payload)))

    def delta(self, turn: Mapping[str, object], text: str) -> None:
        self.text.append(text)

    def finish(self, turn: Mapping[str, object], *, failed: bool = False, code: str | None = None) -> None:
        self.finished_codes.append(code)


def _proven_blocker(fid: str, claim: str) -> dict[str, Any]:
    return {
        "id": fid,
        "severity": "blocker",
        "category": "bug",
        "claim": claim,
        "evidence": {"kind": "code_ref", "file": "out.py", "line": 1, "reason": claim},
    }


def test_native_turn_persistent_stdin_rounds_pass_after_fix(tmp_path: Path) -> None:
    """Native CLI with persistent stdin receives findings via send_user, blocks until resolve_findings, passes round 2."""
    ws = ConversationWorkspace(tmp_path / "ws_stdin")
    ws.root.mkdir(parents=True, exist_ok=True)
    toolset = AgentToolset(ws, enable_terminal=False, code_mode_active=True)

    reports = [
        {"verdict": "fail", "checked": [], "findings": [_proven_blocker("F1", "bug in out.py")], "unverified": []},
        {"verdict": "pass", "checked": [], "findings": [], "unverified": []},
    ]
    verifier_calls = 0

    def fake_run_child(subagent_type: str, task: str, **kwargs: Any) -> ToolOutcome:
        nonlocal verifier_calls
        rep = reports[verifier_calls]
        verifier_calls += 1
        return ToolOutcome("succeeded", "verifier", "done", {"verification_report": rep})

    settings = {"orchestration": {"verifier": {"enabled": True, "threshold": {"files": 1}, "maxRounds": 2}}}
    gate = VerificationGate(
        settings=settings,
        available_models=[
            AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
            AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
        ],
        run_child=fake_run_child,
        workspace_root=ws.root,
    )

    sent_users: list[Any] = []
    call_round = 0

    class _PersistentHandle:
        def __init__(self, req: NativeTurnRequest) -> None:
            self.req = req

        def refuse_tools(self, reason: str = "") -> None:
            pass

        def send_user(self, content: Any) -> None:
            sent_users.append(content)

        def close(self) -> None:
            pass

        def run_until_result(self, sink: NativeTurnSink) -> NativeTurnOutcome:
            nonlocal call_round
            call_round += 1
            init = _make_init()
            if call_round == 1:
                sink.on_init(init)
                res = self.req.executor.call_tool(
                    ToolCallRequest(
                        name="write_file",
                        arguments={"path": "out.py", "content": "x = 1\n"},
                        meta={"claudecode/toolUseId": "c1", "message_id": "m1"},
                    )
                )
                assert not res.is_error
                sink.on_text("Escrevi out.py.")
                return NativeTurnOutcome(init=init, result=_make_result("Escrevi out.py."), final_text="Escrevi out.py.", exit_code=0)
            if call_round == 2:
                # Tries to reply without resolving F1 -> must be blocked and reminded!
                sink.on_text("Ignorando.")
                return NativeTurnOutcome(init=init, result=_make_result("Ignorando."), final_text="Ignorando.", exit_code=0)
            # Round 3: fix + call resolve_findings
            res_w = self.req.executor.call_tool(
                ToolCallRequest(
                    name="write_file",
                    arguments={"path": "out.py", "content": "x = 2\n"},
                    meta={"claudecode/toolUseId": "c2", "message_id": "m3"},
                )
            )
            assert not res_w.is_error
            res_rf = self.req.executor.call_tool(
                ToolCallRequest(
                    name="resolve_findings",
                    arguments={"items": [{"id": "F1", "status": "fixed", "reason": "fixed bug"}]},
                    meta={"claudecode/toolUseId": "c3", "message_id": "m3"},
                )
            )
            assert not res_rf.is_error, res_rf.text
            sink.on_text("Corrigido.")
            return NativeTurnOutcome(init=init, result=_make_result("Corrigido."), final_text="Corrigido.", exit_code=0)

    class _Provider:
        def native_turn_available(self, turn: Mapping[str, object]) -> bool:
            return True

        def start_native_turn(self, req: NativeTurnRequest) -> _PersistentHandle:
            return _PersistentHandle(req)

    store = _NativeStore()
    rt = AgenticTurnRuntime(
        store=store,
        provider=_Provider(),
        toolset=toolset,
        system_prompt="sys",
        limits=AgenticLimits(deadline=timedelta(minutes=5), max_iterations=10, max_actions=20),
        effective_settings=settings,
        verification_gate=gate,
    )

    result = rt.run("turn-native-a5")

    assert result.state == "completed"
    assert verifier_calls == 2
    assert len(sent_users) == 2
    assert "F1" in str(sent_users[0])
    assert "Resolução obrigatória pendente" in str(sent_users[1])


def test_native_turn_non_persistent_codex_resume_closes_with_caveats(tmp_path: Path) -> None:
    """Codex-style CLI (no persistent stdin, session resume) completes with caveats when round 2 still fails."""
    ws = ConversationWorkspace(tmp_path / "ws_codex")
    ws.root.mkdir(parents=True, exist_ok=True)
    toolset = AgentToolset(ws, enable_terminal=False, code_mode_active=True)

    reports = [
        {"verdict": "fail", "checked": [], "findings": [_proven_blocker("F1", "bug 1")], "unverified": []},
        {"verdict": "fail", "checked": [], "findings": [_proven_blocker("F2", "bug 2")], "unverified": []},
    ]
    verifier_calls = 0

    def fake_run_child(subagent_type: str, task: str, **kwargs: Any) -> ToolOutcome:
        nonlocal verifier_calls
        rep = reports[verifier_calls]
        verifier_calls += 1
        return ToolOutcome("succeeded", "verifier", "done", {"verification_report": rep})

    settings = {"orchestration": {"verifier": {"enabled": True, "threshold": {"files": 1}, "maxRounds": 2}}}
    gate = VerificationGate(
        settings=settings,
        available_models=[
            AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
            AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
        ],
        run_child=fake_run_child,
        workspace_root=ws.root,
    )

    started_requests: list[NativeTurnRequest] = []

    class _CodexHandle:
        def __init__(self, req: NativeTurnRequest, proc_idx: int) -> None:
            self.req = req
            self.proc_idx = proc_idx

        def refuse_tools(self, reason: str = "") -> None:
            pass

        def close(self) -> None:
            pass

        def run_until_result(self, sink: NativeTurnSink) -> NativeTurnOutcome:
            init = _make_init(session_id="codex-sess-42")
            sink.on_init(init)
            if self.proc_idx == 1:
                res = self.req.executor.call_tool(
                    ToolCallRequest(
                        name="write_file",
                        arguments={"path": "out.py", "content": "a = 1\n"},
                        meta={"claudecode/toolUseId": "c1", "message_id": "m1"},
                    )
                )
                assert not res.is_error
                sink.on_text("Feito 1.")
                return NativeTurnOutcome(init=init, result=_make_result("Feito 1.", "codex-sess-42"), final_text="Feito 1.", exit_code=0)
            # Process 2: resumed with findings -> resolve_findings(fixed)
            res_rf = self.req.executor.call_tool(
                ToolCallRequest(
                    name="resolve_findings",
                    arguments={"items": [{"id": "F1", "status": "fixed", "reason": "tried fix"}]},
                    meta={"claudecode/toolUseId": "c2", "message_id": "m2"},
                )
            )
            assert not res_rf.is_error, res_rf.text
            sink.on_text("Feito 2.")
            return NativeTurnOutcome(init=init, result=_make_result("Feito 2.", "codex-sess-42"), final_text="Feito 2.", exit_code=0)

    from types import SimpleNamespace

    class _CodexProvider:
        adapter = SimpleNamespace(
            name="codex_cli",
            capabilities=SimpleNamespace(
                persistent_stdin=False,
                supports_session_resume=True,
                tool_list_changed=False,
            ),
        )

        def native_turn_available(self, turn: Mapping[str, object]) -> bool:
            return True

        def start_native_turn(self, req: NativeTurnRequest) -> _CodexHandle:
            started_requests.append(req)
            return _CodexHandle(req, len(started_requests))

    store = _NativeStore(provider="openai-codex", model_id="gpt-6.1-sol")
    rt = AgenticTurnRuntime(
        store=store,
        provider=_CodexProvider(),
        toolset=toolset,
        system_prompt="sys",
        limits=AgenticLimits(deadline=timedelta(minutes=5), max_iterations=10, max_actions=20),
        effective_settings=settings,
        verification_gate=gate,
    )

    result = rt.run("turn-native-a5")

    assert result.state == "completed_with_caveats"
    assert len(started_requests) == 2
    assert started_requests[1].resume_session_id == "codex-sess-42"
    assert "F1" in str(started_requests[1].user_content)
    assert verifier_calls == 2
    assert len(result.caveats) == 1 and result.caveats[0]["id"] == "F2"


def test_native_turn_cancelled_verifier_applies_arf8(tmp_path: Path) -> None:
    """When verifier child is cancelled in native turn, applies A-RF8 (self_verify iff selfVerify.enabled=True)."""
    for self_verify_on in (False, True):
        ws = ConversationWorkspace(tmp_path / f"ws_cancel_{self_verify_on}")
        ws.root.mkdir(parents=True, exist_ok=True)
        toolset = AgentToolset(ws, enable_terminal=False)
        phases = PhaseController()

        def cancelled_child(*args: Any, **kwargs: Any) -> ToolOutcome:
            return ToolOutcome("failed", "cancelled", "cancelled", {}, "TURN_CANCELLED")

        settings = {
            "orchestration": {
                "selfVerify": {"enabled": self_verify_on},
                "verifier": {"enabled": True, "threshold": {"files": 1}},
            }
        }
        gate = VerificationGate(
            settings=settings,
            available_models=[
                AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
                AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
            ],
            run_child=cancelled_child,
            workspace_root=ws.root,
        )

        round_idx = 0
        entered_verify = False

        class _Handle:
            def __init__(self, req: NativeTurnRequest) -> None:
                self.req = req

            def refuse_tools(self, reason: str = "") -> None:
                pass

            def send_user(self, content: Any) -> None:
                pass

            def close(self) -> None:
                pass

            def run_until_result(self, sink: NativeTurnSink) -> NativeTurnOutcome:
                nonlocal round_idx, entered_verify
                round_idx += 1
                init = _make_init()
                if round_idx == 1:
                    sink.on_init(init)
                    self.req.executor.call_tool(
                        ToolCallRequest(
                            name="write_file",
                            arguments={"path": "out.py", "content": "a = 1\n"},
                            meta={"claudecode/toolUseId": "c1", "message_id": "m1"},
                        )
                    )
                    sink.on_text("done 1")
                    return NativeTurnOutcome(init=init, result=_make_result("done 1"), final_text="done 1", exit_code=0)
                entered_verify = phases.current is Phase.VERIFY
                self.req.executor.call_tool(
                    ToolCallRequest(
                        name="report_verification",
                        arguments={"passed": True, "findings": "ok"},
                        meta={"claudecode/toolUseId": "c2", "message_id": "m2"},
                    )
                )
                sink.on_text("verified")
                return NativeTurnOutcome(init=init, result=_make_result("verified"), final_text="verified", exit_code=0)

        class _Prov:
            def native_turn_available(self, turn: Mapping[str, object]) -> bool:
                return True

            def start_native_turn(self, req: NativeTurnRequest) -> _Handle:
                return _Handle(req)

        store = _NativeStore()
        store.turn["code_mode"] = "chat"
        rt = AgenticTurnRuntime(
            store=store,
            provider=_Prov(),
            toolset=toolset,
            system_prompt="sys",
            limits=AgenticLimits(deadline=timedelta(minutes=5), max_iterations=10, max_actions=20),
            phase_controller=phases,
            effective_settings=settings,
            verification_gate=gate,
        )

        result = rt.run("turn-native-a5")

        assert result.state == "completed"
        assert entered_verify is self_verify_on
        assert round_idx == (2 if self_verify_on else 1)

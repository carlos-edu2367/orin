"""Tests for independent verification rounds, mandatory resolve_findings, and completed_with_caveats (Criterion 5 / A-RF7)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.phases import PhaseController
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.verification_gate import VerificationGate
from agentos.agentic.verifier_policy import AvailableVerifierModel
from agentos.agentic.workspace import ConversationWorkspace


def _turn() -> dict[str, object]:
    return {
        "turn_id": "turn-rounds-1",
        "conversation_id": "conv-rounds-1",
        "user_id": "user-1",
        "provider": "anthropic",
        "model_id": "claude-opus-5-5",
        "user_message_id": "u-1",
        "assistant_message_id": "a-1",
    }


class _Store:
    def __init__(self) -> None:
        self.turn = _turn()
        self.events: list[tuple[str, dict[str, object]]] = []
        self.text: list[str] = []
        self.finished_codes: list[str | None] = []

    def load(self, turn_id: str) -> dict[str, object]:
        return self.turn

    def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, str]]:
        return [{"role": "user", "content": "implemente a feature"}]

    def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
        self.events.append((state, dict(payload)))

    def delta(self, turn: dict[str, object], text: str) -> None:
        self.text.append(text)

    def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
        self.finished_codes.append(code)


def _tool_call(call_id: str, name: str, payload: dict[str, Any]) -> str:
    escaped = json.dumps(json.dumps(payload))[1:-1]
    return (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"%s","function":'
        '{"name":"%s","arguments":"%s"}}]},"finish_reason":"tool_calls"}]}'
        % (call_id, name, escaped)
    )


_ANSWER = 'data: {"choices":[{"delta":{"content":"resposta final"},"finish_reason":"stop"}]}'


class _ScriptedProvider:
    def __init__(self, script: list[str]) -> None:
        self.script = script
        self.requests: list[dict[str, object]] = []

    def stream(self, request: dict[str, object]):
        self.requests.append(request)
        line = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        return normalize_sse([line, "data: [DONE]"], provider="openrouter")


class _RecordingReviewRecorder:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.findings: list[tuple[str, tuple[Any, ...]]] = []
        self.resolutions: list[tuple[str, str, str, str]] = []
        self.finished: list[dict[str, Any]] = []
        self._seq = 0

    def start(self, **kwargs: Any) -> str:
        self._seq += 1
        rid = f"rev-{self._seq}"
        self.started.append({"review_id": rid, **kwargs})
        return rid

    def add_findings(self, review_id: str, findings: Any) -> None:
        self.findings.append((review_id, tuple(findings)))

    def set_resolution(self, review_id: str, finding_id: str, status: str, reason: str) -> None:
        self.resolutions.append((review_id, finding_id, status, reason))

    def finish(self, review_id: str, **kwargs: Any) -> None:
        self.finished.append({"review_id": review_id, **kwargs})


def _make_toolset(tmp_path: Path) -> AgentToolset:
    ws = ConversationWorkspace(tmp_path / "ws")
    ws.root.mkdir(parents=True, exist_ok=True)
    return AgentToolset(ws, enable_terminal=False)


def _proven_blocker(fid: str, claim: str) -> dict[str, Any]:
    return {
        "id": fid,
        "severity": "blocker",
        "category": "bug",
        "claim": claim,
        "evidence": {"kind": "code_ref", "file": "a.py", "line": 1, "reason": claim},
    }


def test_criterion_5_blocks_without_resolve_findings_and_closes_with_caveats_after_2_rounds(tmp_path: Path) -> None:
    """Criterion 5: cannot answer with proven blocker without resolve_findings; fixed triggers round 2; closes completed_with_caveats."""
    recorder = _RecordingReviewRecorder()
    verifier_reports = [
        {
            "verdict": "fail",
            "checked": [{"command": "pytest", "exit_code": 1, "summary": "failed"}],
            "findings": [_proven_blocker("F1", "null deref in a.py")],
            "unverified": [],
        },
        {
            "verdict": "fail",
            "checked": [{"command": "pytest", "exit_code": 1, "summary": "still failing"}],
            "findings": [_proven_blocker("F2", "edge case still fails in a.py")],
            "unverified": [],
        },
    ]
    verifier_calls: list[int] = []

    def fake_run_child(subagent_type: str, task: str, **kwargs: Any) -> ToolOutcome:
        idx = len(verifier_calls)
        verifier_calls.append(idx + 1)
        return ToolOutcome("succeeded", "verifier", "report", {"verification_report": verifier_reports[idx]})

    settings = {
        "orchestration": {
            "verifier": {"enabled": True, "threshold": {"files": 1}, "maxRounds": 2},
        }
    }
    toolset = _make_toolset(tmp_path)
    gate = VerificationGate(
        settings=settings,
        available_models=[
            AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
            AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
        ],
        review_recorder=recorder,
        run_child=fake_run_child,
        workspace_root=toolset.workspace.root,
    )
    script = [
        _tool_call("w1", "write_file", {"path": "a.py", "content": "x = 1\n"}),
        _ANSWER,  # triggers round 1 -> returns F1
        _ANSWER,  # tries to answer WITHOUT calling resolve_findings -> BLOCKED with reminder!
        _tool_call("w2", "write_file", {"path": "a.py", "content": "x = 2\n"}),
        _tool_call("rf1", "resolve_findings", {"items": [{"id": "F1", "status": "fixed", "reason": "patched"}]}),
        _ANSWER,  # triggers round 2 -> returns F2 -> maxRounds (2) exhausted -> completed_with_caveats!
    ]
    provider = _ScriptedProvider(script)
    store = _Store()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        system_prompt="prompt",
        limits=AgenticLimits(max_iterations=None, max_actions=None),
        phase_controller=PhaseController(),
        effective_settings=settings,
        verification_gate=gate,
    )

    result = runtime.run("turn-rounds-1")

    assert result.state == "completed_with_caveats"
    assert result.verification == "completed_with_caveats"
    assert len(result.caveats) == 1
    assert result.caveats[0]["id"] == "F2"
    assert len(verifier_calls) == 2
    assert len(recorder.started) == 2
    assert recorder.started[0]["round"] == 1
    assert recorder.started[1]["round"] == 2
    assert ("rev-1", "F1", "fixed", "patched") in recorder.resolutions
    # Implementer was blocked when attempting to reply before resolve_findings
    assert any("Resolução obrigatória pendente" in str(r["messages"]) for r in provider.requests)
    assert any(state == "completed_with_caveats" for state, _ in store.events)
    assert "COMPLETED_WITH_CAVEATS" in store.finished_codes


def test_unproven_blocker_does_not_block_or_require_resolve_findings(tmp_path: Path) -> None:
    """A blocker finding without valid evidence downgrades to unproven and does not block completion."""
    def fake_run_child(subagent_type: str, task: str, **kwargs: Any) -> ToolOutcome:
        return ToolOutcome(
            "succeeded",
            "verifier",
            "report",
            {
                "verification_report": {
                    "verdict": "fail",
                    "checked": [],
                    "findings": [
                        {
                            "id": "F1",
                            "severity": "blocker",
                            "category": "bug",
                            "claim": "maybe broken (no evidence)",
                        }
                    ],
                    "unverified": [],
                }
            },
        )

    settings = {"orchestration": {"verifier": {"enabled": True, "threshold": {"files": 1}}}}
    toolset = _make_toolset(tmp_path)
    gate = VerificationGate(
        settings=settings,
        available_models=[
            AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
            AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
        ],
        run_child=fake_run_child,
        workspace_root=toolset.workspace.root,
    )
    provider = _ScriptedProvider([
        _tool_call("w1", "write_file", {"path": "a.py", "content": "x = 1\n"}),
        _ANSWER,
    ])
    store = _Store()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        system_prompt="prompt",
        limits=AgenticLimits(max_iterations=None, max_actions=None),
        phase_controller=PhaseController(),
        effective_settings=settings,
        verification_gate=gate,
    )

    result = runtime.run("turn-rounds-1")

    assert result.state == "completed"
    assert len(provider.requests) == 2
    assert toolset.open_verification_findings() == ()


def test_rejected_finding_with_reason_completes_without_second_round(tmp_path: Path) -> None:
    """When all blocking findings are rejected with a reason, turn completes without a 2nd verification round."""
    verifier_calls = 0

    def fake_run_child(subagent_type: str, task: str, **kwargs: Any) -> ToolOutcome:
        nonlocal verifier_calls
        verifier_calls += 1
        return ToolOutcome(
            "succeeded",
            "verifier",
            "report",
            {
                "verification_report": {
                    "verdict": "fail",
                    "checked": [],
                    "findings": [_proven_blocker("F1", "false positive")],
                    "unverified": [],
                }
            },
        )

    settings = {"orchestration": {"verifier": {"enabled": True, "threshold": {"files": 1}}}}
    toolset = _make_toolset(tmp_path)
    gate = VerificationGate(
        settings=settings,
        available_models=[
            AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
            AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
        ],
        run_child=fake_run_child,
        workspace_root=toolset.workspace.root,
    )
    provider = _ScriptedProvider([
        _tool_call("w1", "write_file", {"path": "a.py", "content": "x = 1\n"}),
        _ANSWER,
        _tool_call("rf1", "resolve_findings", {"items": [{"id": "F1", "status": "rejected", "reason": "intended API"}]}),
        _ANSWER,
    ])
    runtime = AgenticTurnRuntime(
        store=_Store(),
        provider=provider,
        toolset=toolset,
        system_prompt="prompt",
        limits=AgenticLimits(max_iterations=None, max_actions=None),
        phase_controller=PhaseController(),
        effective_settings=settings,
        verification_gate=gate,
    )

    result = runtime.run("turn-rounds-1")

    assert result.state == "completed"
    assert verifier_calls == 1

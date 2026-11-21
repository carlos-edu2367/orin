"""Tests for VerificationGate: Criterion 3, K17, A-RF12, E-RF21 escalation, D-RF10 promotion, ReviewRecorder."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.change_measure import ChangeSummary
from agentos.agentic.effort_resolver import EffortEscalator
from agentos.agentic.model_tiers import family_of, tier_of
from agentos.agentic.verification_gate import VerificationGate
from agentos.agentic.verifier_policy import AvailableVerifierModel, VerifierChoice
from agentos.memory.promotion import MemoryPromotionGate, PromotionOutcome, ProposedMemoryRecord
from agentos.quota.models import Confidence, QuotaStatus


class _StubToolset:
    def __init__(self, root: Path, changed_paths: list[str]) -> None:
        self.workspace = type("WS", (), {"root": root})()
        self._changed_paths = set(changed_paths)
        self._change_events = len(changed_paths)

    def change_events(self) -> int:
        return self._change_events


class _StubRuntime:
    def __init__(self, toolset: _StubToolset, *, effort: str | None = "medium") -> None:
        self.toolset = toolset
        self.effort = effort
        self.phases = None
        self._verified_at_change_events = 0
        self.lifecycle_events: list[tuple[str, dict[str, Any]]] = []

    def _toolset_change_events(self) -> int:
        return self.toolset.change_events()

    def _needs_verification(self) -> bool:
        return False

    def _life(self, turn: dict[str, Any], state: str, **payload: Any) -> None:
        self.lifecycle_events.append((state, dict(payload)))


class _FakeReviewRecorder:
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


def _turn(provider: str = "anthropic", model_id: str = "claude-opus-5-5") -> dict[str, Any]:
    return {
        "turn_id": "turn-a5-1",
        "conversation_id": "conv-a5-1",
        "user_id": "user-1",
        "provider": provider,
        "model_id": model_id,
    }


def _write_files(root: Path, files: dict[str, int]) -> list[str]:
    paths: list[str] = []
    for rel, num_lines in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(f"line_{i} = {i}" for i in range(num_lines)) + "\n", encoding="utf-8")
        paths.append(rel)
    return paths


def _checkpoint_for_new_files(root: Path, rels: list[str]) -> Any:
    class _StubCheckpoint:
        workspace_root = root

        def get_checkpoint(self, turn_id: str) -> dict[str, Any]:
            return {"files_by_path": {rel: {"existed": False} for rel in rels}}

    return _StubCheckpoint()


def test_criterion_3_thresholds_and_cross_family_selection(tmp_path: Path) -> None:
    """Criterion 3: author claude-opus-5-5; 2 files/40 lines skip; 3 files trigger; alembic/ trigger; cross_family tier>=4."""
    settings = {"orchestration": {"verifier": {"enabled": True, "modelPolicy": "cross_family", "effort": "high"}}}
    available = [
        AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
        AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
        AvailableVerifierModel(provider="google-antigravity", model="gemini-4-hydrogen-high"),
    ]

    # 2 files, 40 lines total -> below threshold (3 files / 150 lines) -> skip
    rels_small = _write_files(tmp_path / "small", {"src/a.py": 20, "src/b.py": 20})
    gate_small = VerificationGate(
        settings=settings,
        available_models=available,
        checkpoint_store=_checkpoint_for_new_files(tmp_path / "small", rels_small),
        workspace_root=tmp_path / "small",
    )
    dec_small = gate_small.decide(_turn(), _StubRuntime(_StubToolset(tmp_path / "small", rels_small)))
    assert dec_small.kind == "skip"
    assert dec_small.summary is not None and dec_small.summary.files == 2 and dec_small.summary.lines == 40

    # 3 files -> triggers with cross_family tier >= 4
    events: list[tuple[str, str, dict[str, Any]]] = []
    rels_3 = _write_files(tmp_path / "three", {"src/a.py": 5, "src/b.py": 5, "src/c.py": 5})
    gate_3 = VerificationGate(
        settings=settings,
        available_models=available,
        checkpoint_store=_checkpoint_for_new_files(tmp_path / "three", rels_3),
        workspace_root=tmp_path / "three",
        event_emitter=lambda et, s, p: events.append((et, s, dict(p))),
    )
    dec_3 = gate_3.decide(_turn(), _StubRuntime(_StubToolset(tmp_path / "three", rels_3)))
    assert dec_3.kind == "independent"
    assert dec_3.reason == "files"
    assert dec_3.choice is not None
    assert dec_3.choice.fallback_step == 1
    assert family_of(dec_3.choice.provider or "", dec_3.choice.model or "") != "anthropic"
    assert tier_of(dec_3.choice.provider or "", dec_3.choice.model or "") >= 4
    assert any(et == "verifier.model_selected" for et, _, _ in events)

    # 1 file under alembic/ -> triggers via sensitive_path
    rels_sens = _write_files(tmp_path / "sens", {"alembic/versions/0099_test.py": 10})
    gate_sens = VerificationGate(
        settings=settings,
        available_models=available,
        checkpoint_store=_checkpoint_for_new_files(tmp_path / "sens", rels_sens),
        workspace_root=tmp_path / "sens",
    )
    dec_sens = gate_sens.decide(_turn(), _StubRuntime(_StubToolset(tmp_path / "sens", rels_sens)))
    assert dec_sens.kind == "independent"
    assert dec_sens.reason == "sensitive_path"


def test_criterion_3_fallback_steps_2_3_and_4_with_quota(tmp_path: Path) -> None:
    """Without Codex/agy, falls back to step 2 (different same-family >=4) or step 3 (same model); step 4 records skipped."""
    rels = _write_files(tmp_path, {"a.py": 10, "b.py": 10, "c.py": 10})
    ckpt = _checkpoint_for_new_files(tmp_path, rels)
    settings = {"orchestration": {"verifier": {"enabled": True, "modelPolicy": "cross_family", "effort": "high"}}}

    # Step 2: same family, different model >= tier 4 (claude-fable-5-2 is tier 5)
    gate_s2 = VerificationGate(
        settings=settings,
        available_models=[
            AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
            AvailableVerifierModel(provider="anthropic", model="claude-fable-5-2"),
        ],
        checkpoint_store=ckpt,
        workspace_root=tmp_path,
    )
    dec_s2 = gate_s2.decide(_turn(), _StubRuntime(_StubToolset(tmp_path, rels)))
    assert dec_s2.kind == "independent"
    assert dec_s2.choice is not None
    assert dec_s2.choice.fallback_step == 2
    assert dec_s2.choice.model == "claude-fable-5-2"

    # Step 3: only author's own model available
    gate_s3 = VerificationGate(
        settings=settings,
        available_models=[AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5")],
        checkpoint_store=ckpt,
        workspace_root=tmp_path,
    )
    dec_s3 = gate_s3.decide(_turn(), _StubRuntime(_StubToolset(tmp_path, rels)))
    assert dec_s3.kind == "independent"
    assert dec_s3.choice is not None
    assert dec_s3.choice.fallback_step == 3
    assert dec_s3.choice.model == "claude-opus-5-5"

    # Step 4: all subscriptions blocked by quota policy -> records skipped in review_recorder
    recorder = _FakeReviewRecorder()
    blocked_quota = QuotaStatus(
        max_percent=95.0, rejected=False, confidence=Confidence.MEASURED, observed_at=datetime.now(UTC)
    )
    settings_quota = {
        "orchestration": {"verifier": {"enabled": True, "modelPolicy": "cross_family"}},
        "quota": {"policy": {"enabled": True, "blockBackgroundPercent": 90}},
    }
    gate_s4 = VerificationGate(
        settings=settings_quota,
        available_models=[
            AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5", quota_status=blocked_quota),
            AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol", quota_status=blocked_quota),
        ],
        review_recorder=recorder,
        checkpoint_store=ckpt,
        workspace_root=tmp_path,
    )
    dec_s4 = gate_s4.decide(_turn(), _StubRuntime(_StubToolset(tmp_path, rels)))
    assert dec_s4.kind == "skip"
    assert dec_s4.choice is not None and dec_s4.choice.fallback_step == 4
    assert len(recorder.started) == 1
    assert recorder.started[0]["fallback_step"] == 4
    assert recorder.started[0]["reviewer_model"] is None
    assert len(recorder.finished) == 1
    assert recorder.finished[0]["verdict"] == "skipped"


def test_visible_cost_event_before_launch_and_k17_pipeline_contract() -> None:
    """A-RF12 emits verifier.started BEFORE launching child; K17 accepts trigger_reason='pipeline' and scope_files."""
    timeline: list[str] = []
    emitted: list[tuple[str, str, dict[str, Any]]] = []
    recorder = _FakeReviewRecorder()
    captured_prompt: list[str] = []

    def fake_emit(event_type: str, summary: str, payload: dict[str, Any]) -> None:
        timeline.append(f"event:{event_type}")
        emitted.append((event_type, summary, dict(payload)))

    def fake_run_child(subagent_type: str, task: str, **kwargs: Any) -> ToolOutcome:
        timeline.append("run_child")
        captured_prompt.append(task)
        return ToolOutcome(
            "succeeded",
            "verifier",
            "ok",
            {
                "usage": {"input_tokens": 1200, "output_tokens": 350, "cost_usd": 0.04},
                "verification_report": {
                    "verdict": "pass",
                    "checked": [{"command": "pytest -q", "exit_code": 0, "summary": "10 passed"}],
                    "findings": [],
                    "unverified": [],
                },
            },
        )

    gate = VerificationGate(
        review_recorder=recorder,
        run_child=fake_run_child,
        event_emitter=fake_emit,
    )
    choice = VerifierChoice(
        provider="openai-codex",
        model="gpt-6.1-sol",
        effort="high",
        policy="cross_family",
        fallback_step=1,
        reason="cross_family_match",
    )
    summary = ChangeSummary(
        files=2,
        lines=42,
        lines_known=True,
        sensitive_hits=(),
        changed_files=("src/x.py", "src/y.py"),
        triggered=True,
        reason="files",
    )

    outcome = gate.run_independent(
        _turn(),
        summary,
        choice,
        trigger_reason="pipeline",
        scope_files=["src/x.py", "src/y.py"],
    )

    assert timeline[0] == "event:verifier.started"
    assert timeline[1] == "run_child"
    assert timeline[2] == "event:verifier.finished"

    started_evt = emitted[0]
    assert "Verificando com gpt-6.1-sol (openai-codex, esforço high): pipeline" == started_evt[2]["message"]
    assert "src/x.py" in captured_prompt[0] and "src/y.py" in captured_prompt[0]

    assert outcome.verdict == "pass"
    assert outcome.passed is True
    assert outcome.trigger_reason == "pipeline"
    assert outcome.review_ids == ("rev-1",)
    assert recorder.started[0]["trigger_reason"] == "pipeline"
    assert recorder.finished[0]["verdict"] == "pass"
    assert recorder.finished[0]["input_tokens"] == 1200
    assert recorder.finished[0]["output_tokens"] == 350
    assert recorder.finished[0]["cost_usd"] == 0.04


def test_effort_escalation_after_two_failures_and_model_switch_proposal_at_cap(tmp_path: Path) -> None:
    """E-RF21: after 2 verifier failures raises effort one step; at cap proposes model switch without switching."""
    events: list[tuple[str, str, dict[str, Any]]] = []
    escalator = EffortEscalator()
    settings = {"effort": {"escalateOnFailure": True}}

    def failing_child(*args: Any, **kwargs: Any) -> ToolOutcome:
        return ToolOutcome(
            "succeeded",
            "verifier",
            "fail",
            {
                "verification_report": {
                    "verdict": "fail",
                    "checked": [{"command": "pytest", "exit_code": 1, "summary": "1 failed"}],
                    "findings": [
                        {
                            "id": "F1",
                            "severity": "blocker",
                            "category": "test",
                            "claim": "pytest failed",
                            "evidence": {"kind": "failing_test", "command": "pytest", "exit_code": 1},
                        }
                    ],
                    "unverified": [],
                }
            },
        )

    gate = VerificationGate(
        settings=settings,
        effort_escalator=escalator,
        run_child=failing_child,
        event_emitter=lambda et, s, p: events.append((et, s, dict(p))),
    )
    choice = VerifierChoice("openai-codex", "gpt-6.1-sol", "high", "cross_family", 1, "match")
    rt = _StubRuntime(_StubToolset(tmp_path, ["a.py"]), effort="medium")
    turn = _turn(provider="anthropic", model_id="claude-opus-5-5")

    out1 = gate.run_independent(turn, None, choice, runtime=rt)
    assert out1.escalation_advice is not None and out1.escalation_advice.escalated is False
    assert rt.effort == "medium"

    out2 = gate.run_independent(turn, None, choice, runtime=rt)
    assert out2.escalation_advice is not None and out2.escalation_advice.escalated is True
    assert out2.escalation_advice.next_effort == "high"
    assert rt.effort == "high"
    assert turn["model_id"] == "claude-opus-5-5"  # never auto-switched

    # At cap (max): proposes model switch without changing model_id
    rt.effort = "max"
    out3 = gate.run_independent(turn, None, choice, runtime=rt)
    assert out3.escalation_advice is not None and out3.escalation_advice.propose_model_switch is True
    assert turn["model_id"] == "claude-opus-5-5"
    assert any(
        et == "effort.adjusted" and p.get("propose_model_switch") is True
        for et, _, p in events
    )


def test_memory_promotion_gate_promotes_on_pass_and_keeps_on_fail() -> None:
    """D-RF10: verdict 'pass' promotes agent memory proposals; verdict 'fail' leaves them unpromoted."""

    class _FakePromotionBackend:
        def __init__(self) -> None:
            self.promoted: list[str] = []
            self._items = [
                ProposedMemoryRecord(
                    memory_id="mem-1",
                    user_id="user-1",
                    scope_type="agent",
                    scope_id="agent:writer",
                    project_id="proj-1",
                    agent_key="writer",
                    text_hash="h1",
                    fact="Use uv run python",
                )
            ]

        def list_proposed_for_turn(self, turn_id: str, *, user_id: str | None = None):
            return list(self._items)

        def promote_proposal(self, memory_id: str, *, actor: str, turn_id: str, now: datetime) -> PromotionOutcome:
            self.promoted.append(memory_id)
            return PromotionOutcome(memory_id=memory_id, outcome="promoted", active_memory_id=memory_id)

        def expire_proposals(self, older_than: datetime, *, now: datetime, actor: str = "system", scope_type: str | None = "agent"):
            return []

    backend = _FakePromotionBackend()
    promo_gate = MemoryPromotionGate(backend, user_id="user-1")
    choice = VerifierChoice("openai-codex", "gpt-6.1-sol", "high", "cross_family", 1, "match")

    def run_verdict(verdict: str):
        def _runner(*args: Any, **kwargs: Any) -> ToolOutcome:
            return ToolOutcome(
                "succeeded",
                "verifier",
                verdict,
                {"verification_report": {"verdict": verdict, "checked": [], "findings": [], "unverified": []}},
            )
        return _runner

    # Fail -> does NOT promote
    gate_fail = VerificationGate(promotion_gate=promo_gate, run_child=run_verdict("fail"))
    gate_fail.run_independent(_turn(), None, choice)
    assert backend.promoted == []

    # Pass -> promotes proposal
    gate_pass = VerificationGate(promotion_gate=promo_gate, run_child=run_verdict("pass"))
    gate_pass.run_independent(_turn(), None, choice)
    assert backend.promoted == ["mem-1"]

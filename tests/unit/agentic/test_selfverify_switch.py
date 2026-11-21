"""Tests for orchestration.selfVerify.enabled switch (0.13 WS-A5, Criterion 2 & A-RF8)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.phases import Phase, PhaseController
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.verification_gate import VerificationGate
from agentos.agentic.verifier_policy import AvailableVerifierModel
from agentos.settings.loader import load_effective_settings
from agentos.settings.paths import orin_managed_dir, orin_user_dir
from agentos.settings.registry import ResolvedSetting, resolve


def _turn() -> dict[str, object]:
    return {
        "turn_id": "turn-sv-1",
        "conversation_id": "conv-sv-1",
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

    def load(self, turn_id: str) -> dict[str, object]:
        return self.turn

    def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, str]]:
        return [{"role": "user", "content": "atualize os arquivos"}]

    def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
        self.events.append((state, dict(payload)))

    def delta(self, turn: dict[str, object], text: str) -> None:
        self.text.append(text)

    def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
        pass


def _tool_call(call_id: str, name: str, payload: dict[str, Any]) -> str:
    escaped = json.dumps(json.dumps(payload))[1:-1]
    return (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"%s","function":'
        '{"name":"%s","arguments":"%s"}}]},"finish_reason":"tool_calls"}]}'
        % (call_id, name, escaped)
    )


_ANSWER = 'data: {"choices":[{"delta":{"content":"feito"},"finish_reason":"stop"}]}'


class _ScriptedProvider:
    def __init__(self, script: list[str]) -> None:
        self.script = script
        self.requests: list[dict[str, object]] = []

    def stream(self, request: dict[str, object]):
        self.requests.append(request)
        line = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        return normalize_sse([line, "data: [DONE]"], provider="openrouter")


class _WorkspaceToolset:
    NAMES = ("write_file", "read_file", "report_verification", "verify_project", "resolve_findings")

    def __init__(self, root: Path) -> None:
        self.workspace = type("WS", (), {"root": root})()
        self._root = root
        self._change_events = 0
        self._changed_paths: set[str] = set()

    def schemas(self, allowed=None, kinds=None):
        names = self.NAMES if allowed is None else [n for n in self.NAMES if n in set(allowed)]
        return [{"type": "function", "function": {"name": name, "parameters": {}}} for name in names]

    def is_read_only(self, name: str) -> bool:
        return name == "read_file"

    def is_mutating(self, name: str) -> bool:
        return name == "write_file"

    def argument_names(self, name: str):
        return None

    def change_events(self) -> int:
        return self._change_events

    def invoke(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        if name == "write_file":
            rel = str(arguments.get("path") or "file.py")
            target = self._root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(arguments.get("content") or "x = 1\n"), encoding="utf-8")
            self._change_events += 1
            self._changed_paths.add(rel)
            return ToolOutcome("succeeded", "write_file", f"wrote {rel}", {})
        if name == "report_verification":
            passed = bool(arguments.get("passed", True))
            return ToolOutcome("succeeded", "report_verification", "ok", {"passed": passed, "findings": "ok"})
        return ToolOutcome("succeeded", name, "ok", {})


def _write_ten_files_script() -> list[str]:
    calls = [_tool_call(f"c{i}", "write_file", {"path": f"src/mod_{i}.py", "content": f"v = {i}\n"}) for i in range(10)]
    return calls


def test_criterion_2a_self_verify_enabled_repeats_012_behavior(tmp_path: Path) -> None:
    """Criterion 2(a): verifier off + selfVerify.enabled=True triggers extra VERIFY pass as in 0.12.31."""
    script = [
        *_write_ten_files_script(),
        _ANSWER,  # tries to answer -> forced into Phase.VERIFY with VERIFY_REQUIRED_MESSAGE
        _tool_call("rv", "report_verification", {"passed": True, "findings": "ok"}),
        _ANSWER,
    ]
    provider = _ScriptedProvider(script)
    store = _Store()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=_WorkspaceToolset(tmp_path),
        system_prompt="prompt",
        limits=AgenticLimits(max_iterations=None, max_actions=None),
        phase_controller=PhaseController(),
        effective_settings={"orchestration": {"selfVerify": {"enabled": True}}},
    )

    result = runtime.run("turn-sv-1")

    assert result.state == "completed"
    assert runtime.phases is not None and runtime.phases.current is Phase.RESPOND
    assert any("Você alterou o workspace" in str(r["messages"]) for r in provider.requests)
    assert any(
        state == "phase_changed" and payload.get("phase") == "verify"
        for state, payload in store.events
    )


def test_criterion_2b_default_absent_key_skips_extra_self_verify_round(tmp_path: Path) -> None:
    """Criterion 2(b): with default settings (both switches off), changing 10 files answers without extra VERIFY round."""
    script = [*_write_ten_files_script(), _ANSWER]
    provider = _ScriptedProvider(script)
    store = _Store()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=_WorkspaceToolset(tmp_path),
        system_prompt="prompt",
        limits=AgenticLimits(max_iterations=None, max_actions=None),
        phase_controller=PhaseController(),
    )

    result = runtime.run("turn-sv-1")

    assert result.state == "completed"
    assert len(provider.requests) == 11  # 10 writes + 1 final answer; zero extra self-verify rounds
    assert not any("Você alterou o workspace" in str(r["messages"]) for r in provider.requests)
    assert not any(
        state == "phase_changed" and payload.get("phase") == "verify"
        for state, payload in store.events
    )


def test_independent_verifier_replaces_self_verify_never_runs_both(tmp_path: Path) -> None:
    """When independent verifier triggers, self-verify is NOT run even if selfVerify.enabled=True."""
    child_calls: list[dict[str, Any]] = []

    def fake_run_child(subagent_type: str, task: str, **kwargs: Any) -> ToolOutcome:
        child_calls.append({"subagent_type": subagent_type, "task": task, **kwargs})
        return ToolOutcome(
            "succeeded",
            "verifier",
            "ok",
            {"verification_report": {"verdict": "pass", "checked": [], "findings": [], "unverified": []}},
        )

    settings = {
        "orchestration": {
            "selfVerify": {"enabled": True},
            "verifier": {"enabled": True, "threshold": {"files": 3}},
        }
    }
    gate = VerificationGate(
        settings=settings,
        available_models=[
            AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
            AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
        ],
        run_child=fake_run_child,
        workspace_root=tmp_path,
    )
    script = [
        _tool_call("c1", "write_file", {"path": "a.py", "content": "1\n"}),
        _tool_call("c2", "write_file", {"path": "b.py", "content": "2\n"}),
        _tool_call("c3", "write_file", {"path": "c.py", "content": "3\n"}),
        _ANSWER,
    ]
    provider = _ScriptedProvider(script)
    store = _Store()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=_WorkspaceToolset(tmp_path),
        system_prompt="prompt",
        limits=AgenticLimits(max_iterations=None, max_actions=None),
        phase_controller=PhaseController(),
        effective_settings=settings,
        verification_gate=gate,
    )

    result = runtime.run("turn-sv-1")

    assert result.state == "completed"
    assert len(child_calls) == 1
    assert child_calls[0]["subagent_type"] == "verifier"
    # Never triggered legacy self-verify alongside independent verification
    assert not any("Você alterou o workspace" in str(r["messages"]) for r in provider.requests)
    assert not any(
        state == "phase_changed" and payload.get("phase") == "verify"
        for state, payload in store.events
    )


def test_step4_no_model_falls_back_to_self_verify_only_when_enabled(tmp_path: Path) -> None:
    """Step 4 (all subscriptions unavailable) falls back to self_verify ONLY if selfVerify.enabled=True."""
    for self_verify_on, expected_verify_phase in ((True, True), (False, False)):
        settings = {
            "orchestration": {
                "selfVerify": {"enabled": self_verify_on},
                "verifier": {"enabled": True, "threshold": {"files": 1}},
            }
        }
        gate = VerificationGate(
            settings=settings,
            available_models=[],  # forces fallback step 4
            workspace_root=tmp_path,
        )
        script = (
            [
                _tool_call("c1", "write_file", {"path": "a.py", "content": "1\n"}),
                _ANSWER,
                _tool_call("rv", "report_verification", {"passed": True, "findings": "ok"}),
                _ANSWER,
            ]
            if self_verify_on
            else [
                _tool_call("c1", "write_file", {"path": "a.py", "content": "1\n"}),
                _ANSWER,
            ]
        )
        provider = _ScriptedProvider(script)
        store = _Store()
        runtime = AgenticTurnRuntime(
            store=store,
            provider=provider,
            toolset=_WorkspaceToolset(tmp_path),
            system_prompt="prompt",
            limits=AgenticLimits(max_iterations=None, max_actions=None),
            phase_controller=PhaseController(),
            effective_settings=settings,
            verification_gate=gate,
        )

        result = runtime.run("turn-sv-1")

        assert result.state == "completed"
        saw_verify_phase = any(
            state == "phase_changed" and payload.get("phase") == "verify"
            for state, payload in store.events
        )
        assert saw_verify_phase is expected_verify_phase
        assert any(
            state == "verifier.finished" and payload.get("verdict") == "skipped"
            for state, payload in store.events
        )


def test_project_settings_cannot_enable_self_verify_true(tmp_path: Path) -> None:
    """A malicious repository setting orchestration.selfVerify.enabled=true is ignored."""
    repo = tmp_path / "repo"
    (repo / ".orin").mkdir(parents=True, exist_ok=True)
    (repo / ".orin" / "settings.json").write_text(
        json.dumps({"orchestration": {"selfVerify": {"enabled": True}}}),
        encoding="utf-8",
    )
    orin_user_dir().mkdir(parents=True, exist_ok=True)
    (orin_user_dir() / "settings.json").write_text("{}", encoding="utf-8")
    orin_managed_dir().mkdir(parents=True, exist_ok=True)
    (orin_managed_dir() / "managed-settings.json").write_text("{}", encoding="utf-8")

    loaded = load_effective_settings(repo, trusted=True)
    assert resolve(loaded, "orchestration.selfVerify.enabled") == ResolvedSetting(
        value=False, layer="default", default=False
    )

    # However, a project CAN turn it off when the user enabled it (PROJECT_OFF_ONLY)
    (orin_user_dir() / "settings.json").write_text(
        json.dumps({"orchestration": {"selfVerify": {"enabled": True}}}),
        encoding="utf-8",
    )
    (repo / ".orin" / "settings.json").write_text(
        json.dumps({"orchestration": {"selfVerify": {"enabled": False}}}),
        encoding="utf-8",
    )
    loaded_off = load_effective_settings(repo, trusted=True)
    assert resolve(loaded_off, "orchestration.selfVerify.enabled") == ResolvedSetting(
        value=False, layer="project", default=False
    )

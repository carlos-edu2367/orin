"""Tests for orchestration.verifier.includeSubagents (A-RF9)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentos.agentic.agent_tools import ToolOutcome
from agentos.agentic.provider_stream import normalize_sse
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.verification_gate import VerificationGate
from agentos.agentic.verifier_policy import AvailableVerifierModel


class _SubStore:
    def __init__(self, turn: dict[str, object]) -> None:
        self.turn = turn
        self.events: list[tuple[str, dict[str, object]]] = []
        self.text: list[str] = []

    def load(self, turn_id: str) -> dict[str, object]:
        return self.turn

    def history_for_turn(self, turn: dict[str, object]) -> list[dict[str, str]]:
        return [{"role": "user", "content": "faça a alteração"}]

    def lifecycle(self, turn: dict[str, object], state: str, **payload: object) -> None:
        self.events.append((state, dict(payload)))

    def delta(self, turn: dict[str, object], text: str) -> None:
        self.text.append(text)

    def finish(self, turn: dict[str, object], *, failed: bool = False, code: str | None = None) -> None:
        pass


class _SubToolset:
    _code_mode_active = True

    def __init__(self, root: Path) -> None:
        self.workspace = type("WS", (), {"root": root})()
        self._root = root
        self._change_events = 0
        self._changed_paths: set[str] = set()

    def schemas(self, allowed=None, kinds=None):
        return [{"type": "function", "function": {"name": "write_file", "parameters": {}}}]

    def is_read_only(self, name: str) -> bool:
        return False

    def is_mutating(self, name: str) -> bool:
        return name == "write_file"

    def argument_names(self, name: str):
        return None

    def change_events(self) -> int:
        return self._change_events

    def code_verification_status(self) -> str:
        return "unverified"

    def invoke(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        rel = str(arguments.get("path") or "a.py")
        p = self._root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")
        self._change_events += 1
        self._changed_paths.add(rel)
        return ToolOutcome("succeeded", "write_file", f"wrote {rel}", {})


def _tool_call(call_id: str, path: str) -> str:
    escaped = json.dumps(json.dumps({"path": path, "content": "x = 1\n"}))[1:-1]
    return (
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"%s","function":'
        '{"name":"write_file","arguments":"%s"}}]},"finish_reason":"tool_calls"}]}'
        % (call_id, escaped)
    )


_ANSWER = 'data: {"choices":[{"delta":{"content":"pronto"},"finish_reason":"stop"}]}'


class _ScriptedProvider:
    def __init__(self, script: list[str]) -> None:
        self.script = script
        self.requests: list[dict[str, object]] = []

    def stream(self, request: dict[str, object]):
        self.requests.append(request)
        line = self.script[min(len(self.requests) - 1, len(self.script) - 1)]
        return normalize_sse([line, "data: [DONE]"], provider="openrouter")


def _subagent_turn(subagent_type: str = "general") -> dict[str, object]:
    return {
        "turn_id": "turn-sub-1",
        "conversation_id": "conv-sub-1",
        "user_id": "user-1",
        "provider": "anthropic",
        "model_id": "claude-opus-5-5",
        "user_message_id": "u-1",
        "assistant_message_id": "a-1",
        "code_mode": "code",
        "is_subagent": True,
        "subagent_type": subagent_type,
    }


def test_general_subagent_excluded_by_default_and_verified_when_include_subagents_on(tmp_path: Path) -> None:
    """A-RF9: includeSubagents=False skips general subagent and returns None in _code_verification; True runs gate."""
    for include_subagents in (False, True):
        sub_root = tmp_path / f"sub_{include_subagents}"
        sub_root.mkdir(parents=True, exist_ok=True)
        verifier_calls: list[dict[str, Any]] = []

        def fake_run_child(subagent_type: str, task: str, **kwargs: Any) -> ToolOutcome:
            verifier_calls.append({"subagent_type": subagent_type, **kwargs})
            return ToolOutcome(
                "succeeded",
                "verifier",
                "pass",
                {"verification_report": {"verdict": "pass", "checked": [], "findings": [], "unverified": []}},
            )

        settings = {
            "orchestration": {
                "verifier": {
                    "enabled": True,
                    "includeSubagents": include_subagents,
                    "threshold": {"files": 3},
                }
            }
        }
        gate = VerificationGate(
            settings=settings,
            available_models=[
                AvailableVerifierModel(provider="anthropic", model="claude-opus-5-5"),
                AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol"),
            ],
            run_child=fake_run_child,
            workspace_root=sub_root,
        )
        turn = _subagent_turn("general")
        store = _SubStore(turn)
        provider = _ScriptedProvider([
            _tool_call("c1", "a.py"),
            _tool_call("c2", "b.py"),
            _tool_call("c3", "c.py"),
            _ANSWER,
        ])
        runtime = AgenticTurnRuntime(
            store=store,
            provider=provider,
            toolset=_SubToolset(sub_root),
            system_prompt="prompt",
            limits=AgenticLimits(max_iterations=None, max_actions=None),
            effective_settings=settings,
            verification_gate=gate,
        )

        result = runtime.run("turn-sub-1", turn=turn)

        assert result.state == "completed"
        if include_subagents:
            assert len(verifier_calls) == 1
            assert verifier_calls[0]["subagent_type"] == "verifier"
            assert result.verification == "unverified"
        else:
            assert verifier_calls == []
            assert result.verification is None


def test_verifier_and_readonly_subagents_never_recurse_even_when_include_subagents_on(tmp_path: Path) -> None:
    """Even with includeSubagents=True, verifier/explore/plan subagents are never verified."""
    settings = {
        "orchestration": {
            "verifier": {"enabled": True, "includeSubagents": True, "threshold": {"files": 1}},
        }
    }
    gate = VerificationGate(
        settings=settings,
        available_models=[AvailableVerifierModel(provider="openai-codex", model="gpt-6.1-sol")],
        workspace_root=tmp_path,
    )
    toolset = _SubToolset(tmp_path)
    toolset._change_events = 5
    toolset._changed_paths = {"a.py"}

    for kind in ("verifier", "explore", "plan"):
        turn = _subagent_turn(kind)
        runtime = AgenticTurnRuntime(
            store=_SubStore(turn),
            provider=_ScriptedProvider([_ANSWER]),
            toolset=toolset,
            system_prompt="prompt",
            effective_settings=settings,
            verification_gate=gate,
        )
        decision = gate.decide(turn, runtime)
        assert decision.kind == "skip"
        assert runtime._code_verification(turn) is None

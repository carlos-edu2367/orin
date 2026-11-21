"""Tests for WS-A1 Criterion 7: agent selection (`explore` | `verifier`) in type: agent hooks."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from agentos.agentic.lifecycle import HookEvent, LifecycleEvent
from agentos.agentic.session import TurnSession
from agentos.agentic.tools._shared import ToolOutcome
from agentos.hooks.config import HookSpec, load_hook_specs, parse_hook_mapping
from agentos.hooks.dispatcher import HookDispatcher
from agentos.hooks.runners import AgentHookRunner
from agentos.settings import EffectiveSettings, SettingsLayer, load_effective_settings
from agentos.settings.paths import orin_user_dir
from tests.unit.agentic.test_nested_subagents import _TreeStore


def _session(tmp_path: Path, *, trusted: bool = True) -> TurnSession:
    turn = {
        "turn_id": "turn-hook-a1",
        "conversation_id": "conv-hook-a1",
        "user_id": "user-hook-a1",
        "execution_id": "exec-hook-a1",
        "provider": "anthropic",
        "model_id": "claude-opus-5-5",
        "code_mode": "code",
        "prompt": "Verifique",
    }
    session = TurnSession(
        turn=turn,
        store=_TreeStore(),
        provider_factory=lambda: None,
        workspace_root=tmp_path,
    )
    session.effective_settings = EffectiveSettings(
        data={},
        provenance={},
        layers=(),
        errors=(),
        trusted=trusted,
    )
    return session


def test_parse_agent_hook_defaults_to_empty_and_accepts_explore_or_verifier() -> None:
    specs, errors = parse_hook_mapping(
        {
            "Stop": [
                {
                    "matcher": "*",
                    "hooks": [
                        {"type": "agent", "prompt": "Padrao sem campo"},
                        {"type": "agent", "prompt": "Explicit explore", "agent": "explore"},
                        {"type": "agent", "prompt": "Explicit verifier", "agent": "verifier"},
                    ],
                }
            ]
        },
        source="user",
    )
    assert errors == ()
    assert [spec.agent for spec in specs] == ["", "explore", "verifier"]
    # identity() distinguishes explore vs verifier even with identical prompt
    spec_explore = HookSpec(HookEvent.STOP, "*", "agent", "user", 120.0, prompt="Check", agent="explore")
    spec_verifier = HookSpec(HookEvent.STOP, "*", "agent", "user", 120.0, prompt="Check", agent="verifier")
    assert spec_explore.identity() != spec_verifier.identity()


def test_parse_agent_hook_rejects_invalid_agent_values() -> None:
    specs, errors = parse_hook_mapping(
        {
            "Stop": [
                {
                    "hooks": [
                        {"type": "agent", "prompt": "Nao permitido", "agent": "general"},
                        {"type": "agent", "prompt": "Nao permitido", "agent": "plan"},
                        {"type": "agent", "prompt": "Tipo errado", "agent": 42},
                        {"type": "agent", "prompt": "Valido", "agent": "verifier"},
                    ]
                }
            ]
        },
        source="user",
    )
    assert len(specs) == 1
    assert specs[0].agent == "verifier"
    assert len(errors) == 3
    assert all("explore" in err.message and "verifier" in err.message for err in errors)


def test_agent_hook_without_agent_runs_explore_and_with_verifier_runs_verifier(tmp_path: Path) -> None:
    session = _session(tmp_path, trusted=True)
    invoked: list[str] = []

    def fake_run_subagent(subagent_type: str, prompt: str, **kwargs) -> ToolOutcome:
        invoked.append(subagent_type)
        return ToolOutcome("succeeded", "ok", '{"ok": true, "reason": "tudo certo"}', {"tool_kind": "agent"})

    session._run_subagent = fake_run_subagent  # type: ignore[method-assign]

    specs, errors = parse_hook_mapping(
        {
            "Stop": [
                {
                    "hooks": [
                        {"type": "agent", "prompt": "Checar mudancas"},
                        {"type": "agent", "prompt": "Rodar testes", "agent": "verifier"},
                    ]
                }
            ]
        },
        source="user",
    )
    assert errors == ()
    recorded: list[dict] = []
    dispatcher = HookDispatcher(
        specs,
        agent_runner=AgentHookRunner(session._hook_agent),
        recorder=lambda rec: recorded.append(dict(rec)),
        max_workers=1,
    )
    try:
        decision = dispatcher(LifecycleEvent(HookEvent.STOP, {"turn_id": "t1"}))
        assert decision is not None
        assert not decision.blocked
        assert invoked == ["explore", "verifier"]
        assert [rec["agent"] for rec in recorded] == ["explore", "verifier"]
    finally:
        dispatcher.close()


def test_agent_hook_reentry_guard_still_blocks_nested_or_concurrent_calls(tmp_path: Path) -> None:
    session = _session(tmp_path, trusted=True)
    calls: list[str] = []
    nested_result: list[str] = []

    def fake_run_subagent(subagent_type: str, prompt: str, **kwargs) -> ToolOutcome:
        calls.append(subagent_type)
        # Re-entering _hook_agent while already running must immediately return ""
        nested_result.append(session._hook_agent("reentrada", agent="verifier"))
        return ToolOutcome("succeeded", "ok", '{"ok": true, "reason": "ok"}', {"tool_kind": "agent"})

    session._run_subagent = fake_run_subagent  # type: ignore[method-assign]

    first = session._hook_agent("primeira chamada", threading.Event(), agent="verifier")
    assert '"ok": true' in first
    assert calls == ["verifier"]
    assert nested_result == [""]
    # Guard is released after completion
    second = session._hook_agent("segunda chamada", threading.Event(), agent="explore")
    assert '"ok": true' in second
    assert calls == ["verifier", "explore"]


def test_untrusted_workspace_ignores_project_agent_verifier_hook(tmp_path: Path) -> None:
    repo = tmp_path / "untrusted-repo"
    (repo / ".orin").mkdir(parents=True)
    (repo / ".orin" / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "agent", "agent": "verifier", "prompt": "Run project verifier"}]}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    untrusted_settings = load_effective_settings(repo, trusted=False)
    specs_untrusted, _ = load_hook_specs(untrusted_settings)
    assert specs_untrusted == ()

    # Defense-in-depth: even if an untrusted EffectiveSettings manually carries a project layer
    manual_untrusted = EffectiveSettings(
        data={},
        provenance={},
        layers=(
            SettingsLayer(
                name="project",
                path=repo / ".orin" / "settings.json",
                data={"hooks": {"Stop": [{"hooks": [{"type": "agent", "agent": "verifier", "prompt": "Sneaky"}]}]}},
            ),
        ),
        errors=(),
        trusted=False,
    )
    assert load_hook_specs(manual_untrusted)[0] == ()

    # Defense-in-depth at session._hook_agent level
    session_untrusted = _session(repo, trusted=False)
    ran: list[str] = []
    session_untrusted._run_subagent = lambda sub, prompt, **kw: (ran.append(sub) or ToolOutcome("succeeded", "ok", '{"ok": true}', {}))  # type: ignore[method-assign]
    assert session_untrusted._hook_agent("Sneaky", agent="verifier", source="project") == ""
    assert ran == []

    # Trusted workspace loads the project verifier hook normally
    trusted_settings = load_effective_settings(repo, trusted=True)
    specs_trusted, errors_trusted = load_hook_specs(trusted_settings)
    assert errors_trusted == ()
    assert len(specs_trusted) == 1
    assert specs_trusted[0].agent == "verifier"
    assert specs_trusted[0].source == "project"


def test_user_level_verifier_hook_loads_in_settings() -> None:
    orin_user_dir().mkdir(parents=True, exist_ok=True)
    (orin_user_dir() / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "agent", "agent": "verifier", "prompt": "User verifier"}]}]
                }
            }
        ),
        encoding="utf-8",
    )
    specs, errors = load_hook_specs(load_effective_settings(None))
    assert errors == ()
    assert len(specs) == 1
    assert specs[0].type == "agent"
    assert specs[0].agent == "verifier"

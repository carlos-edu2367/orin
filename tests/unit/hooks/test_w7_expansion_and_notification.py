"""W7: UserPromptExpansion sees a typed command before it expands (and may block it); a permission prompt fires a
Notification hook with notification_type permission_prompt."""
from __future__ import annotations

import sys
from pathlib import Path

from agentos.agentic.lifecycle import HookEvent, LifecycleBus, LifecycleDecision
from agentos.hooks.matching import matcher_applies
from agentos.settings.paths import orin_user_dir

from agentos.agentic.session import TurnSession

from tests.unit.cli_code.test_cli_turn_session import _Agents, _Store
from tests.unit.hooks.test_session_hooks_w2 import _user


def _skill(name: str) -> None:
    folder = orin_user_dir() / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\ndescription: {name}\n---\nSKILL-BODY-{name}", encoding="utf-8")


def _session(tmp_path: Path, prompt: str) -> TurnSession:
    store = _Store()
    store.history_for_turn = lambda turn: [{"role": "user", "content": prompt}]  # type: ignore[method-assign]
    session = TurnSession(turn={**store.turn, "code_mode": "code"}, store=store, agents_store=_Agents(), memory_store=None,
                          provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False)
    session.plugin_hooks = True  # the local profile: the person's own skills load
    return session


def _texts(session: TurnSession) -> str:
    return "\n".join(item.text for item in session._reminders._items)


def test_expansion_matches_on_the_command_name() -> None:
    assert matcher_applies(HookEvent.USER_PROMPT_EXPANSION, "deploy", {"command_name": "deploy"})
    assert not matcher_applies(HookEvent.USER_PROMPT_EXPANSION, "deploy", {"command_name": "review"})


def test_a_hook_blocks_a_typed_command_and_another_adds_context(tmp_path: Path) -> None:
    _skill("deploy")
    _skill("review")
    block = tmp_path / "block.py"
    block.write_text("import sys; print('deploys need a ticket', file=sys.stderr); sys.exit(2)", encoding="utf-8")
    note = tmp_path / "note.py"
    note.write_text("import json, sys; d = json.load(sys.stdin); print('expanded ' + d['command_name'] + ' via ' + d['expansion_type'])", encoding="utf-8")
    _user({"hooks": {"UserPromptExpansion": [
        {"matcher": "deploy", "hooks": [{"type": "command", "command": f'"{sys.executable}" "{block}"'}]},
        {"matcher": "review", "hooks": [{"type": "command", "command": f'"{sys.executable}" "{note}"'}]},
    ]}})
    blocked = _session(tmp_path / "a", "/deploy now")
    blocked.build_runtime()
    reminders = _texts(blocked)
    assert "SKILL-BODY-deploy" not in reminders and "deploys need a ticket" in reminders
    allowed = _session(tmp_path / "b", "/review 12")
    allowed.build_runtime()
    text = _texts(allowed)
    assert "SKILL-BODY-review" in text and "expanded review via slash_command" in text


def test_a_permission_prompt_notifies() -> None:
    events: list = []
    bus = LifecycleBus()
    bus.subscribe(lambda event: events.append(event) or LifecycleDecision())

    class _Session:
        lifecycle = bus

    TurnSession._notify_permission_prompt(_Session(), "Permissão necessária para executar `rm x`")  # type: ignore[arg-type]
    assert [(event.name, event.payload["notification_type"]) for event in events] == [(HookEvent.NOTIFICATION, "permission_prompt")]
    assert "rm x" in events[0].payload["message"]

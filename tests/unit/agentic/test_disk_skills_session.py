"""W5: the turn loads disk skills (project ones only when trusted), lists them, and runs a person's /command."""
from __future__ import annotations

from pathlib import Path

from agentos.agentic.reminders import COMMAND_REMINDER_CHARS, Reminder, render_reminders
from agentos.agentic.session import TurnSession
from agentos.settings.paths import orin_user_dir


class _Store:
    def __init__(self, message: str) -> None:
        self.message = message

    def main_agent_id(self, turn):
        return f"agent:{turn['conversation_id']}:main"

    def history_for_turn(self, turn):
        return [{"role": "user", "content": self.message}]

    def record(self, *args, **kwargs):
        return None


class _Agents:
    def list(self):
        return []


def _skill(root: Path, name: str, text: str) -> None:
    folder = root / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_bytes(text.encode("utf-8"))


def _session(tmp_path: Path, message: str) -> TurnSession:
    return TurnSession(
        turn={
            "turn_id": "turn-1", "conversation_id": "conv-s", "user_id": "local-user", "execution_id": "exe-1",
            "user_message_id": "um-1", "code_mode": "code", "provider": "openrouter", "model_id": "m",
        },
        store=_Store(message), agents_store=_Agents(), memory_store=None, provider_factory=lambda: None,
        workspace_root=tmp_path / "w", cancelled=lambda _turn: False,
    )


def test_the_turn_lists_disk_skills_and_hands_them_to_the_toolset(tmp_path: Path) -> None:
    _skill(orin_user_dir(), "greet", "---\ndescription: Greets people warmly\n---\nSay hello to $ARGUMENTS.")
    _skill(orin_user_dir(), "secret", "---\ndescription: People only\ndisable-model-invocation: true\n---\nx")
    session = _session(tmp_path, "please greet the team")
    session.plugin_hooks = True  # W5-R5 review fix: the person's own skills are read only where plugin hooks may run (a desktop)
    runtime = session.build_runtime()
    assert runtime.toolset.disk_skill("greet") is not None
    names = {item.name for item in session._skill_catalog("greet", runtime.toolset)}
    assert "greet" in names and "secret" not in names


def test_project_skills_wait_for_trust(tmp_path: Path) -> None:
    session = _session(tmp_path, "hi")
    _skill(session.workspace.root / ".claude", "local-only", "---\ndescription: From the repository\n---\nx")
    runtime = session.build_runtime()
    assert runtime.toolset.disk_skill("local-only") is None


def test_a_person_runs_a_skill_with_a_slash_command(tmp_path: Path) -> None:
    _skill(orin_user_dir(), "greet", "---\ndescription: Greets\ndisable-model-invocation: true\n---\nSay hello to $ARGUMENTS.")
    session = _session(tmp_path, "/greet Ana")
    session.plugin_hooks = True  # W5-R5 review fix: the person's own commands are read only where plugin hooks may run (a desktop)
    session.build_runtime()
    texts = [item.text for item in session._reminders.drain() if item.kind == "command"]
    assert len(texts) == 1 and "/greet Ana" in texts[0] and "Say hello to Ana." in texts[0]


def test_an_unknown_or_not_user_invocable_command_is_left_alone(tmp_path: Path) -> None:
    _skill(orin_user_dir(), "internal", "---\ndescription: Model only\nuser-invocable: false\n---\nx")
    for message in ("/nothing here", "/internal now"):
        session = _session(tmp_path, message)
        session.build_runtime()
        texts = [item.text for item in session._reminders.drain() if item.kind == "command"]
        assert all("Model only" not in text and "\nx" not in text for text in texts)
        assert message != "/nothing here" or not texts


def test_command_reminders_keep_long_instructions() -> None:
    long_text = "y" * 30_000
    assert long_text in render_reminders([Reminder("command", long_text)])
    assert COMMAND_REMINDER_CHARS >= 30_000

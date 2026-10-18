from __future__ import annotations

import json
from pathlib import Path

from agentos.agentic.output_styles import BUILTIN_STYLES, load_output_styles, resolve_output_style
from agentos.code_mode.system_prompt import CodePromptOptions, render_code_system_prompt
from agentos.settings import load_effective_settings
from agentos.settings.paths import orin_user_dir


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_built_in_styles() -> None:
    assert {"default", "explanatory", "learning"} <= set(BUILTIN_STYLES)
    assert BUILTIN_STYLES["explanatory"].keep_coding_instructions is True
    assert BUILTIN_STYLES["learning"].instructions


def test_custom_styles_from_user_and_project_dirs(tmp_path: Path) -> None:
    _write(orin_user_dir() / "output-styles" / "terse.md", "---\nname: Terse\ndescription: very short\n---\nAnswer in one line.")
    _write(tmp_path / ".claude" / "output-styles" / "teacher.md", "---\nkeep-coding-instructions: true\n---\nTeach as you go.")
    styles = load_output_styles(tmp_path)
    assert styles["terse"].instructions == "Answer in one line." and styles["terse"].keep_coding_instructions is False
    assert styles["teacher"].keep_coding_instructions is True and styles["teacher"].source == "project"


def test_resolution_from_settings(tmp_path: Path) -> None:
    assert resolve_output_style(load_effective_settings(tmp_path), tmp_path) is None
    _write(orin_user_dir() / "settings.json", json.dumps({"outputStyle": "Explanatory"}))
    assert resolve_output_style(load_effective_settings(tmp_path), tmp_path).name == "Explanatory"
    _write(orin_user_dir() / "settings.json", json.dumps({"outputStyle": "nope"}))
    assert resolve_output_style(load_effective_settings(tmp_path), tmp_path) is None


def test_a_custom_style_without_coding_instructions_drops_them() -> None:
    prompt = render_code_system_prompt(CodePromptOptions(
        tool_names=frozenset({"read_file", "todo_write"}), public_names=True,
        output_style_name="Terse", output_style_instructions="Answer in one line.", keep_coding_instructions=False,
    ))
    assert "# Output style: Terse" in prompt and "Answer in one line." in prompt
    assert "# Doing tasks" not in prompt and "# Git" not in prompt


def test_the_session_uses_the_style_and_it_changes_the_snapshot_key(tmp_path: Path) -> None:
    from agentos.agentic.session import TurnSession

    class Store:
        turn = {"turn_id": "t", "conversation_id": "c", "user_id": "u", "execution_id": "e", "provider": "openrouter",
                "model_id": "m", "user_message_id": "um", "assistant_message_id": "am", "code_mode": "code"}
        def load(self, turn_id): return self.turn
        def history_for_turn(self, turn): return [{"role": "user", "content": "go"}]
        def lifecycle(self, turn, state, **payload): pass
        def delta(self, turn, text): pass
        def finish(self, turn, *, failed=False, code=None): pass
        def record(self, *args, **kwargs): pass
        def main_agent_id(self, turn): return "agent:c:main"

    class Agents:
        def list(self): return []

    def build() -> TurnSession:
        store = Store()
        session = TurnSession(turn=dict(store.turn), store=store, agents_store=Agents(), memory_store=None,
                              provider_factory=lambda: object(), workspace_root=tmp_path, enable_subagents=False)
        session.build_runtime()
        return session

    plain = build()
    key_plain = plain._snapshot_key()
    _write(orin_user_dir() / "settings.json", json.dumps({"outputStyle": "learning"}))
    styled = build()
    assert "# Output style: Learning" in styled._code_stable_prompt()
    assert styled._snapshot_key() != key_plain

from __future__ import annotations

import pytest

from agentos.agentic.native_turn import (
    SESSION_BRANCH,
    SessionPlan,
    history_digest,
    prompt_digest,
)
from agentos.providers.claude_cli.command import build_claude_command


@pytest.mark.skipif(SESSION_BRANCH != "A", reason="Spike selected SESSION_BRANCH='B'")
def test_resume_only_when_history_unchanged(tmp_path) -> None:
    from agentos.providers.claude_cli.sessions import ClaudeCliSessionStore, SessionRecord

    st = ClaudeCliSessionStore(tmp_path)
    st.put("c1", SessionRecord("u1", "h1", "p1", "m", 0))
    assert st.plan("c1", history_digest="h1", prompt_digest="p1") == SessionPlan("u1", True)
    assert st.plan("c1", history_digest="h2", prompt_digest="p1").resume is False
    assert st.plan("c1", history_digest="h1", prompt_digest="p2").resume is False


def test_history_digest_ignores_tool_steps_and_reminders() -> None:
    base_messages = [
        {"role": "user", "content": "Analise o arquivo main.py"},
        {"role": "assistant", "content": "O arquivo main.py define a função principal."},
    ]
    with_tools_and_reminders = [
        {"role": "user", "content": "Analise o arquivo main.py"},
        {"role": "user", "content": "<system-reminder>Plan mode reminder</system-reminder>", "_reminder": True},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "main.py"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": "def main(): pass"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tu_1", "name": "Read", "input": {"file_path": "main.py"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "def main(): pass"},
            ],
        },
        {"role": "assistant", "content": "O arquivo main.py define a função principal."},
    ]

    d1 = history_digest(base_messages)
    d2 = history_digest(with_tools_and_reminders)
    assert isinstance(d1, str) and len(d1) == 64
    assert d1 == d2

    edited_messages = [
        {"role": "user", "content": "Analise o arquivo app.py"},
        {"role": "assistant", "content": "O arquivo main.py define a função principal."},
    ]
    assert history_digest(edited_messages) != d1


def test_prompt_digest_is_deterministic_sha256() -> None:
    p1 = prompt_digest("You are Orin.")
    p2 = prompt_digest("You are Orin.")
    p3 = prompt_digest("You are Orin Code.")
    assert len(p1) == 64
    assert p1 == p2
    assert p1 != p3


def test_branch_b_never_persists(tmp_path) -> None:
    assert SESSION_BRANCH == "B"
    plan = SessionPlan(session_id="", resume=False)
    assert plan.resume is False
    prompt_file = tmp_path / "sys.txt"
    prompt_file.write_text("sys", encoding="utf-8")
    argv = build_claude_command(
        binary="claude",
        model="claude-opus-5-5",
        system_prompt_file=prompt_file,
    )
    assert "--no-session-persistence" in argv
    assert "--session-id" not in argv
    assert "--resume" not in argv

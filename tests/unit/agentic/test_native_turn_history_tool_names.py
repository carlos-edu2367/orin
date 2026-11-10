"""The tool names in the history handed to agy are the names its MCP tool list announces.

Context (10/11/2026, turn_4cb01cd2): the history said `name=Bash` and agy called `mcp_orin_Bash`. That is the
name agy shows for Orin's `Bash` tool in Code mode (Claude Code names, `public_tool_names`), and those calls
run (transcripts of the same day); the turn died because of `manage_task`, not the name. This pins the
contract so the history and the tool list can never drift apart: in Code mode both say `Bash`, outside it
both say `run_command`.
"""
from __future__ import annotations

from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any

import pytest

from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace
from agentos.providers.antigravity_cli import AntigravityCliAdapter
from agentos.providers.claude_cli.native_loop import NativeTurnOutcome, NativeTurnRequest, NativeTurnSink
from tests.unit.agentic.test_native_turn import (
    FakeNativeProvider,
    FakeStore,
    ScriptedHandle,
    _make_init,
    _make_result,
    _make_toolset,
)

_TOOL_USE_NAME = re.compile(r"\[Tool Use[^\]]*\bname=([A-Za-z0-9_.:-]+)\]")
_TOOL_RESULT_NAME = re.compile(r"\[Tool Result[^\]]*\bname=([A-Za-z0-9_.:-]+)")


def _history() -> list[dict[str, object]]:
    return [
        {"role": "user", "content": "Rode o ffprobe no vídeo final."},
        {
            "role": "assistant",
            "content": "Vou conferir o vídeo.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "run_command", "arguments": '{"command": "ffprobe out.mp4"}'},
                },
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "README.md"}'},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "run_command", "content": "duration=90.0"},
        {"role": "tool", "tool_call_id": "call_2", "name": "read_file", "content": "# readme"},
        {"role": "assistant", "content": "O vídeo tem 90 s."},
        {"role": "user", "content": "finalizou?"},
    ]


def _run(tmp_path: Path, *, code_mode: bool) -> tuple[str, set[str]]:
    store = FakeStore(model_id="gemini-3.8-flash-low")
    store.turn["provider"] = "google-antigravity"
    if not code_mode:
        store.turn["code_mode"] = None
        store.turn["conversation_mode"] = "agent"
    store.history_for_turn = lambda turn: _history()  # type: ignore[method-assign]
    captured: dict[str, Any] = {}

    def _round(req: NativeTurnRequest, sink: NativeTurnSink, h: ScriptedHandle) -> NativeTurnOutcome:
        captured["text"] = "\n".join(str(b.get("text") or "") for b in req.user_content)
        captured["announced"] = {spec.name for spec in req.executor.list_tools()}
        init = _make_init(req.model)
        sink.on_init(init)
        sink.on_text("OK")
        return NativeTurnOutcome(result=_make_result(text="OK"), init=init, final_text="OK", exit_code=0)

    provider = FakeNativeProvider(handle_factory=lambda req: ScriptedHandle(req, [_round]))
    provider.adapter = SimpleNamespace(  # type: ignore[attr-defined]
        name="antigravity", capabilities=AntigravityCliAdapter.capabilities
    )
    toolset = _make_toolset(
        ConversationWorkspace(tmp_path, "c1"),
        public_tool_names=code_mode,
        code_mode_active=code_mode,
        enable_terminal=True,
    )
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=toolset,
        limits=AgenticLimits(max_iterations=5, max_actions=5, context_window_tokens=1_000_000),
    )
    assert runtime.run("t1", turn=store.turn).state == "completed"
    return captured["text"], captured["announced"]


@pytest.mark.parametrize(("code_mode", "shell", "reader"), [(True, "Bash", "Read"), (False, "run_command", "read_file")])
def test_history_names_every_tool_as_the_agy_tool_list_announces_it(
    tmp_path: Path, code_mode: bool, shell: str, reader: str
) -> None:
    text, announced = _run(tmp_path, code_mode=code_mode)
    used = set(_TOOL_USE_NAME.findall(text)) | set(_TOOL_RESULT_NAME.findall(text))

    assert {shell, reader} <= used, text
    # agy calls an Orin tool as mcp_orin_<announced name>: every name the history uses must be one of them.
    assert used <= announced, (used - announced, sorted(announced))
    other = "run_command" if code_mode else "Bash"
    assert other not in used

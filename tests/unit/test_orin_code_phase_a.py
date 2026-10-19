from __future__ import annotations

from pathlib import Path

from agentos.agentic.agent_tools import (
    MAX_TOOL_RESULT_CHARS,
    AgentToolset,
    coerce_tool_arguments,
    compute_structured_patch,
)
from agentos.agentic.models import sanitize_public_mapping
from agentos.agentic.workspace import ConversationWorkspace
from agentos.code_mode.prompt import code_mode_instructions


def test_coerce_tool_arguments_maps_common_aliases(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "chat_coerce")
    tools = AgentToolset(workspace)

    # write_file with file_path + file_text
    w_out = tools.invoke("write_file", {"file_path": "hello.py", "file_text": "x = 1\ny = 2\n"})
    assert w_out.status == "succeeded"
    assert "[Note: " in w_out.content
    assert "parameter 'file_path'" not in w_out.content  # canonical field since W4: no correction note
    assert "parameter 'file_text' was read as 'content'" in w_out.content

    # read_file with file_path
    r_out = tools.invoke("read_file", {"file_path": "hello.py"})
    assert r_out.status == "succeeded"
    assert "x = 1" in r_out.content
    assert "parameter 'file_path'" not in r_out.content

    # edit_file with filepath + old_string + new_string
    e_out = tools.invoke(
        "edit_file",
        {"filepath": "hello.py", "old_string": "x = 1", "new_string": "x = 10"},
    )
    assert e_out.status == "succeeded"
    assert "parameter 'old_string'" not in e_out.content
    assert "parameter 'new_string'" not in e_out.content
    assert workspace.read_text("hello.py")[0] == "x = 10\ny = 2\n"

    # edit_file batch edits with old_string/new_string
    coerced, note = coerce_tool_arguments(
        "edit_file",
        {"file": "hello.py", "edits": [{"old_string": "y = 2", "new_string": "y = 20"}]},
    )
    assert coerced["path"] == "hello.py"
    assert coerced["edits"] == [{"old_text": "y = 2", "new_text": "y = 20"}]
    assert note is not None and "edits[].old_string" in note


def test_code_mode_edit_and_overwrite_require_prior_read_file(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "chat_guard")
    (workspace.root / "existing.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    tools = AgentToolset(workspace, code_mode_active=True)

    # Editing an unread existing file fails with FILE_NOT_READ
    edit_unread = tools.invoke(
        "edit_file",
        {"path": "existing.py", "old_text": "a = 1", "new_text": "a = 10"},
    )
    assert edit_unread.status == "failed"
    assert edit_unread.error_code == "FILE_NOT_READ"

    # Overwriting an unread existing file fails with FILE_NOT_READ
    write_unread = tools.invoke(
        "write_file",
        {"path": "existing.py", "content": "a = 99\n"},
    )
    assert write_unread.status == "failed"
    assert write_unread.error_code == "FILE_NOT_READ"

    # Creating a brand-new file succeeds without prior read_file
    create_new = tools.invoke(
        "write_file",
        {"path": "brand_new.py", "content": "new_var = True\n"},
    )
    assert create_new.status == "succeeded"

    # After reading existing.py, edit_file succeeds
    read_res = tools.invoke("read_file", {"path": "existing.py"})
    assert read_res.status == "succeeded"

    edit_after_read = tools.invoke(
        "edit_file",
        {"path": "existing.py", "old_text": "a = 1", "new_text": "a = 10"},
    )
    assert edit_after_read.status == "succeeded"


def test_code_mode_detects_stale_file_modified_externally_after_read(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "chat_stale")
    target = workspace.root / "module.py"
    target.write_text("value = 'initial'\n", encoding="utf-8")
    tools = AgentToolset(workspace, code_mode_active=True)

    assert tools.invoke("read_file", {"path": "module.py"}).status == "succeeded"

    # Simulate external modification on disk (by user or external tool)
    target.write_text("value = 'externally_changed'\n", encoding="utf-8")

    stale_edit = tools.invoke(
        "edit_file",
        {"path": "module.py", "old_text": "value = 'externally_changed'", "new_text": "value = 'agent'"},
    )
    assert stale_edit.status == "failed"
    assert stale_edit.error_code == "STALE_FILE_READ"

    # Re-reading refreshes the state and unblocks editing
    assert tools.invoke("read_file", {"path": "module.py"}).status == "succeeded"
    fresh_edit = tools.invoke(
        "edit_file",
        {"path": "module.py", "old_text": "value = 'externally_changed'", "new_text": "value = 'agent'"},
    )
    assert fresh_edit.status == "succeeded"


def test_multiple_consecutive_edits_succeed_after_single_read(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "chat_multi_edit")
    workspace.write_text("calc.py", "a = 1\nb = 2\nc = 3\n")
    tools = AgentToolset(workspace, code_mode_active=True)

    assert tools.invoke("read_file", {"path": "calc.py"}).status == "succeeded"

    first = tools.invoke("edit_file", {"path": "calc.py", "old_text": "a = 1", "new_text": "a = 10"})
    assert first.status == "succeeded"

    second = tools.invoke("edit_file", {"path": "calc.py", "old_text": "b = 2", "new_text": "b = 20"})
    assert second.status == "succeeded"
    assert workspace.read_text("calc.py")[0] == "a = 10\nb = 20\nc = 3\n"


def test_compute_structured_patch_and_public_payload_sanitization(tmp_path: Path) -> None:
    hunks, added, removed = compute_structured_patch(
        "line1\nline2\nline3\n",
        "line1\nline2_modified\nline2_extra\nline3\n",
    )
    assert added == 2
    assert removed == 1
    assert len(hunks) == 1
    assert hunks[0]["oldStart"] == 1
    assert hunks[0]["newStart"] == 1
    assert "-line2" in hunks[0]["lines"]
    assert "+line2_modified" in hunks[0]["lines"]
    assert "+line2_extra" in hunks[0]["lines"]

    workspace = ConversationWorkspace(tmp_path, "chat_patch")
    tools = AgentToolset(workspace, code_mode_active=True)

    w_out = tools.invoke("write_file", {"path": "service.py", "content": "def run():\n    return 1\n"})
    assert w_out.status == "succeeded"
    assert w_out.payload["added_lines"] == 2
    assert w_out.payload["removed_lines"] == 0
    assert w_out.payload["original_sha256"] == ""
    assert len(w_out.payload["updated_sha256"]) == 64

    e_out = tools.invoke(
        "edit_file",
        {"path": "service.py", "old_text": "return 1", "new_text": "value = 42\n    return value"},
    )
    assert e_out.status == "succeeded"
    assert e_out.payload["added_lines"] == 2
    assert e_out.payload["removed_lines"] == 1
    assert e_out.payload["original_sha256"] == w_out.payload["updated_sha256"]
    assert e_out.payload["updated_sha256"] != e_out.payload["original_sha256"]
    assert len(e_out.payload["structured_patch"]) == 1

    # Public mapping sanitization must preserve structured_patch and line counts
    sanitized = sanitize_public_mapping(e_out.payload)
    assert sanitized["added_lines"] == 2
    assert sanitized["removed_lines"] == 1
    assert len(sanitized["structured_patch"]) == 1


def test_truncated_tool_output_persists_full_output_to_disk(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "chat_trunc_persist")
    tools = AgentToolset(workspace)
    large_text = "\n".join(f"row-{i:04d}-" + ("x" * 60) for i in range(300)) + "\n"
    tools.invoke("write_file", {"path": "large.txt", "content": large_text})

    outcome = tools.invoke("read_file", {"path": "large.txt", "limit": 800})
    assert outcome.status == "succeeded"
    assert outcome.payload["truncated"] is True
    assert len(outcome.content) <= MAX_TOOL_RESULT_CHARS
    full_path = outcome.payload.get("full_output_path")
    assert isinstance(full_path, str) and full_path.startswith(".orin/tool-outputs/")
    assert full_path in outcome.content
    saved_text = workspace.resolve(full_path).read_text(encoding="utf-8")
    assert "row-0299-" in saved_text


def test_modular_code_mode_prompt_includes_claude_code_sections() -> None:
    prompt = code_mode_instructions(
        work_kind="implementation",
        autonomy="code_autonomy",
        plan_path=".orin/plans/test.md",
    )
    assert "Reversibilidade e Blast Radius" in prompt
    assert "Ferramentas Dedicadas em vez de Shell" in prompt
    assert "Execução Paralela de Ferramentas" in prompt
    assert "Entrega Completa e Citações de Código" in prompt
    assert "caminho/arquivo.ext:linha" in prompt
    assert "produção" in prompt and "sempre exige" in prompt


def test_code_completion_gate_rejects_zero_tool_implementation_turn(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "chat_zero_tool_gate")
    tools = AgentToolset(
        workspace,
        code_mode_active=True,
        code_mode_requires_approval=False,
        code_mode_work_kind="implementation",
    )

    ok, msg = tools.code_completion_gate()
    assert ok is False
    assert msg is not None and "ainda não executou nenhuma ferramenta" in msg

    # After an exploratory read-only tool call, if no files were modified (e.g. answering after inspection), gate allows
    res = tools.invoke("list_files", {})
    assert res.status == "succeeded"
    ok_after_read, msg_after_read = tools.code_completion_gate()
    assert ok_after_read is True
    assert msg_after_read is None


def test_runtime_recovers_from_thinking_only_turn_and_filters_code_mode_schemas(tmp_path: Path) -> None:
    from typing import Any, Mapping
    from agentos.agentic.provider_stream import NormalizedStreamItem, StreamKind
    from agentos.agentic.runtime import AgenticTurnRuntime
    from agentos.providers.models import FinishReason

    class _ThinkingThenToolThenTextProvider:
        def __init__(self) -> None:
            self.calls = 0

        def stream(self, request: Mapping[str, Any]):
            self.calls += 1
            if self.calls == 1:
                # 1st iteration: model emits only thinking tokens and stops with empty content and zero tool calls
                return iter(
                    [
                        NormalizedStreamItem(StreamKind.TEXT, 1, text="", thinking="Analisando o pedido..."),
                        NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason=FinishReason.STOP),
                    ]
                )
            if self.calls == 2:
                # 2nd iteration: after runtime nudge, model creates the file
                return iter(
                    [
                        NormalizedStreamItem(
                            StreamKind.TOOL_CALL,
                            1,
                            tool_call_id="call_write_1",
                            tool_name="write_file",
                            arguments_delta='{"path":"hello.py","content":"def greet():\\n    return \'ok\'\\n"}',
                        ),
                        NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason=FinishReason.TOOL_CALLS),
                    ]
                )
            if self.calls == 3:
                # 3rd iteration: model runs verification
                return iter(
                    [
                        NormalizedStreamItem(
                            StreamKind.TOOL_CALL,
                            1,
                            tool_call_id="call_verify_1",
                            tool_name="verify_project",
                            arguments_delta='{"steps":["test"]}',
                        ),
                        NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason=FinishReason.TOOL_CALLS),
                    ]
                )
            # 4th iteration: model delivers final text response
            return iter(
                [
                    NormalizedStreamItem(StreamKind.TEXT, 1, text="Módulo criado em `hello.py:1`."),
                    NormalizedStreamItem(StreamKind.FINISH, 2, finish_reason=FinishReason.STOP),
                ]
            )

    class _MinimalStore:
        def __init__(self) -> None:
            self.deltas: list[str] = []

        def history_for_turn(self, turn: Mapping[str, Any]) -> list[dict[str, Any]]:
            return [{"role": "user", "content": "cria hello.py"}]

        def lifecycle(self, turn: Mapping[str, Any], state: str, **payload: Any) -> None:
            pass

        def effect_started(self, turn: Mapping[str, Any], **kwargs: Any) -> str:
            return "eff-1"

        def effect_finished(self, turn: Mapping[str, Any], effect_id: str, **kwargs: Any) -> None:
            pass

        def delta(self, turn: Mapping[str, Any], text: str) -> None:
            self.deltas.append(text)

        def finish(self, turn: Mapping[str, Any], *, code: str | None = None) -> None:
            pass

    workspace = ConversationWorkspace(tmp_path / "workspaces", "chat_thinking_only")
    workspace.write_text("package.json", '{"name":"demo","scripts":{"test":"python -c \\"import hello\\""}}\n')
    tools = AgentToolset(
        workspace,
        code_mode_active=True,
        code_mode_requires_approval=False,
        code_mode_work_kind="implementation",
    )
    store = _MinimalStore()
    provider = _ThinkingThenToolThenTextProvider()
    runtime = AgenticTurnRuntime(
        store=store,
        provider=provider,
        toolset=tools,
        phase_controller=None,
    )

    turn = {
        "turn_id": "turn-thinking-recovery",
        "code_mode": "code",
        "assistant_message_id": "msg-thinking-1",
    }
    schema_names = {s["function"]["name"] for s in runtime._tool_schemas(turn)}
    assert "write_contract" not in schema_names
    assert "report_verification" not in schema_names
    assert {"list_files", "read_file", "write_file", "edit_file", "run_command", "verify_project"}.issubset(
        schema_names
    )

    result = runtime.run("turn-thinking-recovery", turn=turn)
    assert result.state == "completed"
    assert "".join(store.deltas) == "Módulo criado em `hello.py:1`."
    assert "def greet():" in workspace.resolve("hello.py").read_text(encoding="utf-8")



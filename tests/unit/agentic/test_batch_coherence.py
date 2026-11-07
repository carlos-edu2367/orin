import json
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset, ToolOutcome
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace

TURN = {"turn_id": "turn-1", "conversation_id": "conversation-1", "user_id": "user-1", "provider": "openrouter"}


def _call(index: int, name: str, arguments: dict) -> dict[str, str]:
    return {"id": f"c{index}", "name": name, "arguments": json.dumps(arguments)}


class _CountingToolset:
    READ_ONLY = {"search_files", "read_process_output"}

    def __init__(self) -> None:
        self.invocations: list[str] = []

    def is_read_only(self, name): return name in self.READ_ONLY
    def argument_names(self, name): return None

    def invoke(self, name, arguments):
        self.invocations.append(name)
        return ToolOutcome("succeeded", "ok", f"result {len(self.invocations)}", {})


def test_identical_calls_in_one_batch_run_once_and_share_the_result() -> None:
    toolset = _CountingToolset()
    runtime = AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset)
    results = runtime._run_toolset(TURN, [
        _call(1, "search_files", {"pattern": "x"}), _call(2, "search_files", {"pattern": "x"}),
        _call(3, "run_command", {"command": "pytest"}), _call(4, "run_command", {"command": "pytest"}),
    ])
    assert toolset.invocations == ["search_files", "run_command"]
    assert [item["id"] for item in results] == ["c1", "c2", "c3", "c4"]
    assert results[1]["content"].startswith(results[0]["content"])
    assert results[3]["content"].startswith(results[2]["content"])
    assert runtime.counters.redundant_tool_calls == 2


def test_polling_calls_are_never_merged() -> None:
    toolset = _CountingToolset()
    runtime = AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset)
    runtime._run_toolset(TURN, [_call(1, "read_process_output", {"pid": 1}), _call(2, "read_process_output", {"pid": 1})])
    assert toolset.invocations == ["read_process_output", "read_process_output"]


def _real(tmp_path: Path) -> tuple[AgentToolset, AgenticTurnRuntime]:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, permission_mode="full_autonomy")
    (toolset.workspace.root / "a.txt").write_bytes(b"one\n")
    return toolset, AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset)


def test_an_edit_of_a_file_read_only_in_the_same_batch_is_refused(tmp_path: Path) -> None:
    toolset, runtime = _real(tmp_path)
    edit = {"path": "a.txt", "old_text": "one", "new_text": "two"}
    results = runtime._run_toolset(TURN, [_call(1, "read_file", {"path": "a.txt"}), _call(2, "edit_file", edit)])
    assert results[1]["status"] == "failed"
    assert results[1]["error_code"] == "READ_IN_SAME_BATCH"
    assert results[1]["content"] == (
        "You read this file in the same batch; look at the read result before editing it. "
        "Wait for the read result, then edit in your next tool call. "
        "Do not re-read the file and do not write it through the shell."
    )
    assert (toolset.workspace.root / "a.txt").read_bytes() == b"one\n"
    again = runtime._run_toolset(TURN, [_call(3, "edit_file", edit)])
    assert again[0]["status"] == "succeeded"  # the refusal is not remembered as a failed signature


def test_an_edit_of_a_file_read_in_an_earlier_batch_runs(tmp_path: Path) -> None:
    toolset, runtime = _real(tmp_path)
    runtime._run_toolset(TURN, [_call(1, "read_file", {"path": "a.txt"})])
    results = runtime._run_toolset(TURN, [
        _call(2, "read_file", {"path": "a.txt"}),
        _call(3, "edit_file", {"path": "a.txt", "old_text": "one", "new_text": "two"}),
    ])
    assert results[1]["status"] == "succeeded"


def test_identical_reads_around_an_edit_both_run(tmp_path: Path) -> None:
    toolset, runtime = _real(tmp_path)
    runtime._run_toolset(TURN, [_call(1, "read_file", {"path": "a.txt"})])
    read = {"path": "a.txt"}
    results = runtime._run_toolset(TURN, [
        _call(2, "read_file", read),
        _call(3, "edit_file", {"path": "a.txt", "old_text": "one", "new_text": "two"}),
        _call(4, "read_file", read),
    ])
    assert "two" in results[2]["content"]
    assert "[identical to an earlier call" not in results[2]["content"]


def test_identical_commands_around_an_edit_both_run() -> None:
    class Toolset(_CountingToolset):
        pass

    toolset = Toolset()
    runtime = AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset)
    runtime._run_toolset(TURN, [
        _call(1, "run_command", {"command": "pytest"}),
        _call(2, "edit_file", {"path": "x"}),
        _call(3, "run_command", {"command": "pytest"}),
    ])
    assert toolset.invocations == ["run_command", "edit_file", "run_command"]


def test_a_failed_call_is_counted_once_per_real_execution() -> None:
    class Failing(_CountingToolset):
        def invoke(self, name, arguments):
            self.invocations.append(name)
            return ToolOutcome("failed", "no", "boom", {})

    toolset = Failing()
    runtime = AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset)
    runtime._run_toolset(TURN, [_call(1, "run_command", {"command": "x"}), _call(2, "run_command", {"command": "x"})])
    assert toolset.invocations == ["run_command"]
    assert list(runtime._consecutive_tool_failures.values()) == [1]


def test_a_read_of_a_missing_file_does_not_block_a_write_in_the_same_batch(tmp_path: Path) -> None:
    toolset, runtime = _real(tmp_path)
    results = runtime._run_toolset(TURN, [
        _call(1, "read_file", {"path": "new.txt"}),
        _call(2, "write_file", {"path": "new.txt", "content": "hi"}),
    ])
    assert results[1]["error_code"] != "READ_IN_SAME_BATCH"
    assert results[1]["status"] == "succeeded"

from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.runtime import AgenticTurnRuntime
from agentos.agentic.workspace import ConversationWorkspace


class _ForgetfulToolset:
    def __init__(self) -> None:
        self.forgotten: list[str] = []

    def forget_read_content(self, path: str) -> bool:
        self.forgotten.append(path)
        return True


def _runtime(toolset) -> AgenticTurnRuntime:
    return AgenticTurnRuntime(store=object(), provider=object(), toolset=toolset)


def test_a_forgotten_read_comes_back_with_content_and_still_allows_the_edit(tmp_path: Path) -> None:
    toolset = AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, permission_mode="full_autonomy")
    (toolset.workspace.root / "a.txt").write_bytes(b"one\n")
    toolset.invoke("read_file", {"path": "a.txt"})
    assert toolset.forget_read_content("a.txt") is True
    assert toolset.invoke("read_file", {"path": "a.txt"}).data["tool_output"]["type"] == "text"
    assert toolset.invoke("edit_file", {"path": "a.txt", "old_text": "one", "new_text": "two"}).status == "succeeded"


def test_tool_result_messages_carry_the_read_path() -> None:
    messages = AgenticTurnRuntime._tool_result_messages(
        {"provider": "openrouter"}, {"id": "c1", "name": "read_file", "content": "x", "read_path": "a.txt"},
    )
    assert messages[0]["_read_path"] == "a.txt"


def test_aging_a_read_result_releases_its_stub_once() -> None:
    toolset = _ForgetfulToolset()
    runtime = _runtime(toolset)
    messages = [
        {"role": "tool", "tool_call_id": "a", "content": "x" * 5000, "_read_path": "a.txt"},
        {"role": "tool", "tool_call_id": "b", "content": "y" * 5000, "_read_path": "b.txt"},
    ]
    AgenticTurnRuntime._age_tool_results(messages, keep_recent=1)
    assert runtime._release_read_stubs(messages) == 1
    assert toolset.forgotten == ["a.txt"]
    assert runtime._release_read_stubs(messages) == 0


def test_a_short_read_that_aging_leaves_intact_keeps_its_stub() -> None:
    toolset = _ForgetfulToolset()
    messages = [{"role": "tool", "tool_call_id": "a", "content": "x" * 100, "_read_path": "a.txt"}]
    AgenticTurnRuntime._age_tool_results(messages, keep_recent=0)
    assert _runtime(toolset)._release_read_stubs(messages) == 0


def test_microcompacting_a_read_result_releases_its_stub() -> None:
    toolset = _ForgetfulToolset()
    runtime = _runtime(toolset)
    call = lambda cid: {"role": "assistant", "content": None, "tool_calls": [{"id": cid, "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]}
    messages = [
        {"role": "user", "content": "task"},
        call("c0"), {"role": "tool", "tool_call_id": "c0", "content": "r" * 500, "_read_path": "a.txt"},
        call("c1"), {"role": "tool", "tool_call_id": "c1", "content": "s" * 500, "_read_path": "b.txt"},
    ]
    runtime._microcompact(messages, keep_recent=0)
    assert runtime._release_read_stubs(messages) == 1
    assert toolset.forgotten == ["a.txt"]


def test_folded_reads_are_released_even_without_shrinking() -> None:
    toolset = _ForgetfulToolset()
    folded = [{"role": "tool", "tool_call_id": "a", "content": "x", "_read_path": "a.txt"}]
    assert _runtime(toolset)._release_read_stubs(folded, folded=True) == 1

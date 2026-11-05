from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.workspace import ConversationWorkspace


def _toolset(tmp_path: Path) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "c1"), code_mode_active=True, permission_mode="full_autonomy")


def _seed(toolset: AgentToolset, name: str, data: bytes) -> Path:
    target = toolset.workspace.root / name
    target.write_bytes(data)
    assert toolset.invoke("read_file", {"path": name}).status == "succeeded"
    return target


def test_edit_finds_lf_old_text_in_a_crlf_file_and_keeps_crlf(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    target = _seed(toolset, "a.txt", b"one\r\ntwo\r\nthree\r\n")
    outcome = toolset.invoke("edit_file", {"path": "a.txt", "old_text": "one\ntwo", "new_text": "uno\ndos\ntres"})
    assert outcome.status == "succeeded", outcome.content
    assert target.read_bytes() == b"uno\r\ndos\r\ntres\r\nthree\r\n"


def test_a_single_line_old_text_with_a_multiline_replacement_uses_crlf(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    target = _seed(toolset, "a.txt", b"a\r\nb\r\n")
    assert toolset.invoke("edit_file", {"path": "a.txt", "old_text": "a", "new_text": "x\ny"}).status == "succeeded"
    assert target.read_bytes() == b"x\r\ny\r\nb\r\n"


def test_edit_of_an_lf_region_in_a_mixed_file_touches_only_that_region(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    target = _seed(toolset, "a.txt", b"a\r\nb\r\nc\nd\n")
    assert toolset.invoke("edit_file", {"path": "a.txt", "old_text": "c\nd", "new_text": "x\ny"}).status == "succeeded"
    assert target.read_bytes() == b"a\r\nb\r\nx\ny\n"


def test_edit_of_an_lf_file_is_unchanged_behaviour(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    target = _seed(toolset, "a.txt", b"a\nb\n")
    assert toolset.invoke("edit_file", {"path": "a.txt", "old_text": "a\nb", "new_text": "c\nd"}).status == "succeeded"
    assert target.read_bytes() == b"c\nd\n"


def test_write_file_over_a_crlf_file_keeps_crlf(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    target = _seed(toolset, "a.txt", b"old\r\nfile\r\n")
    assert toolset.invoke("write_file", {"path": "a.txt", "content": "new\ncontent\n"}).status == "succeeded"
    assert target.read_bytes() == b"new\r\ncontent\r\n"


def test_write_file_of_a_new_file_keeps_what_the_model_sent(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path)
    assert toolset.invoke("write_file", {"path": "n.txt", "content": "a\nb\n"}).status == "succeeded"
    assert (toolset.workspace.root / "n.txt").read_bytes() == b"a\nb\n"

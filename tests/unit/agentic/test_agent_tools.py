from __future__ import annotations

from pathlib import Path
import subprocess

import httpx
import pytest

from agentos.agentic import agent_tools
from agentos.agentic.agent_tools import AgentToolError, AgentToolset, parse_arguments
from agentos.agentic.workspace import ConversationWorkspace, WorkspaceError


@pytest.fixture()
def toolset(tmp_path: Path) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "chat_abc"))


def test_write_then_read_returns_the_content_to_the_model(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "notes/plan.md", "content": "step one"})
    outcome = toolset.invoke("read_file", {"path": "notes/plan.md"})

    assert outcome.status == "succeeded"
    assert "step one" in outcome.content


def test_web_search_is_absent_without_a_configured_client(toolset: AgentToolset) -> None:
    assert "web_search" not in [item.name for item in toolset.definitions()]


def test_web_search_returns_titles_and_urls_to_the_model(tmp_path) -> None:
    from agentos.agentic.web_search import SearchResult

    class Searcher:
        def search(self, query, *, limit=5):
            return [SearchResult("Orin docs", "https://example.test/a", "how it works")]

    tools = AgentToolset(ConversationWorkspace(tmp_path, "chat_search"), search_client=Searcher())

    outcome = tools.invoke("web_search", {"query": "orin"})

    assert outcome.status == "succeeded"
    assert "https://example.test/a" in outcome.content
    assert outcome.payload["count"] == 1
    assert tools.is_read_only("web_search")


def test_web_search_redacts_the_configured_key_from_the_activity_facing_outcome(tmp_path) -> None:
    from agentos.agentic.web_search import BraveSearchClient

    secret = "search-secret-123"
    payload = {"web": {"results": [{
        "title": f"Title {secret}",
        "url": f"https://example.test/{secret}",
        "description": f"Description {secret}",
    }]}}
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    tools = AgentToolset(
        ConversationWorkspace(tmp_path, "chat_search_secret"),
        search_client=BraveSearchClient(secret, client),
    )

    outcome = tools.invoke("web_search", {"query": f"find {secret}"})

    assert secret not in outcome.summary
    assert secret not in outcome.content
    assert secret not in repr(outcome.payload)


def test_web_search_translates_provider_errors_without_leaking_the_key(tmp_path) -> None:
    from agentos.agentic.web_search import BraveSearchClient

    secret = "search-secret-456"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": secret})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tools = AgentToolset(
        ConversationWorkspace(tmp_path, "chat_search_error"),
        search_client=BraveSearchClient(secret, client),
    )

    outcome = tools.invoke("web_search", {"query": f"find {secret}"})

    assert outcome.status == "failed"
    assert secret not in outcome.summary
    assert secret not in outcome.content
    assert secret not in repr(outcome.payload)


def test_web_search_no_results_redacts_the_query_from_summary_and_label(tmp_path) -> None:
    from agentos.agentic.web_search import BraveSearchClient

    secret = "search-secret-789"
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"web": {"results": []}})))
    tools = AgentToolset(
        ConversationWorkspace(tmp_path, "chat_search_empty"),
        search_client=BraveSearchClient(secret, client),
    )

    outcome = tools.invoke("web_search", {"query": f"find {secret}"})

    assert outcome.status == "succeeded"
    assert outcome.content == "[no results]"
    assert secret not in outcome.summary
    assert secret not in repr(outcome.payload)


def test_edit_file_replaces_a_unique_text_fragment_without_rewriting_the_document(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "notes/plan.md", "content": "first\nsecond\nthird\n"})

    outcome = toolset.invoke("edit_file", {"path": "notes/plan.md", "old_text": "second", "new_text": "updated"})

    assert outcome.status == "succeeded"
    content = toolset.invoke("read_file", {"path": "notes/plan.md"}).content
    assert "     1\tfirst" in content
    assert "     2\tupdated" in content
    assert "     3\tthird" in content


def test_edit_file_refuses_ambiguous_fragments(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "notes/plan.md", "content": "same\nsame\n"})

    outcome = toolset.invoke("edit_file", {"path": "notes/plan.md", "old_text": "same", "new_text": "updated"})

    assert outcome.status == "failed"
    assert outcome.error_code == "TOOL_REFUSED"


def test_edit_file_applies_a_batch_of_edits_in_one_call(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "app.py", "content": "alpha\nbeta\ngamma\n"})

    outcome = toolset.invoke("edit_file", {"path": "app.py", "edits": [
        {"old_text": "alpha", "new_text": "one"},
        {"old_text": "gamma", "new_text": "three"},
    ]})

    assert outcome.status == "succeeded"
    assert outcome.payload["edits_applied"] == 2
    assert "     1\tone" in toolset.invoke("read_file", {"path": "app.py"}).content
    assert "     3\tthree" in toolset.invoke("read_file", {"path": "app.py"}).content


def test_edit_file_writes_nothing_when_one_edit_in_the_batch_fails(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "app.py", "content": "alpha\nbeta\n"})

    outcome = toolset.invoke("edit_file", {"path": "app.py", "edits": [
        {"old_text": "alpha", "new_text": "one"},
        {"old_text": "missing", "new_text": "x"},
    ]})

    assert outcome.status == "failed"
    assert "alpha" in toolset.invoke("read_file", {"path": "app.py"}).content


def test_edit_file_can_replace_every_occurrence_when_asked(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "app.py", "content": "same\nsame\n"})

    outcome = toolset.invoke("edit_file", {"path": "app.py", "old_text": "same", "new_text": "done", "replace_all": True})

    assert outcome.status == "succeeded"
    assert "same" not in toolset.invoke("read_file", {"path": "app.py"}).content


def test_edit_file_refuses_mixing_the_single_and_batch_forms(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "app.py", "content": "alpha\n"})

    outcome = toolset.invoke("edit_file", {"path": "app.py", "old_text": "alpha", "new_text": "one", "edits": [{"old_text": "alpha", "new_text": "two"}]})

    assert outcome.status == "failed"
    assert outcome.error_code == "TOOL_REFUSED"


def test_paths_cannot_escape_the_conversation_workspace(toolset: AgentToolset) -> None:
    outcome = toolset.invoke("write_file", {"path": "../escape.txt", "content": "x"})

    assert outcome.status == "failed"
    assert not (toolset.workspace.root.parent / "escape.txt").exists()


def test_a_symlink_out_of_the_workspace_is_rejected(tmp_path: Path) -> None:
    workspace = ConversationWorkspace(tmp_path, "chat_link")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        (workspace.root / "link.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this host")

    with pytest.raises(WorkspaceError):
        workspace.read_text("link.txt")


def test_failed_tools_report_the_reason_back_to_the_model(toolset: AgentToolset) -> None:
    outcome = toolset.invoke("read_file", {"path": "missing.md"})

    assert outcome.status == "failed"
    assert "missing.md" in outcome.content


def test_read_file_numbers_lines_and_paginates(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "big.txt", "content": "\n".join(f"line {index}" for index in range(1, 51)) + "\n"})

    outcome = toolset.invoke("read_file", {"path": "big.txt", "offset": 3, "limit": 2})

    assert outcome.status == "succeeded"
    assert "     3\tline 3" in outcome.content
    assert "line 5" not in outcome.content
    assert outcome.payload["total_lines"] == 50


def test_read_file_tells_the_model_how_to_read_the_rest(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "big.txt", "content": "\n".join(f"line {index}" for index in range(1, 51)) + "\n"})

    outcome = toolset.invoke("read_file", {"path": "big.txt", "offset": 1, "limit": 2})

    assert "offset=3" in outcome.content


def test_list_files_reports_workspace_entries(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "a.txt", "content": "1"})
    toolset.invoke("write_file", {"path": "sub/b.txt", "content": "2"})

    outcome = toolset.invoke("list_files", {})

    assert "a.txt" in outcome.content
    assert "sub" in outcome.content


def test_search_files_returns_matches_the_model_can_read(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "src/app.py", "content": "import os\nDEBUG = True\n"})

    outcome = toolset.invoke("search_files", {"pattern": "DEBUG"})

    assert outcome.status == "succeeded"
    assert "src/app.py:2" in outcome.content
    assert outcome.payload["count"] == 1


def test_search_files_reports_no_match_without_failing(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "src/app.py", "content": "nothing\n"})

    outcome = toolset.invoke("search_files", {"pattern": "DEBUG"})

    assert outcome.status == "succeeded"
    assert outcome.payload["count"] == 0


def test_run_command_returns_output_and_exit_code(toolset: AgentToolset) -> None:
    outcome = toolset.invoke("run_command", {"command": "echo agentos"})

    assert outcome.status == "succeeded"
    assert "agentos" in outcome.content
    assert "exit=0" in outcome.content


def test_run_command_can_start_a_long_lived_server_without_waiting(toolset: AgentToolset, monkeypatch: pytest.MonkeyPatch) -> None:
    class BackgroundProcess:
        pid = 2468

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            raise AssertionError("a background process must not be awaited")

    process = BackgroundProcess()
    captured: dict[str, object] = {}
    monkeypatch.setattr(agent_tools.subprocess, "Popen", lambda *args, **kwargs: captured.update(kwargs) or process)

    outcome = toolset.invoke("run_command", {"command": "python server.py", "background": True})

    assert outcome.status == "succeeded"
    assert outcome.payload["background"] is True
    assert outcome.payload["pid"] == 2468
    assert captured["stdout"] is subprocess.DEVNULL


def test_run_command_reports_workspace_files_it_created(toolset: AgentToolset) -> None:
    outcome = toolset.invoke("run_command", {"command": "python -c \"open('report.pdf', 'wb').write(b'%PDF')\""})

    assert outcome.status == "succeeded"
    assert outcome.payload["artifacts"] == [{"path": "report.pdf", "size_bytes": 4}]


def test_run_command_refuses_a_host_destroying_command(toolset: AgentToolset) -> None:
    outcome = toolset.invoke("run_command", {"command": "shutdown /s /t 0"})

    assert outcome.status == "failed"
    assert outcome.error_code == "TOOL_REFUSED"


def test_run_command_terminates_its_process_tree_when_it_times_out(toolset: AgentToolset, monkeypatch: pytest.MonkeyPatch) -> None:
    class TimedOutProcess:
        pid = 2468
        returncode: int | None = None

        def __init__(self) -> None:
            self.timeouts: list[float | None] = []

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            self.timeouts.append(timeout)
            if len(self.timeouts) == 1:
                raise subprocess.TimeoutExpired("slow command", timeout, output=b"partial", stderr=b"still running")
            self.returncode = -9
            return b"partial", b"still running"

    process = TimedOutProcess()
    terminated: list[int] = []
    monkeypatch.setattr(agent_tools.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(agent_tools, "_terminate_process_tree", lambda item: terminated.append(item.pid))

    outcome = toolset.invoke("run_command", {"command": "slow command"})

    assert outcome.status == "failed"
    assert outcome.error_code == "TOOL_REFUSED"
    assert outcome.content == "Command timed out after 45s."
    assert process.timeouts == [45, None]
    assert terminated == [2468]


def test_fetch_url_refuses_private_addresses(toolset: AgentToolset) -> None:
    for target in ("http://127.0.0.1:8000/v1/providers", "http://localhost/admin", "http://192.168.0.10/"):
        outcome = toolset.invoke("fetch_url", {"url": target})
        assert outcome.status == "failed", target


def test_fetch_url_returns_readable_text(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html="<html><head><title>Doc</title></head><body><script>x=1</script><p>Hello world</p></body></html>")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tools = AgentToolset(ConversationWorkspace(tmp_path, "chat_fetch"), http_client=client)

    outcome = tools.invoke("fetch_url", {"url": "https://example.com/doc"})

    assert outcome.status == "succeeded"
    assert "Hello world" in outcome.content
    assert "x=1" not in outcome.content


def test_unknown_tool_is_reported_not_raised(toolset: AgentToolset) -> None:
    outcome = toolset.invoke("launch_missiles", {})

    assert outcome.status == "failed"
    assert "Unknown tool" in outcome.content


def test_invalid_arguments_are_reported_to_the_model(toolset: AgentToolset) -> None:
    outcome = toolset.invoke("write_file", {"path": "a.txt"})

    assert outcome.status == "failed"
    assert outcome.error_code == "INVALID_ARGUMENTS"


def test_parse_arguments_rejects_non_objects() -> None:
    with pytest.raises(AgentToolError):
        parse_arguments("[1, 2]")


def test_schemas_are_provider_ready(toolset: AgentToolset) -> None:
    schemas = toolset.schemas()

    assert {item["function"]["name"] for item in schemas} >= {"read_file", "write_file", "edit_file", "list_files", "run_command", "fetch_url"}
    for item in schemas:
        assert item["function"]["parameters"]["type"] == "object"
        assert item["function"]["description"]


def test_truncated_output_tells_the_model_how_to_narrow_it(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "huge.txt", "content": "x" * 20_000 + "\n"})

    outcome = toolset.invoke("read_file", {"path": "huge.txt"})

    assert outcome.payload["truncated"] is True
    assert "narrow" in outcome.content.lower() or "offset" in outcome.content.lower()


def test_definitions_are_built_once_per_toolset(toolset: AgentToolset) -> None:
    assert toolset.definitions() is toolset.definitions()


def test_truncated_output_content_fits_within_the_result_budget(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "huge.txt", "content": "x" * 20_000 + "\n"})

    outcome = toolset.invoke("read_file", {"path": "huge.txt"})

    assert outcome.payload["truncated"] is True
    assert len(outcome.content) <= agent_tools.MAX_TOOL_RESULT_CHARS


def test_truncated_output_keeps_the_full_instructive_message(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "huge.txt", "content": "x" * 20_000 + "\n"})

    outcome = toolset.invoke("read_file", {"path": "huge.txt"})

    assert "narrow the request instead of repeating it" in outcome.content


def test_read_tools_are_declared_read_only_and_write_tools_are_not(toolset: AgentToolset) -> None:
    assert toolset.is_read_only("read_file")
    assert toolset.is_read_only("list_files")
    assert not toolset.is_read_only("write_file")
    assert not toolset.is_read_only("run_command")
    assert not toolset.is_read_only("unknown_tool")

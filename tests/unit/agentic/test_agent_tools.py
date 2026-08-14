from __future__ import annotations

from pathlib import Path
import subprocess
import time
import base64

import httpx
import pytest

from agentos.agentic import agent_tools
from agentos.agentic.agent_tools import AgentToolError, AgentToolset, ToolOutcome, parse_arguments
from agentos.agentic.workspace import ConversationWorkspace, WorkspaceError
from agentos.skills.service import SkillLibraryService


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


def test_ask_user_accepts_a_mixed_batch_and_refuses_invalid_choices(toolset: AgentToolset) -> None:
    outcome = toolset.invoke("ask_user", {"questions": [
        {"id": "features", "question": "Quais recursos?", "mode": "checkbox", "options": [{"id": "search", "label": "Busca"}]},
        {"id": "tone", "question": "Qual tom?", "mode": "single_choice", "options": [{"id": "formal", "label": "Formal"}, {"id": "casual", "label": "Casual"}]},
        {"id": "notes", "question": "Algo mais?", "mode": "text"},
    ]})

    assert outcome.status == "succeeded"
    assert outcome.payload["wait_for_user"] is True
    assert [item["mode"] for item in outcome.payload["questions"]] == ["checkbox", "single_choice", "text"]
    assert toolset.invoke("ask_user", {"questions": [{"id": "bad", "question": "Escolha", "mode": "single_choice", "options": [{"id": "only", "label": "Apenas uma"}]}]}).status == "failed"


def test_ask_user_accepts_the_documented_maximum_batch_with_options(toolset: AgentToolset) -> None:
    questions = [
        {
            "id": f"choice_{index}",
            "question": f"Qual opcao voce prefere {index}?",
            "mode": "checkbox",
            "options": [{"id": f"option_{option}", "label": f"Opcao {option}"} for option in range(12)],
        }
        for index in range(8)
    ]

    outcome = toolset.invoke("ask_user", {"questions": questions})

    assert outcome.status == "succeeded"
    assert len(outcome.payload["questions"]) == 8
    assert all(len(item["options"]) == 12 for item in outcome.payload["questions"])


def test_agent_can_publish_and_immediately_use_a_versioned_user_skill(tmp_path: Path) -> None:
    library = SkillLibraryService(builtins=())
    tools = AgentToolset(
        ConversationWorkspace(tmp_path, "chat_skills"), skills=library._registry("owner-1"),
        skill_library=library, skill_user_id="owner-1", skill_agent_id="agent-1",
    )

    outcome = tools.invoke("create_skill", {
        "name": "Endpoint Recovery", "description": "Recover a failing HTTP endpoint using a repeatable diagnosis.",
        "instructions": "## Workflow\n\n1. Reproduce the failure.\n\n## Validation\n\nRun the focused check.",
        "tags": ["http", "debugging"], "capabilities": ["diagnose_endpoint"],
        "when_to_use": ["an endpoint fails after a change"],
        "when_not_to_use": ["a request is only for style review"],
        "requires_tools": ["read_file"], "user_id": "attacker-should-not-control-this",
    })

    assert outcome.status == "succeeded"
    assert outcome.payload["skill_id"] == "endpoint-recovery"
    detail = library.get({"user_id": "owner-1", "skill_id": "endpoint-recovery"})
    assert detail["instructions"].startswith("## Workflow")
    assert detail["versions"] == ["1.0.0"]
    assert tools.invoke("use_skill", {"skill_id": "endpoint-recovery"}).status == "succeeded"
    assert library.list({"user_id": "attacker-should-not-control-this", "query": "endpoint"})["items"] == []


def test_agent_skill_publishing_requires_validation_and_available_tools(tmp_path: Path) -> None:
    library = SkillLibraryService(builtins=())
    tools = AgentToolset(
        ConversationWorkspace(tmp_path, "chat_skills"), skills=library._registry("owner-1"),
        skill_library=library, skill_user_id="owner-1",
    )
    base = {
        "name": "Safe Review", "description": "Review changes safely.",
        "instructions": "## Workflow\n\n1. Inspect the change.",
        "when_to_use": ["before a release"], "when_not_to_use": ["for unrelated research"],
    }

    assert tools.invoke("create_skill", base).status == "failed"
    base["instructions"] += "\n\n## Validation\n\n1. Run a focused test."
    base["requires_tools"] = ["tool-that-does-not-exist"]
    assert tools.invoke("create_skill", base).status == "failed"


def test_agent_edits_only_its_custom_skill_as_a_new_version(tmp_path: Path) -> None:
    library = SkillLibraryService(builtins=())
    library.create({
        "user_id": "owner-1", "name": "Safe Review", "description": "Review changes safely.",
        "instructions": "## Workflow\n\n1. Inspect.\n\n## Validation\n\n1. Test.",
    })
    tools = AgentToolset(
        ConversationWorkspace(tmp_path, "chat_skills"), skills=library._registry("owner-1"),
        skill_library=library, skill_user_id="owner-1",
    )

    outcome = tools.invoke("edit_skill", {"skill_id": "safe-review", "description": "Review release changes safely."})

    assert outcome.status == "succeeded"
    assert outcome.payload["version"] == "1.0.1"
    assert library.get({"user_id": "owner-1", "skill_id": "safe-review"})["description"] == "Review release changes safely."


def test_browse_page_is_absent_without_a_browser(toolset: AgentToolset) -> None:
    assert "browse_page" not in [item.name for item in toolset.definitions()]


def test_browse_page_returns_the_rendered_text(tmp_path) -> None:
    class Browser:
        def render(self, url):
            return "<html><head><title>Rendered</title></head><body><p>hello</p><script>x()</script></body></html>"

    tools = AgentToolset(ConversationWorkspace(tmp_path, "chat_browser"), browser=Browser())

    outcome = tools.invoke("browse_page", {"url": "https://example.test/page"})

    assert outcome.status == "succeeded"
    assert "hello" in outcome.content
    assert "x()" not in outcome.content
    assert outcome.payload["label"] == "Rendered"


def test_interactive_browser_tools_save_a_private_visual_capture(tmp_path: Path) -> None:
    screenshot = base64.b64encode(b"not-a-real-png-but-a-bounded-private-capture").decode()

    class Browser:
        def navigate(self, url):
            return {"url": url, "title": "Example", "html": "<html><body><button id='next'>Next</button></body></html>", "screenshot": screenshot}

        def click(self, selector):
            assert selector == "#next"
            return {"url": "https://example.test/next", "title": "Next", "html": "<html><body>next page</body></html>", "screenshot": screenshot}

    workspace = ConversationWorkspace(tmp_path, "chat_browser_interactive")
    tools = AgentToolset(workspace, browser=Browser())

    assert {"browse_page", "browser_click", "browser_fill", "browser_screenshot"} <= {item.name for item in tools.definitions()}
    opened = tools.invoke("browse_page", {"url": "https://example.test"})
    clicked = tools.invoke("browser_click", {"selector": "#next"})

    assert opened.status == clicked.status == "succeeded"
    path = str(clicked.payload["screenshot_path"])
    assert path.startswith("browser-captures/")
    assert workspace.resolve(path).read_bytes().startswith(b"not-a-real")
    assert clicked.payload["artifacts"] == [{"path": path, "size_bytes": workspace.resolve(path).stat().st_size}]


def test_browse_page_refuses_a_private_address(tmp_path) -> None:
    class Browser:
        def render(self, url):
            raise AssertionError("must not be reached")

    tools = AgentToolset(ConversationWorkspace(tmp_path, "chat_browser"), browser=Browser())

    outcome = tools.invoke("browse_page", {"url": "http://127.0.0.1/admin"})

    assert outcome.status == "failed"


def test_browse_page_redacts_url_credentials_queries_and_secret_like_page_text(tmp_path) -> None:
    secret = "super-secret-123"

    class Browser:
        def render(self, url):
            return f"<html><head><title>token={secret}</title></head><body><p>api_key={secret}</p><p>visible content</p></body></html>"

    raw_url = f"https://user:{secret}@example.test/page?api_key={secret}&next=/account#{secret}"
    outcome = AgentToolset(ConversationWorkspace(tmp_path, "chat_browser_secret"), browser=Browser()).invoke("browse_page", {"url": raw_url})

    assert outcome.status == "succeeded"
    assert secret not in outcome.summary
    assert secret not in outcome.content
    assert secret not in repr(outcome.payload)
    assert "https://example.test/page" in outcome.content
    assert "visible content" in outcome.content


@pytest.mark.parametrize(
    ("page", "secret_fragments"),
    (
        ("<p>Authorization: Bearer bearer-secret-123</p><p>ordinary prose remains</p>", ("bearer-secret-123",)),
        ("<p>api_key = super secret-value-456</p><p>ordinary prose remains</p>", ("super secret-value-456", "secret-value-456")),
        ("<pre>-----BEGIN PRIVATE KEY-----\\nMIIE-secret-material\\n-----END PRIVATE KEY-----</pre><p>ordinary prose remains</p>", ("MIIE-secret-material", "BEGIN PRIVATE KEY", "END PRIVATE KEY")),
        ("<p>Unlabelled eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature-secret-value</p><p>ordinary prose remains</p>", ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signature-secret-value",)),
    ),
)
def test_browse_page_redacts_common_rendered_secret_forms(tmp_path, page: str, secret_fragments: tuple[str, ...]) -> None:
    class Browser:
        def render(self, url):
            return f"<html><head><title>{page}</title></head><body>{page}</body></html>"

    outcome = AgentToolset(ConversationWorkspace(tmp_path, "chat_browser_secret_forms"), browser=Browser()).invoke(
        "browse_page", {"url": "https://example.test/public"}
    )

    assert outcome.status == "succeeded"
    for fragment in secret_fragments:
        assert fragment not in outcome.summary
        assert fragment not in outcome.content
        assert fragment not in repr(outcome.payload)
    assert "ordinary prose remains" in outcome.content


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


def test_web_search_redactor_failures_use_a_fixed_safe_error(tmp_path) -> None:
    from agentos.agentic.web_search import SearchResult

    secret = "redactor-secret-999"

    class ExplodingSearcher:
        def search(self, query, *, limit=5):
            return [SearchResult("title", "https://example.test/a", "snippet")]

        def redact_text(self, value):
            raise RuntimeError(secret)

    tools = AgentToolset(
        ConversationWorkspace(tmp_path, "chat_search_redactor_error"),
        search_client=ExplodingSearcher(),
    )

    outcome = tools.invoke("web_search", {"query": "orin"})

    assert outcome.status == "failed"
    assert outcome.content == "The search provider returned an invalid response."
    assert outcome.summary == "The search provider returned an invalid response."
    assert outcome.payload == {"tool_kind": "web"}
    assert secret not in outcome.content
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


def test_fetch_url_rejects_a_private_redirect_before_following_it(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/internal"}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    tools = AgentToolset(ConversationWorkspace(tmp_path, "chat_fetch_redirect"), http_client=client)

    outcome = tools.invoke("fetch_url", {"url": "https://example.com/start"})

    assert outcome.status == "failed"
    assert len(requests) == 1
    assert "127.0.0.1" not in outcome.content


def test_fetch_url_redacts_url_secrets_and_common_fetched_secret_text(tmp_path: Path) -> None:
    secret = "fetch-secret-123"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            html=(
                f"<html><head><title>token={secret}</title></head>"
                f"<body><p>api_key={secret}</p><p>visible content</p></body></html>"
            ),
            request=request,
        )

    raw_url = f"https://user:{secret}@example.com/doc?token={secret}#{secret}"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    tools = AgentToolset(ConversationWorkspace(tmp_path, "chat_fetch_secret"), http_client=client)

    outcome = tools.invoke("fetch_url", {"url": raw_url})

    assert outcome.status == "succeeded"
    assert secret not in outcome.summary
    assert secret not in outcome.content
    assert secret not in repr(outcome.payload)
    assert "https://example.com/doc" in outcome.content
    assert "visible content" in outcome.content


def test_delegated_tool_outcomes_use_the_same_result_cap(tmp_path: Path) -> None:
    delegated = ToolOutcome("succeeded", "delegated", "x" * (agent_tools.MAX_TOOL_RESULT_CHARS + 100))
    tools = AgentToolset(
        ConversationWorkspace(tmp_path, "chat_delegate_cap"),
        delegate=lambda _name, _task: delegated,
    )

    outcome = tools.invoke("ask_agent", {"name": "Researcher", "task": "return a lot"})

    assert outcome.status == "succeeded"
    assert len(outcome.content) <= agent_tools.MAX_TOOL_RESULT_CHARS
    assert "narrow the request instead of repeating it" in outcome.content


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


def test_parse_arguments_repairs_literal_control_characters_inside_json_strings() -> None:
    raw = '{"path":"styles.css","content":"body {\n  color: \\\"#fff\\\";\r\n\tmargin: 0;\n}"}'

    assert parse_arguments(raw) == {
        "path": "styles.css",
        "content": 'body {\n  color: "#fff";\r\n\tmargin: 0;\n}',
    }


def test_parse_arguments_repairs_unescaped_code_quotes_inside_json_strings() -> None:
    raw = '{"path":"app.js","content":"const message = "hello";\nconsole.log(message);"}'

    assert parse_arguments(raw) == {
        "path": "app.js",
        "content": 'const message = "hello";\nconsole.log(message);',
    }


def test_parse_arguments_repairs_a_quote_followed_by_a_comma_inside_css() -> None:
    raw = '{"path":"styles.css","content":"body { font-family: "Inter", "Helvetica", sans-serif; }"}'

    assert parse_arguments(raw) == {
        "path": "styles.css",
        "content": 'body { font-family: "Inter", "Helvetica", sans-serif; }',
    }


def test_parse_arguments_repairs_a_quote_inside_an_attribute_selector() -> None:
    raw = '{"path":"styles.css","content":"a[data-role="tab"] { color: red; }"}'

    assert parse_arguments(raw) == {
        "path": "styles.css",
        "content": 'a[data-role="tab"] { color: red; }',
    }


def test_parse_arguments_repairs_invalid_escape_sequences() -> None:
    raw = '{"path":"styles.css","content":".q::before { content: \'\\201C\'; }"}'

    assert parse_arguments(raw) == {
        "path": "styles.css",
        "content": ".q::before { content: '\\201C'; }",
    }


def test_parse_arguments_repairs_a_javascript_regex_backslash() -> None:
    raw = '{"path":"app.js","content":"const digits = /\\d+/g;\nconst gap = /\\s/;"}'

    assert parse_arguments(raw) == {
        "path": "app.js",
        "content": "const digits = /\\d+/g;\nconst gap = /\\s/;",
    }


def test_parse_arguments_keeps_a_quote_before_a_brace_as_content() -> None:
    raw = '{"path":"app.js","content":"const close = "}";\nrender(close);"}'

    assert parse_arguments(raw) == {
        "path": "app.js",
        "content": 'const close = "}";\nrender(close);',
    }


def test_parse_arguments_keeps_json_content_that_looks_like_the_call_itself(toolset: AgentToolset) -> None:
    raw = '{"path":"data.json","content":"{"name": "orin", "version": "1.0"}"}'

    assert parse_arguments(raw, toolset.argument_names("write_file")) == {
        "path": "data.json",
        "content": '{"name": "orin", "version": "1.0"}',
    }


def test_argument_names_are_unknown_for_an_unknown_tool(toolset: AgentToolset) -> None:
    assert toolset.argument_names("launch_missiles") is None
    assert toolset.argument_names("write_file") == frozenset({"path", "content", "mode"})


def test_parse_arguments_repairs_nested_arrays_of_edits() -> None:
    raw = '{"path":"app.js","edits":[{"old_text":"const a = "1";","new_text":"const a = "2";"}]}'

    assert parse_arguments(raw) == {
        "path": "app.js",
        "edits": [{"old_text": 'const a = "1";', "new_text": 'const a = "2";'}],
    }


def test_parse_arguments_keeps_valid_json_untouched() -> None:
    raw = '{"path":"a.txt","content":"tab\\tnewline\\nquote\\"backslash\\\\","limit":5,"deep":true}'

    assert parse_arguments(raw) == {
        "path": "a.txt",
        "content": 'tab\tnewline\nquote"backslash\\',
        "limit": 5,
        "deep": True,
    }


def test_parse_arguments_still_rejects_malformed_json_after_safe_repair() -> None:
    with pytest.raises(AgentToolError):
        parse_arguments('{"path":"styles.css","content":"unterminated}')


def test_parse_arguments_reports_truncated_arguments_with_a_way_out() -> None:
    with pytest.raises(AgentToolError) as error:
        parse_arguments('{"path":"styles.css","content":"body {\n  margin: 0;\n  colo')

    message = str(error.value)
    assert "cut off" in message
    assert "append" in message


def test_parse_arguments_repairs_a_long_stylesheet(toolset: AgentToolset) -> None:
    css = "".join(
        f'.card-{index} {{ font-family: "Inter", "Helvetica Neue", sans-serif; }}\n'
        f'.card-{index}::before {{ content: "\\201C"; }}\n'
        f'.card-{index}[data-state="open"] {{ background: url("img/photo-{index}.jpg"); }}\n'
        for index in range(200)
    )

    value = parse_arguments('{"path":"styles.css","content":"' + css + '"}', toolset.argument_names("write_file"))

    assert value == {"path": "styles.css", "content": css}


def test_parse_arguments_rejects_an_ambiguous_payload_without_stalling(toolset: AgentToolset) -> None:
    started = time.perf_counter()

    with pytest.raises(AgentToolError):
        parse_arguments('{"path":"a.css","content":"' + ('x"y", "k": ' * 3000), toolset.argument_names("write_file"))

    assert time.perf_counter() - started < 5.0


def test_write_file_appends_instead_of_overwriting(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "styles.css", "content": "body { margin: 0; }\n"})

    outcome = toolset.invoke("write_file", {"path": "styles.css", "content": "h1 { color: red; }\n", "mode": "append"})

    assert outcome.status == "succeeded"
    assert outcome.payload["mode"] == "append"
    assert toolset.workspace.read_text("styles.css")[0] == "body { margin: 0; }\nh1 { color: red; }\n"


def test_write_file_append_creates_a_missing_file(toolset: AgentToolset) -> None:
    outcome = toolset.invoke("write_file", {"path": "notes/log.md", "content": "first\n", "mode": "append"})

    assert outcome.status == "succeeded"
    assert toolset.workspace.read_text("notes/log.md")[0] == "first\n"


def test_write_file_rejects_an_unknown_mode(toolset: AgentToolset) -> None:
    outcome = toolset.invoke("write_file", {"path": "a.txt", "content": "x", "mode": "prepend"})

    assert outcome.status == "failed"
    assert "append" in outcome.content


def test_write_file_append_respects_the_workspace_write_limit(toolset: AgentToolset) -> None:
    toolset.invoke("write_file", {"path": "big.txt", "content": "x" * 900_000})

    outcome = toolset.invoke("write_file", {"path": "big.txt", "content": "y" * 200_000, "mode": "append"})

    assert outcome.status == "failed"
    assert toolset.workspace.resolve("big.txt").stat().st_size == 900_000


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

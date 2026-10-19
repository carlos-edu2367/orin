"""W5: an MCP server's prompts run as /mcp__server__prompt commands."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentos.mcp.client import McpClient
from agentos.mcp.models import McpServerConfig, McpServerState, McpTransport
from agentos.mcp.toolset import McpToolProvider


class _PromptServer:
    kind = "fake"

    def __init__(self) -> None:
        self.on_message = None
        self.asked: list[dict] = []

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def send(self, frame):
        if "id" not in frame:
            return None
        method, params = frame["method"], frame.get("params") or {}
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "s"}, "capabilities": {"prompts": {}}}
        elif method == "prompts/list":
            result = {"prompts": [{"name": "review", "description": "Review a PR", "arguments": [
                {"name": "pr", "required": True}, {"name": "focus", "required": False},
            ]}]}
        elif method == "prompts/get":
            self.asked.append(params)
            args = params.get("arguments") or {}
            result = {"messages": [
                {"role": "user", "content": {"type": "text", "text": f"Review PR {args.get('pr')} focusing on {args.get('focus', 'anything')}."}},
                {"role": "assistant", "content": {"type": "resource", "resource": {"uri": "x://guide", "text": "Use the checklist."}}},
            ]}
        else:
            return {"jsonrpc": "2.0", "id": frame["id"], "error": {"code": -32601, "message": "no"}}
        return {"jsonrpc": "2.0", "id": frame["id"], "result": result}


def _provider(server: _PromptServer) -> McpToolProvider:
    config = McpServerConfig(
        server_id="user:docs", user_id="u1", slug="docs", display_name="Docs", transport=McpTransport.HTTP,
        url="https://mcp.example.com/mcp", state=McpServerState.ACTIVE, origin="user",
    )
    return McpToolProvider([(config, (), {})], client_factory=lambda config, secrets, **_: McpClient(server))


def test_words_fill_the_prompts_arguments_in_order() -> None:
    server = _PromptServer()
    text = _provider(server).get_prompt("docs", "review", ["42", "the", "parser"])
    assert server.asked[-1]["arguments"] == {"pr": "42", "focus": "the parser"}
    assert "Review PR 42 focusing on the parser." in text and "Use the checklist." in text


def test_a_missing_required_argument_or_prompt_is_refused() -> None:
    provider = _provider(_PromptServer())
    with pytest.raises(ValueError, match="pr"):
        provider.get_prompt("docs", "review", [])
    with pytest.raises(LookupError):
        provider.get_prompt("docs", "nothing", ["1"])
    with pytest.raises(LookupError):
        provider.get_prompt("elsewhere", "review", ["1"])


def test_a_person_runs_an_mcp_prompt_as_a_command(tmp_path: Path) -> None:
    from tests.unit.agentic.test_disk_skills_session import _session

    session = _session(tmp_path, '/mcp__docs__review 7 "error handling"')
    session.mcp_provider = _provider(_PromptServer())
    session.build_runtime()
    texts = [item.text for item in session._reminders.drain() if item.kind == "command"]
    assert texts and "/mcp__docs__review" in texts[0] and "Review PR 7 focusing on error handling." in texts[0]


def test_a_failing_prompt_tells_the_model_why(tmp_path: Path) -> None:
    from tests.unit.agentic.test_disk_skills_session import _session

    session = _session(tmp_path, "/mcp__docs__review")
    session.mcp_provider = _provider(_PromptServer())
    session.build_runtime()
    texts = [item.text for item in session._reminders.drain() if item.kind == "command"]
    assert texts and "could not run" in texts[0] and "pr" in texts[0]

def test_a_windows_path_and_quoted_words_reach_the_prompt_intact(tmp_path: Path) -> None:
    from tests.unit.agentic.test_disk_skills_session import _session

    server = _PromptServer()
    session = _session(tmp_path, '/mcp__docs__review C:\\work\\pr-7 "the parser"')
    session.mcp_provider = _provider(server)
    session.build_runtime()
    assert server.asked[-1]["arguments"] == {"pr": "C:\\work\\pr-7", "focus": "the parser"}


def test_without_any_mcp_server_the_model_hears_why_nothing_ran(tmp_path: Path) -> None:
    from tests.unit.agentic.test_disk_skills_session import _session

    session = _session(tmp_path, "/mcp__docs__review 7")
    session.build_runtime()
    texts = [item.text for item in session._reminders.drain() if item.kind == "command"]
    assert texts and "no MCP server is connected" in texts[0]

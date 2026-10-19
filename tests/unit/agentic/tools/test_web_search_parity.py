"""W4: WebSearch narrows results to allowed domains and drops blocked ones."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.tools.public_names import arguments_in
from agentos.agentic.tools.web.search import WebSearchOutput
from agentos.agentic.workspace import ConversationWorkspace


@dataclass
class _Result:
    title: str
    url: str
    snippet: str


class _Client:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, limit: int = 5):
        self.queries.append(query)
        return [
            _Result("Docs", "https://docs.python.org/3/", "python docs"),
            _Result("Blog", "https://blog.example.com/post", "a post"),
            _Result("Spam", "https://spam.example.com/x", "spam"),
            _Result("Other", "https://other.org/", "other"),
        ]


def _toolset(tmp_path: Path, client: _Client) -> AgentToolset:
    return AgentToolset(ConversationWorkspace(tmp_path, "c1"), search_client=client)


def test_domains_are_kept_on_the_way_in() -> None:
    assert arguments_in("web_search", {"query": "q", "allowed_domains": ["a.com"]}) == {"query": "q", "allowed_domains": ["a.com"]}


def test_allowed_domains_keep_only_their_hosts(tmp_path: Path) -> None:
    client = _Client()
    outcome = _toolset(tmp_path, client).invoke("WebSearch", {"query": "python", "allowed_domains": ["python.org", "example.com"]})
    assert "docs.python.org" in outcome.content and "blog.example.com" in outcome.content
    assert "other.org" not in outcome.content
    assert "site:python.org" in client.queries[0] and "site:example.com" in client.queries[0]


def test_blocked_domains_win(tmp_path: Path) -> None:
    outcome = _toolset(tmp_path, _Client()).invoke(
        "WebSearch", {"query": "x", "allowed_domains": ["example.com"], "blocked_domains": ["spam.example.com"]},
    )
    assert "blog.example.com" in outcome.content and "spam.example.com" not in outcome.content


def test_the_output_matches_claude_code(tmp_path: Path) -> None:
    outcome = _toolset(tmp_path, _Client()).invoke("WebSearch", {"query": "python", "blocked_domains": ["example.com"]})
    output = outcome.data["tool_output"]
    WebSearchOutput.model_validate(output)
    urls = [item["url"] for block in output["results"] for item in block["content"]]
    assert urls == ["https://docs.python.org/3/", "https://other.org/"]
    assert output["query"] == "python" and output["durationSeconds"] >= 0


def test_bad_domains_are_refused(tmp_path: Path) -> None:
    toolset = _toolset(tmp_path, _Client())
    for bad in (["not a domain"], ["a.com/path"], ["x"] * 21, "example.com"):
        assert toolset.invoke("WebSearch", {"query": "q", "allowed_domains": bad}).status == "failed", bad

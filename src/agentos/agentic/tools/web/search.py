"""Search tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import re
import time
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.tools._shared import AgentToolError, _public_url, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec, ToolInput

MAX_SEARCH_DOMAINS = 20
_DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _clean_domains(values: Any, label: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > MAX_SEARCH_DOMAINS:
        raise AgentToolError(f"{label} must be a list of at most {MAX_SEARCH_DOMAINS} domain names")
    cleaned: list[str] = []
    for value in values:
        domain = str(value or "").strip().lower().rstrip(".")
        if domain.startswith("www."):
            domain = domain[4:]
        if not _DOMAIN.fullmatch(domain):
            raise AgentToolError(f"{label}: '{str(value)[:80]}' is not a domain name")
        cleaned.append(domain)
    return list(dict.fromkeys(cleaned))


def _host_in(host: str, domains: list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in domains)


class WebSearchTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def web_search(
        self, query: str, limit: int = 5,
        allowed_domains: list[str] | None = None, blocked_domains: list[str] | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        if self._search_client is None:
            raise AgentToolError("Web search is not available.")
        if not isinstance(query, str) or not query.strip():
            raise AgentToolError("query must be a non-blank string")
        allowed = _clean_domains(allowed_domains, "allowed_domains")
        blocked = _clean_domains(blocked_domains, "blocked_domains")
        provider_query = query.strip()
        if allowed:
            provider_query += " (" + " OR ".join(f"site:{domain}" for domain in allowed) + ")"
        try:
            redact = getattr(self._search_client, "redact_text", str)
            clean_query = str(redact(query.strip()))
            results = self._search_client.search(provider_query, limit=int(limit))
            safe_results: list[tuple[str, str, str]] = []
            for item in results or ():
                try:
                    url = _public_url(str(item.url))
                except (AgentToolError, TypeError, ValueError):
                    continue
                host = urlparse(url).hostname or ""
                if (allowed and not _host_in(host, allowed)) or (blocked and _host_in(host, blocked)):
                    continue
                safe_results.append((str(redact(item.title)), str(redact(url)), str(redact(item.snippet))))
        except httpx.HTTPError as error:
            raise AgentToolError(f"The search provider could not be reached: {type(error).__name__}") from error
        except AgentToolError:
            raise
        except Exception:  # noqa: BLE001 - provider failures must not reach the model
            raise AgentToolError("The search provider returned an invalid response.") from None
        output = {
            "query": clean_query,
            "results": [{
                "tool_use_id": str(getattr(self, "_active_invocation_id", None) or ""),
                "content": [{"title": title, "url": url} for title, url, _ in safe_results],
            }],
            "durationSeconds": round(time.monotonic() - started, 3),
        }
        if not safe_results:
            return {
                "summary": f"Nenhum resultado para '{clean_query[:40]}'", "content": "[no results]",
                "payload": {"count": 0, "label": clean_query[:80]}, "tool_output": output,
            }
        body = "\n\n".join("\n".join(item) for item in safe_results)
        return {
            "summary": f"Buscou na web: {len(safe_results)} {'resultado' if len(safe_results) == 1 else 'resultados'}",
            "content": body,
            "payload": {"count": len(safe_results), "label": clean_query[:80]},
            "tool_output": output,
        }


class WebSearchInput(ToolInput):
    query: str = Field(description="Search query.")
    allowed_domains: list[str] | None = None
    blocked_domains: list[str] | None = None


class WebSearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    url: str


class WebSearchResultBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_use_id: str
    content: list[WebSearchHit]


class WebSearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str
    results: list[WebSearchResultBlock]
    durationSeconds: float


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="web_search",
        description="Search the public web and return titles, URLs and snippets. Use this to find an address, then fetch_url to read it.",
        legacy_schema=_schema({"query": _TEXT, "limit": {"type": "integer", "minimum": 1, "maximum": 10}}, ("query",)),
        handler="web_search",
        kind="web",
        input_model=WebSearchInput,
        output_model=WebSearchOutput,
        public_description='Search the web and return titles, URLs and snippets.',
        read_only=True,
        policy_tags=("network",),
        available=lambda self: (self._search_client is not None),
        order=210,
    ),
)

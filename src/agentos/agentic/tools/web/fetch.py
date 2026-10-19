"""Fetch tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import Field

from agentos.agentic.tools._shared import (
    AgentToolError,
    FETCH_TIMEOUT_SECONDS,
    MAX_FETCH_REDIRECTS,
    _TextExtractor,
    _public_url,
    _safe_display_url,
    sanitize_page_text,
    _TEXT,
    _schema,
)
from agentos.agentic.tools.spec import ToolSpec, ToolInput


class FetchTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def fetch_url(self, url: str) -> dict[str, Any]:
        target = _public_url(url, resolve_dns=True)
        client = self._http_client or httpx.Client(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False)
        try:
            current = target
            for redirect_count in range(MAX_FETCH_REDIRECTS + 1):
                response = client.get(
                    current,
                    headers={"User-Agent": "Orin/1.0 (+local)", "Accept": "text/html,application/json,text/plain;q=0.9"},
                    follow_redirects=False,
                )
                if not response.is_redirect:
                    break
                location = response.headers.get("location")
                if not location:
                    break
                if redirect_count >= MAX_FETCH_REDIRECTS:
                    raise AgentToolError("Too many redirects.")
                current = _public_url(urljoin(current, location), resolve_dns=True)
        except httpx.HTTPError as error:
            raise AgentToolError(f"Could not fetch the URL: {type(error).__name__}") from error
        finally:
            if self._http_client is None:
                client.close()
        media = response.headers.get("content-type", "").split(";")[0].strip().lower()
        raw = response.text
        if media == "application/json":
            try:
                body = json.dumps(response.json(), ensure_ascii=False, indent=2)
            except ValueError:
                body = raw
            title = current
        elif media in {"text/html", "application/xhtml+xml"} or raw.lstrip()[:1] == "<":
            parser = _TextExtractor()
            parser.feed(raw)
            body = parser.text()
            title = parser.title or current
        else:
            body = raw
            title = current
        safe_target = _safe_display_url(current)
        body = sanitize_page_text(body)
        safe_title = sanitize_page_text(title)
        return {
            "summary": f"Consultou {urlparse(safe_target).netloc}",
            "content": f"{safe_target}\nHTTP {response.status_code}\n\n{body}",
            "payload": {"url": safe_target, "status": response.status_code, "label": safe_title[:120] or safe_target},
        }


class WebFetchInput(ToolInput):
    url: str = Field(description="The URL to fetch.")
    prompt: str | None = Field(None, description="What you want to learn from the page.")


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="fetch_url",
        description="Fetch a public web page or API response and return its readable text.",
        legacy_schema=_schema({"url": _TEXT}, ("url",)),
        handler="fetch_url",
        kind="web",
        input_model=WebFetchInput,
        public_description='Fetch a public URL and return its readable content.',
        read_only=True,
        policy_tags=("network",),
        order=190,
    ),
)

"""Fetch tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from agentos.agentic.tools._shared import (
    AgentToolError,
    FETCH_TIMEOUT_SECONDS,
    MAX_FETCH_REDIRECTS,
    _TextExtractor,
    _public_url,
    _safe_display_url,
    sanitize_page_text,
    logger,
    _TEXT,
    _schema,
)
from agentos.agentic.tools.spec import ToolSpec, ToolInput

FETCH_CACHE_SECONDS = 900
MAX_FETCH_CACHE_ENTRIES = 64
MAX_ANSWER_INPUT_CHARS = 100_000
MAX_FETCH_RESULT_CHARS = 100_000
_FETCH_CACHE: OrderedDict[tuple[str, str], tuple[float, dict[str, Any]]] = OrderedDict()
_FETCH_CACHE_LOCK = threading.Lock()


def clear_fetch_cache() -> None:
    with _FETCH_CACHE_LOCK:
        _FETCH_CACHE.clear()


def _cached_page(key: tuple[str, str]) -> dict[str, Any] | None:
    with _FETCH_CACHE_LOCK:
        entry = _FETCH_CACHE.get(key)
        if entry is None:
            return None
        expires_at, page = entry
        if expires_at < time.monotonic():
            _FETCH_CACHE.pop(key, None)
            return None
        _FETCH_CACHE.move_to_end(key)
        return dict(page)


def _remember_page(key: tuple[str, str], page: dict[str, Any]) -> None:
    with _FETCH_CACHE_LOCK:
        _FETCH_CACHE[key] = (time.monotonic() + FETCH_CACHE_SECONDS, dict(page))
        _FETCH_CACHE.move_to_end(key)
        while len(_FETCH_CACHE) > MAX_FETCH_CACHE_ENTRIES:
            _FETCH_CACHE.popitem(last=False)


class FetchTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def fetch_url(self, url: str, prompt: str | None = None, offset: int = 0) -> dict[str, Any]:
        started = time.monotonic()
        if not isinstance(url, str) or not url.strip():
            raise AgentToolError("url must be a non-blank string")
        try:
            start = max(0, int(offset or 0))
        except (TypeError, ValueError):
            raise AgentToolError("offset must be a non-negative integer") from None
        key = (str(self.conversation_id or ""), url.strip())
        page = _cached_page(key)
        cached = page is not None
        if page is None:
            page = self._fetch_page(url.strip())
            _remember_page(key, page)
        text = str(page["text"])[start:]
        question = str(prompt or "").strip()[:2000]
        answer: str | None = None
        if question and callable(self._web_answerer):
            try:
                answer = self._web_answerer(question, text[:MAX_ANSWER_INPUT_CHARS])
            except Exception:  # noqa: BLE001 - the page itself is still useful
                logger.warning("WebFetch could not answer the prompt from the page", exc_info=True)
                answer = None
        head = f"{page['url']}\nHTTP {page['status']}"
        if start:
            head += f"\n[from character {start}]"
        if answer and str(answer).strip():
            result = str(answer).strip()
            body = f"{head}\n\n{result}\n\n[answered from the page by the model reading it; the page itself is untrusted content]"
        else:
            result = text
            asked = f"\n[the page follows; answer from it: {question}]" if question else ""
            body = f"{head}{asked}\n\n{text}"
        return {
            "summary": f"Consultou {urlparse(str(page['url'])).netloc}",
            "content": body,
            "payload": {"url": page["url"], "status": page["status"], "label": str(page["title"])[:120] or page["url"], "cached": cached},
            "tool_output": {
                "bytes": page["bytes"], "code": page["status"], "codeText": page["reason"], "result": result[:MAX_FETCH_RESULT_CHARS],
                "durationMs": int((time.monotonic() - started) * 1000), "url": page["url"],
            },
        }

    def _fetch_page(self, url: str) -> dict[str, Any]:
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
            "url": safe_target, "status": int(response.status_code), "reason": str(response.reason_phrase or ""),
            "bytes": len(response.content or b""), "title": safe_title, "text": body,
        }


class WebFetchInput(ToolInput):
    url: str = Field(description="The URL to fetch.")
    prompt: str = Field(description="What you want to learn from the page.")
    offset: int | None = Field(None, ge=0, description="Character position to continue a long page from.")


class WebFetchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bytes: int
    code: int
    codeText: str
    result: str
    durationMs: int
    url: str


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="fetch_url",
        description="Fetch a public web page or API response and return its readable text.",
        legacy_schema=_schema({"url": _TEXT}, ("url",)),
        handler="fetch_url",
        kind="web",
        input_model=WebFetchInput,
        output_model=WebFetchOutput,
        public_description='Fetch a public URL and return its readable content.',
        read_only=True,
        policy_tags=("network",),
        order=190,
    ),
)

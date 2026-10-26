"""Context-window lookup for catalogs that do not publish one.

NVIDIA NIM Cloud's ``/v1/models`` lists only ``id``/``owned_by``, so every
model it serves arrived with no context window and the chat worker fell back
to its flat 60k budget -- far below what models like MiniMax or GLM accept.
OpenRouter's public catalog (no key needed) carries ``context_length`` for
most open-weight models, under the same model slug with a different vendor
prefix (``minimaxai/`` vs ``minimax/``), so it is used as a reference.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping

import httpx

ContextWindowLookup = Callable[[str], "int | None"]

_SIZE_MARKER_RE = re.compile(r"(?i)(?:^|[-_/])(\d{1,4})k(?=$|[-_.])")


def _slug(model_id: str) -> str:
    return model_id.strip().lstrip("~").split(":", 1)[0].rsplit("/", 1)[-1].lower()


def _full_id(model_id: str) -> str:
    return model_id.strip().lstrip("~").split(":", 1)[0].lower()


def context_window_from_id(model_id: str) -> int | None:
    """A window spelled into the id itself (``phi-3-vision-128k-instruct``)."""
    match = _SIZE_MARKER_RE.search(model_id or "")
    if match is None:
        return None
    size = int(match.group(1))
    return size * 1000 if size >= 2 else None


class ReferenceContextWindows:
    """Maps model ids to context windows taken from a reference catalog."""

    def __init__(self, models: list[Mapping[str, Any]]) -> None:
        self._by_full_id: dict[str, int] = {}
        self._by_slug: dict[str, int] = {}
        for model in models:
            model_id = model.get("id")
            window = model.get("context_length")
            if not isinstance(model_id, str) or isinstance(window, bool) or not isinstance(window, int) or window < 1:
                continue
            full_id = _full_id(model_id)
            self._by_full_id[full_id] = min(window, self._by_full_id.get(full_id, window))
            slug = _slug(model_id)
            # Two vendors publishing the same slug is rare; when it happens the
            # smaller window is the one that cannot overflow a request.
            self._by_slug[slug] = min(window, self._by_slug.get(slug, window))

    def __call__(self, model_id: str) -> int | None:
        if not model_id:
            return None
        found = self._by_full_id.get(_full_id(model_id)) or self._by_slug.get(_slug(model_id))
        return found if found is not None else context_window_from_id(model_id)


class OpenRouterContextReference:
    """Loads OpenRouter's public model list once per catalog refresh."""

    def __init__(self, *, base_url: str = "https://openrouter.ai", timeout: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def load(self) -> ContextWindowLookup:
        response = httpx.get(f"{self._base_url}/api/v1/models", timeout=self._timeout)
        response.raise_for_status()
        payload: Any = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise ValueError("OpenRouter returned an invalid catalog")
        return ReferenceContextWindows([item for item in data if isinstance(item, Mapping)])


__all__ = [
    "ContextWindowLookup",
    "OpenRouterContextReference",
    "ReferenceContextWindows",
    "context_window_from_id",
]

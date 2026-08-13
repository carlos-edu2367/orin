"""One neutral image block, three provider shapes.

The runtime never learns a provider's content format: it appends
``image_block`` values and this module rewrites them at the transport edge,
which is the only place a provider name is already known.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

IMAGE = "image"


def image_block(media_type: str, data: str) -> dict[str, str]:
    """A provider-neutral image block: base64 payload plus its media type."""
    return {"type": IMAGE, "media_type": str(media_type), "data": str(data)}


def _is_image(block: object) -> bool:
    return isinstance(block, Mapping) and block.get("type") == IMAGE


def _anthropic(block: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": str(block.get("media_type") or "image/png"), "data": str(block.get("data") or "")}}


def _openai(block: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": f"data:{block.get('media_type') or 'image/png'};base64,{block.get('data') or ''}"}}


def project_messages(messages: Sequence[Mapping[str, Any]], provider: str) -> list[dict[str, Any]]:
    """Rewrite neutral image blocks into ``provider``'s own representation."""
    name = str(provider or "").lower()
    projected: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list) or not any(_is_image(block) for block in content):
            projected.append(dict(message))
            continue
        if name == "ollama":
            # The native API carries images beside the text, not inside it.
            text = " ".join(str(block.get("text") or "") for block in content if isinstance(block, Mapping) and block.get("type") == "text").strip()
            images = [str(block.get("data") or "") for block in content if _is_image(block)]
            projected.append({**{key: value for key, value in message.items() if key != "content"}, "content": text, "images": images})
            continue
        convert = _anthropic if name == "anthropic" else _openai
        projected.append({**message, "content": [convert(block) if _is_image(block) else dict(block) if isinstance(block, Mapping) else block for block in content]})
    return projected


__all__ = ["image_block", "project_messages"]

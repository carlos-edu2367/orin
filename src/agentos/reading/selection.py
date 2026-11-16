"""Pick the model that reads a picture when the turn's own model cannot.

The order is deliberate: what the person chose, then the provider already in
use for this turn, then a local Ollama — a file the person attached should not
leave the machine when a local model can read it — then anything else.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

LOCAL_PROVIDER = "ollama"
PROVIDERS_WITHOUT_INLINE_IMAGES: frozenset[str] = frozenset({"google-antigravity", "openai-codex"})


@dataclass(frozen=True, slots=True)
class VisionModel:
    provider: str
    model_id: str


def choose_vision_model(candidates: Sequence[VisionModel], *, turn_provider: str, override: VisionModel | None = None) -> VisionModel | None:
    """The model that will transcribe, or None when nothing can."""
    available = [
        item for item in candidates
        if item.provider.strip().lower() not in PROVIDERS_WITHOUT_INLINE_IMAGES
    ]
    if not available:
        return None
    if (
        override is not None
        and override.provider.strip().lower() not in PROVIDERS_WITHOUT_INLINE_IMAGES
        and override in available
    ):
        return override
    provider = str(turn_provider or "").lower()
    for item in available:
        if item.provider.lower() == provider:
            return item
    for item in available:
        if item.provider.lower() == LOCAL_PROVIDER:
            return item
    return available[0]


__all__ = ["LOCAL_PROVIDER", "PROVIDERS_WITHOUT_INLINE_IMAGES", "VisionModel", "choose_vision_model"]

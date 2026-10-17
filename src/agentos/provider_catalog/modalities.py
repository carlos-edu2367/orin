"""Hybrid media modality detection and same-provider media model authorization."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

VALID_OUTPUT_MODALITIES = frozenset({"text", "image", "audio", "video"})
MEDIA_MODALITIES = ("image", "audio", "video")

_IMAGE_MODEL_RE = re.compile(
    r"(?i)(?:^|[/._-])(?:"
    r"gpt-image[\w.-]*|dall-e[\w.-]*|flux[\w.-]*|sdxl[\w.-]*|stable-diffusion[\w.-]*|"
    r"imagen[\w.-]*|recraft[\w.-]*|ideogram[\w.-]*|midjourney[\w.-]*|playground-v[\w.-]*|"
    r"[\w.-]*-image-preview[\w.-]*|[\w.-]*-image-generation[\w.-]*"
    r")$"
)

_AUDIO_MODEL_RE = re.compile(
    r"(?i)(?:^|[/._-])(?:"
    r"tts-[\w.-]+|[\w.-]+-tts(?:-[\w.-]+)?|gpt-4o(?:-[\w.-]+)?-audio[\w.-]*|"
    r"elevenlabs[\w.-]*|kokoro[\w.-]*|bark[\w.-]*|musicgen[\w.-]*|stable-audio[\w.-]*"
    r")$"
)

_VIDEO_MODEL_RE = re.compile(
    r"(?i)(?:^|[/._-])(?:"
    r"sora[\w.-]*|veo[\w.-]*|runway[\w.-]*|gen-3[\w.-]*|kling[\w.-]*|"
    r"luma[\w.-]*|ray-[23][\w.-]*|minimax-video[\w.-]*|wan-[\w.-]+|"
    r"cogvideo[\w.-]*|hunyuan-video[\w.-]*|mochi[\w.-]*"
    r")$"
)


def infer_output_modalities(
    model_id: str,
    explicit_modalities: Sequence[str] | Iterable[str] = (),
) -> tuple[str, ...]:
    """Return normalized output modalities, enriching text-only defaults when model_id matches a known media family."""
    cleaned: list[str] = []
    if isinstance(explicit_modalities, str):
        raw_items: Iterable[str] = [item.strip() for item in explicit_modalities.split(",")]
    else:
        raw_items = explicit_modalities or ()
    for item in raw_items:
        norm = str(item or "").strip().lower()
        if norm in VALID_OUTPUT_MODALITIES and norm not in cleaned:
            cleaned.append(norm)

    if any(mod in cleaned for mod in MEDIA_MODALITIES):
        return tuple(cleaned)

    candidate = str(model_id or "").strip().lstrip("~")
    inferred: list[str] = []
    if candidate:
        if _VIDEO_MODEL_RE.search(candidate):
            inferred.append("video")
        elif _AUDIO_MODEL_RE.search(candidate):
            inferred.append("audio")
        elif _IMAGE_MODEL_RE.search(candidate):
            inferred.append("image")

    if inferred:
        return tuple(inferred)
    return tuple(cleaned) if cleaned else ("text",)


@dataclass(frozen=True, slots=True)
class SameProviderMediaModelPolicy:
    """Authorizes image, audio, and video generation models strictly within the turn's active provider."""

    turn_provider: str
    favorites_by_modality: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_catalog_models(
        cls,
        *,
        turn_provider: str,
        favorite_models: Iterable[tuple[str, str, Sequence[str]]],
    ) -> "SameProviderMediaModelPolicy":
        active = str(turn_provider or "").strip().lower()
        buckets: dict[str, list[str]] = {mod: [] for mod in MEDIA_MODALITIES}
        for provider, model_id, raw_modalities in favorite_models:
            if str(provider or "").strip().lower() != active:
                continue
            clean_id = str(model_id or "").strip()
            if not clean_id or clean_id.lower().endswith(":batch"):
                continue
            modalities = infer_output_modalities(clean_id, raw_modalities)
            for mod in MEDIA_MODALITIES:
                if mod in modalities and clean_id not in buckets[mod]:
                    buckets[mod].append(clean_id)
        return cls(
            turn_provider=active,
            favorites_by_modality={mod: tuple(items) for mod, items in buckets.items() if items},
        )

    def available_modalities(self) -> frozenset[str]:
        return frozenset(
            mod for mod, models in self.favorites_by_modality.items()
            if mod in MEDIA_MODALITIES and models
        )

    def has_modality(self, modality: str) -> bool:
        return bool(self.models_for(modality))

    def models_for(self, modality: str) -> tuple[str, ...]:
        return tuple(self.favorites_by_modality.get(str(modality or "").strip().lower(), ()))

    def default_model_for(self, modality: str) -> str | None:
        models = self.models_for(modality)
        return models[0] if models else None

    def resolve_model(self, modality: str, requested_model: str | None = None) -> str:
        norm_mod = str(modality or "").strip().lower()
        allowed = self.models_for(norm_mod)
        if not allowed:
            raise PermissionError(
                f"no favorited {norm_mod} model is configured for provider '{self.turn_provider}'"
            )
        raw = str(requested_model or "").strip()
        if not raw:
            return allowed[0]
        if raw in allowed:
            return raw
        norm_raw = raw.lstrip("~").lower()
        for candidate in allowed:
            cand_norm = candidate.lstrip("~").lower()
            if cand_norm == norm_raw or cand_norm.split("/", 1)[-1] == norm_raw:
                return candidate
        raise PermissionError(
            f"model '{raw}' is not a favorited {norm_mod} model for provider '{self.turn_provider}'. "
            f"Available favorites: {', '.join(allowed)}"
        )


__all__ = [
    "MEDIA_MODALITIES",
    "SameProviderMediaModelPolicy",
    "VALID_OUTPUT_MODALITIES",
    "infer_output_modalities",
]

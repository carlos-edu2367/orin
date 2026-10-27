"""Hybrid media modality detection and same-provider media model authorization."""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

VALID_OUTPUT_MODALITIES = frozenset({"text", "image", "audio", "video"})
MEDIA_MODALITIES = ("image", "audio", "video")

_IMAGE_MODEL_RE = re.compile(
    r"(?i)(?:^|[/._-])(?:"
    r"gpt-image[\w.-]*|dall-e[\w.-]*|flux[\w.-]*|sdxl[\w.-]*|stable-diffusion[\w.-]*|"
    r"imagen[\w.-]*|recraft[\w.-]*|ideogram[\w.-]*|midjourney[\w.-]*|playground-v[\w.-]*|"
    r"qwen-image[\w.-]*|seedream[\w.-]*|nano-banana[\w.-]*|"
    r"[\w.-]*-image-preview[\w.-]*|[\w.-]*-image-generation[\w.-]*"
    r")$"
)

_AUDIO_MODEL_RE = re.compile(
    r"(?i)(?:^|[/._-])(?:"
    r"tts-[\w.-]+|[\w.-]+-tts(?:-[\w.-]+)?|gpt-4o(?:-[\w.-]+)?-audio[\w.-]*|"
    r"elevenlabs[/\w.-]*|eleven-[\w.-]+|kokoro[\w.-]*|bark[\w.-]*|musicgen[\w.-]*|stable-audio[\w.-]*"
    r")$"
)

_VIDEO_MODEL_RE = re.compile(
    r"(?i)(?:^|[/._-])(?:"
    r"sora[\w.-]*|veo[\w.-]*|runway[\w.-]*|gen-3[\w.-]*|kling[\w.-]*|"
    r"luma[\w.-]*|ray-[23][\w.-]*|minimax-video[\w.-]*|wan-[\w.-]+|seedance[\w.-]*|"
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
class MediaModelTarget:
    """A resolved (provider, model_id) pair for media generation."""

    provider: str
    model_id: str

    @property
    def qualified_id(self) -> str:
        return f"{self.provider}:{self.model_id}"


@dataclass(frozen=True, slots=True)
class SameProviderMediaModelPolicy:
    """Authorizes image, audio, and video generation models across configured defaults and enabled providers."""

    turn_provider: str
    favorites_by_modality: Mapping[str, tuple[str, ...]]
    targets_by_modality: Mapping[str, tuple[MediaModelTarget, ...]] = field(default_factory=dict)

    @classmethod
    def from_catalog_models(
        cls,
        *,
        turn_provider: str,
        favorite_models: Iterable[tuple[str, str, Sequence[str]]],
        defaults_by_modality: Mapping[str, tuple[str, str] | MediaModelTarget] | None = None,
        enabled_providers: Iterable[str] | None = None,
    ) -> "SameProviderMediaModelPolicy":
        active = str(turn_provider or "").strip().lower()
        cross_provider_mode = defaults_by_modality is not None or enabled_providers is not None
        if enabled_providers is not None:
            allowed_providers = {str(p).strip().lower() for p in enabled_providers if str(p).strip()}
        else:
            allowed_providers = {active} if active else set()

        target_buckets: dict[str, list[MediaModelTarget]] = {mod: [] for mod in MEDIA_MODALITIES}

        def _append_target(mod: str, prov: str, mid: str) -> None:
            target = MediaModelTarget(provider=prov, model_id=mid)
            if target not in target_buckets[mod]:
                target_buckets[mod].append(target)

        # 1. Configured per-modality default (always first priority and auto-authorized if its provider is enabled)
        if defaults_by_modality:
            for raw_mod, raw_target in defaults_by_modality.items():
                mod = str(raw_mod or "").strip().lower()
                if mod not in MEDIA_MODALITIES or raw_target is None:
                    continue
                if isinstance(raw_target, MediaModelTarget):
                    d_prov, d_mid = raw_target.provider, raw_target.model_id
                else:
                    d_prov, d_mid = raw_target[0], raw_target[1]
                clean_prov = str(d_prov or "").strip().lower()
                clean_mid = str(d_mid or "").strip()
                if not clean_prov or not clean_mid or clean_mid.lower().endswith(":batch"):
                    continue
                if allowed_providers and clean_prov not in allowed_providers:
                    continue
                _append_target(mod, clean_prov, clean_mid)

        # Materialize favorite_models so we can prioritize turn_provider favorites before other enabled providers
        normalized_favorites: list[tuple[str, str, tuple[str, ...]]] = []
        for provider, model_id, raw_modalities in favorite_models:
            clean_prov = str(provider or "").strip().lower()
            clean_id = str(model_id or "").strip()
            if not clean_prov or not clean_id or clean_id.lower().endswith(":batch"):
                continue
            if not cross_provider_mode and clean_prov != active:
                continue
            if allowed_providers and clean_prov not in allowed_providers:
                continue
            modalities = infer_output_modalities(clean_id, raw_modalities)
            normalized_favorites.append((clean_prov, clean_id, modalities))

        # 2. Favorites belonging to the active turn provider
        for clean_prov, clean_id, modalities in normalized_favorites:
            if clean_prov != active:
                continue
            for mod in MEDIA_MODALITIES:
                if mod in modalities:
                    _append_target(mod, clean_prov, clean_id)

        # 3. Favorites belonging to other enabled providers (when cross-provider mode is active)
        if cross_provider_mode:
            for clean_prov, clean_id, modalities in normalized_favorites:
                if clean_prov == active:
                    continue
                for mod in MEDIA_MODALITIES:
                    if mod in modalities:
                        _append_target(mod, clean_prov, clean_id)

        return cls(
            turn_provider=active,
            favorites_by_modality={
                mod: tuple(t.model_id for t in items)
                for mod, items in target_buckets.items()
                if items
            },
            targets_by_modality={
                mod: tuple(items)
                for mod, items in target_buckets.items()
                if items
            },
        )

    def targets_for(self, modality: str) -> tuple[MediaModelTarget, ...]:
        norm_mod = str(modality or "").strip().lower()
        if self.targets_by_modality and norm_mod in self.targets_by_modality:
            return tuple(self.targets_by_modality[norm_mod])
        legacy = self.favorites_by_modality.get(norm_mod, ())
        return tuple(MediaModelTarget(self.turn_provider, mid) for mid in legacy)

    def available_modalities(self) -> frozenset[str]:
        return frozenset(
            mod for mod in MEDIA_MODALITIES
            if self.targets_for(mod)
        )

    def has_modality(self, modality: str) -> bool:
        return bool(self.targets_for(modality))

    def models_for(self, modality: str) -> tuple[str, ...]:
        targets = self.targets_for(modality)
        if not targets:
            return ()
        if all(t.provider == self.turn_provider for t in targets):
            return tuple(t.model_id for t in targets)
        return tuple(t.model_id if t.provider == self.turn_provider else t.qualified_id for t in targets)

    def default_target_for(self, modality: str) -> MediaModelTarget | None:
        targets = self.targets_for(modality)
        return targets[0] if targets else None

    def default_model_for(self, modality: str) -> str | None:
        models = self.models_for(modality)
        return models[0] if models else None

    def resolve_target(self, modality: str, requested_model: str | None = None) -> MediaModelTarget:
        norm_mod = str(modality or "").strip().lower()
        allowed_targets = self.targets_for(norm_mod)
        if not allowed_targets:
            raise PermissionError(
                f"no configured or favorited {norm_mod} model is available for provider '{self.turn_provider}'"
            )
        raw = str(requested_model or "").strip()
        if not raw:
            return allowed_targets[0]

        # Check explicit provider:model_id qualification first
        if ":" in raw:
            req_prov, req_mid = raw.split(":", 1)
            norm_prov = req_prov.strip().lower()
            norm_mid = req_mid.strip().lstrip("~").lower()
            for candidate in allowed_targets:
                cand_norm = candidate.model_id.lstrip("~").lower()
                if candidate.provider == norm_prov and (
                    cand_norm == norm_mid or cand_norm.split("/", 1)[-1] == norm_mid
                ):
                    return candidate

        # Unqualified model_id lookup across allowed targets in priority order
        for candidate in allowed_targets:
            if candidate.model_id == raw:
                return candidate
        norm_raw = raw.lstrip("~").lower()
        for candidate in allowed_targets:
            cand_norm = candidate.model_id.lstrip("~").lower()
            if cand_norm == norm_raw or cand_norm.split("/", 1)[-1] == norm_raw:
                return candidate

        allowed_labels = ", ".join(self.models_for(norm_mod))
        raise PermissionError(
            f"model '{raw}' is not an authorized {norm_mod} model for provider '{self.turn_provider}'. "
            f"Available models: {allowed_labels}"
        )

    def resolve_model(self, modality: str, requested_model: str | None = None) -> str:
        return self.resolve_target(modality, requested_model).model_id


CrossProviderMediaModelPolicy = SameProviderMediaModelPolicy


__all__ = [
    "CrossProviderMediaModelPolicy",
    "MEDIA_MODALITIES",
    "MediaModelTarget",
    "SameProviderMediaModelPolicy",
    "VALID_OUTPUT_MODALITIES",
    "infer_output_modalities",
]

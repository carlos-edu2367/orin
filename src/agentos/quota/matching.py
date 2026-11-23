"""Which scoped quota window applies to a given model id, per subscription provider."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .normalize import normalize_provider_name

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Antigravity (agy) serves a Gemini group and a "Claude and GPT" group; the model id prefix picks the family.
_AGY_FAMILIES: tuple[tuple[str, str], ...] = (("gemini", "gemini"), ("claude", "claude"), ("gpt", "gpt"))


def _slug(value: str | None) -> str:
    return _NON_ALNUM.sub("", str(value or "").lower())


def scope_matches_model(
    provider: str,
    scope_model: str | None,
    model_id: str | None,
    *,
    scope_models: Iterable[str] = (),
) -> bool:
    """True when a window scoped to ``scope_model`` (and the models of its group) covers ``model_id``.

    Matching ignores punctuation, so "Gemini 3.8 Flash" matches ``gemini-3.8-flash-low`` and
    "Claude Sonnet 5.5" matches ``claude-sonnet-5-5-high``. A group without a model list still matches by
    family for Antigravity (``gemini*`` -> the Gemini group, ``claude*``/``gpt*`` -> the Claude and GPT group).
    """
    model = str(model_id or "").strip().lower()
    if not model or not scope_model:
        return False
    canon = normalize_provider_name(provider)
    bare = model.split("/", 1)[-1]
    if scope_model.lower() in model:
        return True
    model_slug = _slug(bare)
    scope_slug = _slug(scope_model)
    if scope_slug and scope_slug in model_slug:
        return True
    for name in scope_models:
        name_slug = _slug(name)
        if name_slug and name_slug in model_slug:
            return True
    if canon == "google-antigravity":
        group = scope_model.lower()
        for prefix, token in _AGY_FAMILIES:
            if bare.startswith(prefix):
                return token in group
    return False


__all__ = ["scope_matches_model"]

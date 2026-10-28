"""Chatterbox Multilingual text-to-speech worker (runs in Orin's isolated `chatterbox` environment).

A voice is either the model's built-in speaker or a short reference clip
(5–15 s) that Chatterbox clones zero-shot. Conditionals are computed once per
(clip, exaggeration) and kept by reference: `prepare_conditionals` replaces the
model's voice, and its tensors are not graph leaves, so they cannot be copied.
"""
from __future__ import annotations

from typing import Any

from _protocol import expose_windows_dll_directories, pcm16_base64, serve

expose_windows_dll_directories()

_model: Any = None
_device = "cpu"
_builtin_conds: Any = None
_voice_conds: dict[tuple[str, float], Any] = {}
_DEFAULT_SEED = 42


def _seed_deterministic(seed: int = _DEFAULT_SEED) -> None:
    expose_windows_dll_directories()
    try:
        import torch

        torch.manual_seed(seed)
        if hasattr(torch, "cuda") and torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except Exception:
        pass
    try:
        import numpy as np

        np.random.seed(seed)
    except Exception:
        pass


def _chatterbox() -> Any:
    global _model, _device, _builtin_conds
    if _model is None:
        expose_windows_dll_directories()
        import torch
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS

        if torch.cuda.is_available():
            _device = "cuda"
        elif getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            _device = "mps"
        _model = ChatterboxMultilingualTTS.from_pretrained(device=_device)
        _builtin_conds = _model.conds
    return _model


def load(_request: dict[str, Any]) -> dict[str, Any]:
    model = _chatterbox()
    _seed_deterministic(_DEFAULT_SEED)
    # Downloads the weights on first run and proves the whole pipeline speaks.
    model.generate("Olá!", language_id="pt")
    return {"device": _device}


def synthesize(request: dict[str, Any]) -> dict[str, Any]:
    model = _chatterbox()
    voice_path = str(request.get("voice_path") or "")
    exaggeration = max(0.25, min(2.0, float(request.get("exaggeration") or 0.5)))
    if voice_path:
        key = (voice_path, round(exaggeration, 2))
        conds = _voice_conds.get(key)
        if conds is None:
            model.prepare_conditionals(voice_path, exaggeration=exaggeration)
            conds = model.conds
            _voice_conds[key] = conds
        model.conds = conds
    else:
        model.conds = _builtin_conds
        _seed_deterministic(int(request.get("seed") or _DEFAULT_SEED))
    raw_lang = str(request.get("language") or "pt").strip().lower()
    lang_id = "pt" if raw_lang.startswith("pt") else (raw_lang.split("-")[0] or "pt")
    wav = model.generate(
        str(request.get("text") or ""),
        language_id=lang_id,
        exaggeration=exaggeration,
        cfg_weight=max(0.0, min(1.0, float(request.get("cfg_weight") if request.get("cfg_weight") is not None else 0.5))),
    )
    return {"pcm_base64": pcm16_base64(wav.squeeze(0).numpy()), "sample_rate": int(model.sr), "device": _device}


if __name__ == "__main__":
    serve({"load": load, "synthesize": synthesize})

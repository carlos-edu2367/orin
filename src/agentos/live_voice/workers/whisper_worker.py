"""faster-whisper speech-to-text worker (runs in Orin's isolated `whisper` environment)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from _protocol import expose_windows_dll_directories, pcm16_from_base64, serve

MODEL_ALIASES = {
    "faster-whisper-large-v3-turbo": "large-v3-turbo",
    "faster-whisper-small-int8": "small",
}
_models: dict[str, Any] = {}


def _expose_cuda_libraries() -> None:
    """The cuBLAS/cuDNN wheels keep their DLLs under site-packages/nvidia/*/bin (Windows) or lib (Linux)."""
    expose_windows_dll_directories()
    for entry in sys.path:
        nvidia = Path(entry) / "nvidia"
        if not nvidia.is_dir():
            continue
        for lib_dir in [*nvidia.glob("*/bin"), *nvidia.glob("*/lib")]:
            current_path = os.environ.get("PATH", "")
            if str(lib_dir) not in current_path.split(os.pathsep):
                os.environ["PATH"] = f"{lib_dir}{os.pathsep}{current_path}" if current_path else str(lib_dir)


_expose_cuda_libraries()


def _model(model_id: str) -> tuple[Any, str]:
    _expose_cuda_libraries()
    import ctranslate2
    from faster_whisper import WhisperModel

    has_cuda = ctranslate2.get_cuda_device_count() > 0
    # No model named: the best one this machine runs in real time.
    name = MODEL_ALIASES.get(model_id, model_id) or ("large-v3-turbo" if has_cuda else "small")
    cached = _models.get(name)
    if cached is not None:
        return cached
    if has_cuda:
        try:
            loaded = (WhisperModel(name, device="cuda", compute_type="float16"), "cuda")
            _models[name] = loaded
            return loaded
        except Exception as exc:  # noqa: BLE001 - CUDA present but unusable: CPU still works
            print(f"CUDA unavailable for faster-whisper, using CPU: {exc}", file=sys.stderr)
    loaded = (WhisperModel(name, device="cpu", compute_type="int8"), "cpu")
    _models[name] = loaded
    return loaded


def load(request: dict[str, Any]) -> dict[str, Any]:
    model, device = _model(str(request.get("model") or ""))
    return {"device": device, "model": getattr(model, "model_size_or_path", "")}


def transcribe(request: dict[str, Any]) -> dict[str, Any]:
    model, device = _model(str(request.get("model") or ""))
    audio = pcm16_from_base64(str(request.get("pcm_base64") or ""))
    rate = int(request.get("sample_rate") or 16000)
    if rate != 16000 and audio.size:
        import numpy as np

        duration = audio.size / rate
        target = np.linspace(0.0, duration, int(duration * 16000), endpoint=False)
        audio = np.interp(target, np.linspace(0.0, duration, audio.size, endpoint=False), audio).astype(np.float32)
    prompt_hint = str(request.get("prompt_hint") or "").strip() or None
    segments, _info = model.transcribe(
        audio,
        language=str(request.get("language") or "pt").split("-")[0],
        beam_size=1,
        vad_filter=True,
        condition_on_previous_text=False,
        initial_prompt=prompt_hint,
    )
    return {"text": " ".join(segment.text.strip() for segment in segments).strip(), "device": device}


if __name__ == "__main__":
    _expose_cuda_libraries()
    serve({"load": load, "transcribe": transcribe})

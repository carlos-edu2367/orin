"""Kokoro-82M (ONNX) text-to-speech worker (runs in Orin's isolated `kokoro` environment).

Usage: kokoro_worker.py <dir holding kokoro-v1.0.onnx and voices-v1.0.bin>
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from _protocol import pcm16_base64, serve

_engine: Any = None
_model_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()


def _espeak_data_dir() -> str:
    """espeak-ng keeps its data path in a 160-byte buffer and silently falls back to a
    build-machine path when it is longer, so deep install folders get a short copy."""
    import espeakng_loader

    data = Path(espeakng_loader.get_data_path())
    if len(str(data)) <= 110:
        return str(data)
    short = Path(tempfile.gettempdir()) / "orin-espeak-ng-data"
    if len(str(short)) > 110:
        short = Path(data.anchor) / "orin-espeak-ng-data"
    if not (short / "phontab").is_file():
        shutil.copytree(data, short, dirs_exist_ok=True)
    return str(short)


def _kokoro() -> Any:
    global _engine
    if _engine is None:
        import espeakng_loader
        from kokoro_onnx import Kokoro
        from kokoro_onnx.config import EspeakConfig

        _engine = Kokoro(
            str(_model_dir / "kokoro-v1.0.onnx"),
            str(_model_dir / "voices-v1.0.bin"),
            espeak_config=EspeakConfig(data_path=_espeak_data_dir(), lib_path=espeakng_loader.get_library_path()),
        )
    return _engine


def load(_request: dict[str, Any]) -> dict[str, Any]:
    engine = _kokoro()
    # A real sentence proves phonemizer, espeak data and the model all work together.
    engine.create("Olá!", voice="pf_dora", lang="pt-br")
    return {"voices": sorted(engine.get_voices()), "device": "cpu"}


def synthesize(request: dict[str, Any]) -> dict[str, Any]:
    engine = _kokoro()
    voice = str(request.get("voice") or "pf_dora")
    if voice not in engine.get_voices():
        voice = "pf_dora"
    samples, sample_rate = engine.create(
        str(request.get("text") or ""),
        voice=voice,
        speed=max(0.5, min(2.0, float(request.get("speed") or 1.0))),
        lang=str(request.get("language") or "pt-br"),
    )
    return {"pcm_base64": pcm16_base64(samples), "sample_rate": int(sample_rate), "voice": voice}


if __name__ == "__main__":
    serve({"load": load, "synthesize": synthesize})

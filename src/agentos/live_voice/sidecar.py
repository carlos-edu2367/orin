from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agentos.live_voice.hardware_governor import VoiceHardwareGovernor
from agentos.live_voice.providers import (
    MultiProviderSTTAdapter,
    MultiProviderTTSAdapter,
    VoiceConfig,
)

DEFAULT_SIDECAR_HOST = "127.0.0.1"
DEFAULT_SIDECAR_PORT = 20129


class SidecarTTSRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(min_length=1, max_length=2000)
    model_id: str = Field(default="chatterbox-pt-br", max_length=120)
    voice_id: str = Field(default="orin-default-ptbr", max_length=120)
    reference_audio_path: str | None = Field(default=None, max_length=1024)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class SidecarSTTRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    pcm_base64: str = Field(default="")
    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    language: str = Field(default="pt", max_length=16)
    model_id: str = Field(default="faster-whisper-large-v3-turbo", max_length=120)
    prompt_hint: str | None = Field(default=None, max_length=500)


def create_sidecar_app() -> FastAPI:
    """Cria a aplicação FastAPI isolada do `orin-voice-sidecar`."""
    app = FastAPI(title="Orin Voice Sidecar", version="1.0.0")
    governor = VoiceHardwareGovernor()
    tts_adapter = MultiProviderTTSAdapter(sample_rate=24000)
    stt_adapter = MultiProviderSTTAdapter()
    loaded_models: set[str] = set()

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": "orin-voice-sidecar",
                "loaded_models": sorted(loaded_models),
            }
        )

    @app.get("/v1/hardware")
    async def hardware() -> JSONResponse:
        profile = governor.recommend()
        return JSONResponse(profile.to_dict())

    @app.post("/v1/tts/synthesize")
    async def synthesize(payload: SidecarTTSRequest) -> JSONResponse:
        loaded_models.add(payload.model_id)
        cfg = VoiceConfig(
            tts_provider="local_sidecar",
            tts_model=payload.model_id,
            voice_id=payload.voice_id,
            reference_audio_path=payload.reference_audio_path,
            speed=payload.speed,
        )
        chunk = tts_adapter.synthesize_chunk(payload.text, voice_config=cfg)
        return JSONResponse(
            {
                "sample_rate": chunk.sample_rate,
                "duration_ms": chunk.duration_ms,
                "model_id": chunk.model_id,
                "pcm_base64": base64.b64encode(chunk.pcm_bytes).decode("ascii"),
                "word_timings": [wt.to_dict() for wt in chunk.word_timings],
            }
        )

    @app.post("/v1/stt/transcribe")
    async def transcribe(payload: SidecarSTTRequest) -> JSONResponse:
        loaded_models.add(payload.model_id)
        try:
            pcm_bytes = base64.b64decode(payload.pcm_base64) if payload.pcm_base64 else b""
        except Exception:
            pcm_bytes = b""

        res = stt_adapter.transcribe(
            pcm_bytes,
            sample_rate=payload.sample_rate,
            language=payload.language,
            provider="local_sidecar",
            model_id=payload.model_id,
            prompt_hint=payload.prompt_hint,
        )
        return JSONResponse(
            {
                "text": res.text,
                "language": res.language,
                "confidence": res.confidence,
                "duration_ms": res.duration_ms,
                "model_id": res.model_id,
                "words": [w.to_dict() for w in res.words],
            }
        )

    @app.post("/v1/models/unload")
    async def unload_models() -> JSONResponse:
        loaded_models.clear()
        return JSONResponse({"status": "unloaded", "loaded_models": []})

    return app


app = create_sidecar_app()


@dataclass(slots=True)
class VoiceSidecarSupervisor:
    """Gerenciador sob demanda do processo `orin-voice-sidecar`."""

    host: str = DEFAULT_SIDECAR_HOST
    port: int = DEFAULT_SIDECAR_PORT
    _active: bool = field(default=False, init=False)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def is_running(self) -> bool:
        return self._active

    def status(self) -> dict[str, Any]:
        return {
            "service": "voice-sidecar",
            "host": self.host,
            "port": self.port,
            "base_url": self.base_url,
            "running": self._active,
        }


def main() -> int:
    import uvicorn

    host = os.getenv("ORIN_VOICE_SIDECAR_HOST", DEFAULT_SIDECAR_HOST)
    port = int(os.getenv("ORIN_VOICE_SIDECAR_PORT", str(DEFAULT_SIDECAR_PORT)))
    uvicorn.run(
        "agentos.live_voice.sidecar:app",
        host=host,
        port=port,
        log_level=os.getenv("ORIN_LOG_LEVEL", "info"),
        access_log=False,
    )
    return 0


__all__ = [
    "DEFAULT_SIDECAR_HOST",
    "DEFAULT_SIDECAR_PORT",
    "SidecarSTTRequest",
    "SidecarTTSRequest",
    "VoiceSidecarSupervisor",
    "app",
    "create_sidecar_app",
    "main",
]

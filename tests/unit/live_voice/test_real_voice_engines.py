from __future__ import annotations

import io
import json
import wave
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from agentos.live_voice import engines
from agentos.live_voice.engines import (
    CloudCredential,
    LocalWhisperEngine,
    VoiceEngineError,
    pcm16_to_float32,
    pcm16_to_wav,
    synthesize_openai_speech,
    transcribe_openai_compatible,
)
from agentos.live_voice.local_runtime import LocalEngineRuntime
from agentos.live_voice.providers import MultiProviderSTTAdapter
from agentos.live_voice.session import register_live_voice_routes

CRED = CloudCredential("openai", "https://api.openai.test/v1", "sk-test")


def _speech_pcm(seconds: float = 0.4, sample_rate: int = 16000) -> bytes:
    t = np.linspace(0.0, seconds, int(sample_rate * seconds), dtype=np.float32)
    return (np.sin(2.0 * np.pi * 220.0 * t) * 9000).astype(np.int16).tobytes()


def test_pcm16_to_wav_is_a_valid_mono_16bit_file() -> None:
    pcm = _speech_pcm()
    with wave.open(io.BytesIO(pcm16_to_wav(pcm + b"\x01", 16000))) as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        assert wav.getnframes() == len(pcm) // 2


def test_pcm16_to_float32_resamples_to_whisper_rate() -> None:
    audio = pcm16_to_float32(_speech_pcm(0.5, 48000), 48000)
    assert audio.dtype == np.float32
    assert abs(audio.size - 8000) <= 1
    assert float(np.max(np.abs(audio))) <= 1.0


def test_stt_adapter_never_invents_a_transcript() -> None:
    result = MultiProviderSTTAdapter().transcribe(_speech_pcm(), sample_rate=16000, provider="local_sidecar")
    assert result.text == ""


def test_local_whisper_reports_install_hint_when_missing(tmp_path) -> None:
    engine = LocalWhisperEngine(LocalEngineRuntime(root=tmp_path))
    engine.importable = staticmethod(lambda: False)  # type: ignore[method-assign]
    with pytest.raises(VoiceEngineError) as caught:
        engine.transcribe(_speech_pcm(), sample_rate=16000)
    assert caught.value.code == "local_stt_unavailable"
    assert caught.value.hint == "install:whisper"


def test_openai_transcription_posts_wav_and_returns_text() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.read()
        return httpx.Response(200, json={"text": " Oi, Orin! "})

    text = transcribe_openai_compatible(CRED, _speech_pcm(), sample_rate=16000, transport=httpx.MockTransport(handler))
    assert text == "Oi, Orin!"
    assert seen["url"] == "https://api.openai.test/v1/audio/transcriptions"
    assert seen["auth"] == "Bearer sk-test"
    assert b"RIFF" in seen["body"] and b"gpt-4o-mini-transcribe" in seen["body"]


def test_openai_transcription_surfaces_provider_error() -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(VoiceEngineError) as caught:
        transcribe_openai_compatible(CRED, _speech_pcm(), sample_rate=16000, transport=transport)
    assert caught.value.code == "cloud_stt_failed"
    assert "bad key" in caught.value.message


def test_openai_speech_requests_raw_pcm_and_sanitises_voice() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.read()))
        return httpx.Response(200, content=b"\x00\x01" * 1200)

    pcm = synthesize_openai_speech(CRED, "Olá!", voice="not-a-voice", speed=9, instructions="calmo", transport=httpx.MockTransport(handler))
    assert len(pcm) == 2400
    assert seen["response_format"] == "pcm"
    assert seen["voice"] == "coral"
    assert seen["speed"] == 2.0
    assert seen["instructions"] == "calmo"


def _app(principal_for: Any = None, services: Any = None, runtime: Any = None) -> FastAPI:
    app = FastAPI()
    register_live_voice_routes(app, services=services, principal_for=principal_for, runtime=runtime)
    return app


def test_capabilities_endpoint_reports_only_what_can_run(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(LocalWhisperEngine, "importable", staticmethod(lambda: False))
    payload = TestClient(_app(runtime=LocalEngineRuntime(root=tmp_path))).get("/v1/live-voice/capabilities").json()
    assert payload["stt"]["local_sidecar"]["available"] is False
    assert payload["stt"]["local_sidecar"]["install"]["state"] == "idle"
    assert payload["stt"]["openai"]["available"] is False
    assert payload["stt"]["openrouter"]["available"] is False
    assert payload["tts"]["kokoro"]["available"] is False
    assert payload["tts"]["kokoro"]["voices"][:3] == ["pf_dora", "pm_alex", "pm_santa"]
    assert payload["tts"]["chatterbox"]["voice_clips"] == [{"id": "default", "name": "Voz padrão do Chatterbox"}]
    assert payload["recommended_stt"] in {"browser", "groq"}
    assert payload["recommended_tts"] == "browser"


def test_websocket_rejects_unauthenticated_connections() -> None:
    def deny(_request: Any) -> Any:
        raise PermissionError("no session")

    with pytest.raises(WebSocketDisconnect) as caught:
        with TestClient(_app(principal_for=deny)).websocket_connect("/v1/conversations/c1/live") as ws:
            ws.receive_json()
    assert caught.value.code == 4401


def test_websocket_validates_csrf_from_query_string() -> None:
    calls: list[tuple[str | None, str | None]] = []
    security = SimpleNamespace(
        validate_csrf=lambda _p, token, origin: calls.append((token, origin)),
        authorize=lambda *_a, **_k: None,
    )
    services = SimpleNamespace(security=security)
    principal = SimpleNamespace(user_id="u1")
    client = TestClient(_app(principal_for=lambda _r: principal, services=services))
    with client.websocket_connect("/v1/conversations/c1/live?csrf=tok-1") as ws:
        assert ws.receive_json()["type"] == "session.ready"
    assert calls == [("tok-1", None)]


def test_websocket_reports_stt_error_instead_of_fake_text(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(LocalWhisperEngine, "importable", staticmethod(lambda: False))
    with TestClient(_app(runtime=LocalEngineRuntime(root=tmp_path))).websocket_connect("/v1/conversations/c1/live") as ws:
        ws.receive_json()
        ws.send_json({"type": "config", "stt_provider": "local_sidecar"})
        ws.receive_json()
        ws.send_bytes(_speech_pcm())
        assert ws.receive_json()["type"] == "vad.frame"
        ws.send_json({"type": "audio_speech_end", "silence_ms": 700})
        assert ws.receive_json()["type"] == "stt.processing"
        error = ws.receive_json()
        assert error["type"] == "stt.error"
        assert error["code"] == "local_stt_unavailable"


def test_websocket_transcribes_real_audio_with_local_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_transcribe(self: LocalWhisperEngine, pcm: bytes, **kwargs: Any) -> str:
        captured["bytes"] = len(pcm)
        captured.update(kwargs)
        return "Qual é a previsão do tempo hoje?"

    monkeypatch.setattr(LocalWhisperEngine, "transcribe", fake_transcribe)
    pcm = _speech_pcm()
    with TestClient(_app()).websocket_connect("/v1/conversations/c1/live") as ws:
        ws.receive_json()
        ws.send_bytes(pcm)
        ws.receive_json()
        ws.send_json({"type": "audio_speech_end", "silence_ms": 700, "sample_rate": 16000})
        assert ws.receive_json()["type"] == "stt.processing"
        transcript = ws.receive_json()
        assert transcript["type"] == "stt.transcript"
        assert transcript["text"] == "Qual é a previsão do tempo hoje?"
    assert captured["bytes"] == len(pcm)
    assert captured["sample_rate"] == 16000

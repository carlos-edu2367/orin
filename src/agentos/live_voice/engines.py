"""Real speech engines behind the Live voice mode.

`local_sidecar` STT runs `faster-whisper` in-process (GPU when CTranslate2 sees
CUDA, CPU int8 otherwise). Cloud STT/TTS go through an OpenAI-compatible API
with the person's own provider key. Nothing here fabricates a transcript: an
engine that cannot run raises `VoiceEngineError` with a public, actionable
message instead.
"""
from __future__ import annotations

import importlib.util
import base64
import io
import logging
import threading
import time
import wave
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np

_LOGGER = logging.getLogger("agentos.live_voice.engines")

LOCAL_WHISPER_INSTALL_COMMAND = "uv pip install faster-whisper"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_CATALOG_TTL_SECONDS = 3600
WHISPER_SAMPLE_RATE = 16000
OPENAI_TTS_SAMPLE_RATE = 24000
CLOUD_TIMEOUT_SECONDS = 60.0

LOCAL_WHISPER_MODEL_ALIASES: dict[str, str] = {
    "faster-whisper-large-v3-turbo": "large-v3-turbo",
    "faster-whisper-small-int8": "small",
}

OPENAI_TTS_VOICES: tuple[str, ...] = (
    "alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer",
)


class VoiceEngineError(RuntimeError):
    """A speech engine could not run; `code` and `message` are safe to show the person."""

    def __init__(self, code: str, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_event(self, event_type: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": event_type, "code": self.code, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        return payload


@dataclass(frozen=True, slots=True)
class CloudCredential:
    provider: str
    base_url: str
    api_key: str


def pcm16_to_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
    aligned = pcm_bytes if len(pcm_bytes) % 2 == 0 else pcm_bytes[:-1]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes(aligned)
    return buffer.getvalue()


def pcm16_to_float32(pcm_bytes: bytes, sample_rate: int, target_rate: int = WHISPER_SAMPLE_RATE) -> np.ndarray:
    aligned = pcm_bytes if len(pcm_bytes) % 2 == 0 else pcm_bytes[:-1]
    audio = np.frombuffer(aligned, dtype=np.int16).astype(np.float32) / 32768.0
    if sample_rate == target_rate or audio.size == 0:
        return audio
    duration = audio.size / float(sample_rate)
    target_len = max(1, int(round(duration * target_rate)))
    source_x = np.linspace(0.0, duration, num=audio.size, endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
    return np.interp(target_x, source_x, audio).astype(np.float32)


def _cuda_device_count() -> int:
    try:
        import ctranslate2  # type: ignore[import-not-found]

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


class LocalWhisperEngine:
    """faster-whisper, preferably in Orin's isolated `whisper` environment.

    A source checkout that already has `faster_whisper` importable runs it
    in-process instead, which keeps development setups working unchanged.
    """

    _lock = threading.Lock()
    _models: dict[str, Any] = {}

    def __init__(self, runtime: Any = None) -> None:
        self._runtime = runtime

    def _rt(self) -> Any:
        if self._runtime is None:
            from agentos.live_voice.local_runtime import local_runtime

            self._runtime = local_runtime()
        return self._runtime

    @staticmethod
    def importable() -> bool:
        return importlib.util.find_spec("faster_whisper") is not None

    def is_installed(self) -> bool:
        return self._rt().is_installed("whisper") or self.importable()

    def device(self) -> str:
        if self._rt().is_installed("whisper"):
            return str(self._rt().installed_info("whisper").get("device") or "cpu")
        return "cuda" if _cuda_device_count() > 0 else "cpu"

    def _load_in_process(self, model_id: str) -> Any:
        name = LOCAL_WHISPER_MODEL_ALIASES.get(model_id, model_id or "large-v3-turbo")
        device = "cuda" if _cuda_device_count() > 0 else "cpu"
        key = f"{name}@{device}"
        with self._lock:
            cached = self._models.get(key)
            if cached is not None:
                return cached
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]

            try:
                model = WhisperModel(name, device=device, compute_type="float16" if device == "cuda" else "int8")
            except Exception:
                if device != "cuda":
                    raise
                _LOGGER.warning("faster-whisper could not start on CUDA; falling back to CPU", exc_info=True)
                model = WhisperModel(name, device="cpu", compute_type="int8")
            self._models[key] = model
            return model

    def transcribe(
        self,
        pcm_bytes: bytes,
        *,
        sample_rate: int,
        language: str = "pt",
        model_id: str = "",
        prompt_hint: str | None = None,
    ) -> str:
        from agentos.live_voice.local_runtime import LocalEngineError

        runtime = self._rt()
        if runtime.is_installed("whisper"):
            try:
                response = runtime.call(
                    "whisper",
                    {
                        "op": "transcribe",
                        "model": model_id,
                        "language": language,
                        "sample_rate": sample_rate,
                        "pcm_base64": base64.b64encode(pcm_bytes).decode("ascii"),
                    },
                )
            except LocalEngineError as exc:
                raise VoiceEngineError("local_stt_failed", exc.message) from exc
            return str(response.get("text") or "").strip()
        if not self.importable():
            raise VoiceEngineError(
                "local_stt_unavailable",
                "O Whisper local ainda não está instalado nesta máquina.",
                hint="install:whisper",
            )
        try:
            model = self._load_in_process(model_id)
            segments, _info = model.transcribe(
                pcm16_to_float32(pcm_bytes, sample_rate),
                language=(language or "pt").split("-")[0],
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=prompt_hint or None,
            )
            return " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            _LOGGER.warning("local whisper transcription failed", exc_info=True)
            raise VoiceEngineError(
                "local_stt_failed",
                "O Whisper local falhou ao transcrever o áudio. Veja os logs do backend.",
            ) from exc


def synthesize_local(runtime: Any, engine: str, text: str, *, voice: str, speed: float, language: str = "pt") -> tuple[bytes, int]:
    """Kokoro or Chatterbox through the isolated runtime: raw PCM16 and its sample rate."""
    from agentos.live_voice.local_runtime import LocalEngineError

    payload: dict[str, Any] = {"op": "synthesize", "text": text, "speed": speed}
    if engine == "kokoro":
        payload.update({"voice": voice or "pf_dora", "language": "pt-br" if language.startswith("pt") else language})
    else:
        clip = runtime.voice_clip_path(voice) if voice and voice != "default" else None
        payload.update({"voice_path": str(clip) if clip else "", "language": language.split("-")[0] or "pt"})
    try:
        response = runtime.call(engine, payload)
    except LocalEngineError as exc:
        code = "local_tts_unavailable" if exc.code == "local_engine_not_installed" else "local_tts_failed"
        raise VoiceEngineError(code, exc.message, hint=f"install:{engine}") from exc
    return base64.b64decode(str(response.get("pcm_base64") or "")), int(response.get("sample_rate") or 24000)


def _cloud_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"][:240]
        if isinstance(error, str):
            return error[:240]
    return f"HTTP {response.status_code}"


def transcribe_openai_compatible(
    credential: CloudCredential,
    pcm_bytes: bytes,
    *,
    sample_rate: int,
    language: str = "pt",
    model_id: str = "gpt-4o-mini-transcribe",
    prompt_hint: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> str:
    data: dict[str, str] = {
        "model": model_id,
        "language": (language or "pt").split("-")[0],
        "response_format": "json",
    }
    if prompt_hint:
        data["prompt"] = prompt_hint[:400]
    try:
        with httpx.Client(timeout=CLOUD_TIMEOUT_SECONDS, transport=transport) as client:
            response = client.post(
                f"{credential.base_url.rstrip('/')}/audio/transcriptions",
                headers={"Authorization": f"Bearer {credential.api_key}"},
                data=data,
                files={"file": ("speech.wav", pcm16_to_wav(pcm_bytes, sample_rate), "audio/wav")},
            )
    except httpx.HTTPError as exc:
        raise VoiceEngineError("cloud_stt_failed", f"Não consegui falar com {credential.provider}: {exc.__class__.__name__}.") from exc
    if response.status_code >= 400:
        raise VoiceEngineError("cloud_stt_failed", f"{credential.provider} recusou a transcrição: {_cloud_error_message(response)}")
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()
    return str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""


def synthesize_openai_speech(
    credential: CloudCredential,
    text: str,
    *,
    model_id: str = "gpt-4o-mini-tts",
    voice: str = "coral",
    speed: float = 1.0,
    instructions: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    """Return raw 24 kHz mono PCM16 for `text`."""
    body: dict[str, Any] = {
        "model": model_id,
        "input": text,
        "voice": voice if voice in OPENAI_TTS_VOICES else "coral",
        "response_format": "pcm",
        "speed": max(0.5, min(2.0, float(speed or 1.0))),
    }
    if instructions and "gpt-4o" in model_id:
        body["instructions"] = instructions[:500]
    try:
        with httpx.Client(timeout=CLOUD_TIMEOUT_SECONDS, transport=transport) as client:
            response = client.post(
                f"{credential.base_url.rstrip('/')}/audio/speech",
                headers={"Authorization": f"Bearer {credential.api_key}"},
                json=body,
            )
    except httpx.HTTPError as exc:
        raise VoiceEngineError("cloud_tts_failed", f"Não consegui falar com {credential.provider}: {exc.__class__.__name__}.") from exc
    if response.status_code >= 400:
        raise VoiceEngineError("cloud_tts_failed", f"{credential.provider} recusou a síntese de voz: {_cloud_error_message(response)}")
    if not response.content:
        raise VoiceEngineError("cloud_tts_failed", f"{credential.provider} devolveu áudio vazio.")
    return response.content




# Models that read the `instructions` field (tone, pacing); others ignore or reject it.
_INSTRUCTION_PREFIXES = ("openai/", "google/", "fish-audio/")


def _openrouter_headers(credential: CloudCredential) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {credential.api_key}",
        "HTTP-Referer": "https://github.com/carlos-edu2367/orin",
        "X-Title": "Orin",
    }


class OpenRouterVoiceCatalog:
    """Speech and transcription models with their voices, from OpenRouter's public models API."""

    def __init__(self, *, base_url: str = OPENROUTER_BASE_URL, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    def _fetch(self, modality: str) -> list[dict[str, Any]]:
        with self._lock:
            cached = self._cache.get(modality)
            if cached and time.monotonic() - cached[0] < OPENROUTER_CATALOG_TTL_SECONDS:
                return cached[1]
        try:
            with httpx.Client(timeout=20.0, transport=self._transport) as client:
                response = client.get(f"{self._base_url}/models", params={"output_modalities": modality})
            response.raise_for_status()
            raw = response.json().get("data") or []
        except (httpx.HTTPError, ValueError):
            _LOGGER.info("OpenRouter %s catalog unavailable", modality, exc_info=True)
            return cached[1] if cached else []
        models: list[dict[str, Any]] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            model_id = item["id"]
            voices = item.get("supported_voices")
            models.append(
                {
                    "id": model_id,
                    "name": str(item.get("name") or model_id),
                    "vendor": model_id.split("/", 1)[0],
                    "voices": [v for v in voices if isinstance(v, str)] if isinstance(voices, list) else [],
                    "supports_instructions": model_id.startswith(_INSTRUCTION_PREFIXES),
                    "free": model_id.endswith(":free"),
                }
            )
        models.sort(key=lambda m: (m["vendor"], m["name"]))
        with self._lock:
            self._cache[modality] = (time.monotonic(), models)
        return models

    def speech_models(self) -> list[dict[str, Any]]:
        return self._fetch("speech")

    def transcription_models(self) -> list[dict[str, Any]]:
        return self._fetch("transcription")

    def model(self, model_id: str) -> dict[str, Any] | None:
        return next((m for m in self.speech_models() if m["id"] == model_id), None)


def _pcm_rate(content_type: str, default: int = 24000) -> int:
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() in {"rate", "samplerate", "sample_rate"} and value.strip().isdigit():
            return int(value.strip())
    return default


def synthesize_openrouter_speech(
    credential: CloudCredential,
    text: str,
    *,
    model_id: str,
    voice: str | None,
    speed: float = 1.0,
    instructions: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> tuple[bytes, str, int]:
    """Return (audio bytes, encoding, sample rate); encoding is `mp3` or `pcm16`.

    MP3 is asked for first because its sample rate travels with it; models that
    only produce PCM (Gemini, HeyGen) answer 400 and are retried as 16-bit PCM.
    """
    body: dict[str, Any] = {"model": model_id, "input": text}
    if voice:
        body["voice"] = voice
    if instructions and model_id.startswith(_INSTRUCTION_PREFIXES):
        body["instructions"] = instructions[:500]
    if model_id.startswith("openai/") and abs(float(speed or 1.0) - 1.0) > 0.01:
        body["speed"] = max(0.5, min(2.0, float(speed)))
    url = f"{credential.base_url.rstrip('/')}/audio/speech"
    try:
        with httpx.Client(timeout=CLOUD_TIMEOUT_SECONDS, transport=transport) as client:
            response = client.post(url, headers=_openrouter_headers(credential), json={**body, "response_format": "mp3"})
            if response.status_code == 400:
                response = client.post(url, headers=_openrouter_headers(credential), json={**body, "response_format": "pcm"})
    except httpx.HTTPError as exc:
        raise VoiceEngineError("cloud_tts_failed", f"Não consegui falar com o OpenRouter: {exc.__class__.__name__}.") from exc
    if response.status_code >= 400:
        raise VoiceEngineError("cloud_tts_failed", f"O OpenRouter recusou a voz: {_cloud_error_message(response)}")
    if not response.content:
        raise VoiceEngineError("cloud_tts_failed", "O OpenRouter devolveu áudio vazio.")
    content_type = (response.headers.get("content-type") or "").lower()
    if "pcm" in content_type or "l16" in content_type:
        return response.content, "pcm16", _pcm_rate(content_type)
    if "wav" in content_type:
        return response.content, "wav", 0
    return response.content, "mp3", 0


def transcribe_openrouter(
    credential: CloudCredential,
    pcm_bytes: bytes,
    *,
    sample_rate: int,
    language: str = "pt",
    model_id: str = "openai/whisper-large-v3-turbo",
    transport: httpx.BaseTransport | None = None,
) -> str:
    body = {
        "model": model_id,
        "input_audio": {"data": base64.b64encode(pcm16_to_wav(pcm_bytes, sample_rate)).decode("ascii"), "format": "wav"},
        "language": (language or "pt").split("-")[0],
    }
    try:
        with httpx.Client(timeout=CLOUD_TIMEOUT_SECONDS, transport=transport) as client:
            response = client.post(
                f"{credential.base_url.rstrip('/')}/audio/transcriptions",
                headers=_openrouter_headers(credential),
                json=body,
            )
    except httpx.HTTPError as exc:
        raise VoiceEngineError("cloud_stt_failed", f"Não consegui falar com o OpenRouter: {exc.__class__.__name__}.") from exc
    if response.status_code >= 400:
        raise VoiceEngineError("cloud_stt_failed", f"O OpenRouter recusou a transcrição: {_cloud_error_message(response)}")
    try:
        payload = response.json()
    except ValueError:
        return ""
    return str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""


__all__ = [
    "CLOUD_TIMEOUT_SECONDS",
    "CloudCredential",
    "LOCAL_WHISPER_INSTALL_COMMAND",
    "LocalWhisperEngine",
    "OPENROUTER_BASE_URL",
    "OpenRouterVoiceCatalog",
    "OPENAI_TTS_SAMPLE_RATE",
    "OPENAI_TTS_VOICES",
    "VoiceEngineError",
    "pcm16_to_float32",
    "pcm16_to_wav",
    "synthesize_local",
    "synthesize_openai_speech",
    "synthesize_openrouter_speech",
    "transcribe_openrouter",
    "transcribe_openai_compatible",
]

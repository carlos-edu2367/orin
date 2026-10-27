"""Real speech engines behind the Live voice mode.

`local_sidecar` STT runs `faster-whisper` in-process (GPU when CTranslate2 sees
CUDA, CPU int8 otherwise). Cloud STT/TTS go through an OpenAI-compatible API
with the person's own provider key. Nothing here fabricates a transcript: an
engine that cannot run raises `VoiceEngineError` with a public, actionable
message instead.
"""
from __future__ import annotations

import importlib.util
import io
import logging
import threading
import wave
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np

_LOGGER = logging.getLogger("agentos.live_voice.engines")

LOCAL_WHISPER_INSTALL_COMMAND = "uv pip install faster-whisper"
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
    """Lazily loads one `faster-whisper` model per (name, device) and keeps it warm."""

    _lock = threading.Lock()
    _models: dict[str, Any] = {}

    @staticmethod
    def is_installed() -> bool:
        return importlib.util.find_spec("faster_whisper") is not None

    @staticmethod
    def preferred_device() -> str:
        return "cuda" if _cuda_device_count() > 0 else "cpu"

    def _load(self, model_id: str) -> Any:
        name = LOCAL_WHISPER_MODEL_ALIASES.get(model_id, model_id or "large-v3-turbo")
        device = self.preferred_device()
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
                # A CUDA runtime that is present but unusable (missing cuDNN,
                # out of VRAM) still leaves the CPU path available.
                _LOGGER.warning("faster-whisper could not start on CUDA; falling back to CPU", exc_info=True)
                key = f"{name}@cpu"
                model = WhisperModel(name, device="cpu", compute_type="int8")
            self._models[key] = model
            return model

    def transcribe(
        self,
        pcm_bytes: bytes,
        *,
        sample_rate: int,
        language: str = "pt",
        model_id: str = "faster-whisper-large-v3-turbo",
        prompt_hint: str | None = None,
    ) -> str:
        if not self.is_installed():
            raise VoiceEngineError(
                "local_stt_unavailable",
                "O Whisper local ainda não está instalado nesta máquina.",
                hint=LOCAL_WHISPER_INSTALL_COMMAND,
            )
        try:
            model = self._load(model_id)
            audio = pcm16_to_float32(pcm_bytes, sample_rate)
            segments, _info = model.transcribe(
                audio,
                language=(language or "pt").split("-")[0],
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=prompt_hint or None,
            )
            return " ".join(segment.text.strip() for segment in segments).strip()
        except VoiceEngineError:
            raise
        except Exception as exc:
            _LOGGER.warning("local whisper transcription failed", exc_info=True)
            raise VoiceEngineError(
                "local_stt_failed",
                "O Whisper local falhou ao transcrever o áudio. Veja os logs do backend.",
            ) from exc


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




class LocalWhisperInstaller:
    """Installs `faster-whisper` into the interpreter running Orin, one job at a time."""

    _lock = threading.Lock()

    def __init__(self) -> None:
        self.state = "installed" if LocalWhisperEngine.is_installed() else "idle"
        self.message = ""
        self._thread: threading.Thread | None = None

    def status(self) -> dict[str, Any]:
        if self.state != "running" and LocalWhisperEngine.is_installed():
            self.state = "installed"
        return {"state": self.state, "message": self.message, "command": LOCAL_WHISPER_INSTALL_COMMAND}

    @staticmethod
    def _command() -> list[str]:
        import shutil
        import sys

        uv = shutil.which("uv")
        if uv:
            return [uv, "pip", "install", "--python", sys.executable, "faster-whisper"]
        return [sys.executable, "-m", "pip", "install", "faster-whisper"]

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self.state == "running" or LocalWhisperEngine.is_installed():
                return self.status()
            self.state = "running"
            self.message = "Baixando e instalando o Whisper local…"
            self._thread = threading.Thread(target=self._run, name="orin-whisper-install", daemon=True)
            self._thread.start()
        return self.status()

    def _run(self) -> None:
        import subprocess

        try:
            proc = subprocess.run(self._command(), capture_output=True, text=True, timeout=1800, check=False)
        except Exception as exc:
            self.state, self.message = "failed", f"A instalação não pôde iniciar: {exc.__class__.__name__}."
            return
        importlib.invalidate_caches()
        if proc.returncode == 0 and LocalWhisperEngine.is_installed():
            self.state, self.message = "installed", "Whisper local instalado. O modelo é baixado na primeira fala."
        else:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
            self.state, self.message = "failed", f"A instalação falhou: {tail[0][:200]}"


__all__ = [
    "CLOUD_TIMEOUT_SECONDS",
    "CloudCredential",
    "LOCAL_WHISPER_INSTALL_COMMAND",
    "LocalWhisperEngine",
    "LocalWhisperInstaller",
    "OPENAI_TTS_SAMPLE_RATE",
    "OPENAI_TTS_VOICES",
    "VoiceEngineError",
    "pcm16_to_float32",
    "pcm16_to_wav",
    "synthesize_openai_speech",
    "transcribe_openai_compatible",
]

"""Real speech engines behind the Live voice mode.

`local_sidecar` STT runs `faster-whisper` in-process (GPU when CTranslate2 sees
CUDA, CPU int8 otherwise). Cloud STT/TTS go through an OpenAI-compatible API
with the person's own provider key. Nothing here fabricates a transcript: an
engine that cannot run raises `VoiceEngineError` with a public, actionable
message instead.
"""
from __future__ import annotations

import base64
import importlib.util
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

PTBR_DEFAULT_TTS_INSTRUCTIONS = (
    "Speak strictly in native Brazilian Portuguese (pt-BR, Brazilian accent). "
    "NEVER use European Portuguese (pt-PT), Spanish (es), or Italian (it) pronunciation, "
    "even on short phrases or cognates. "
    "Fale estritamente em português do Brasil (pt-BR) nativo, natural e fluido, com pronúncia brasileira autêntica. "
    "Nunca use sotaque de Portugal (pt-PT), espanhol nem italiano."
)

LOCAL_WHISPER_MODEL_ALIASES: dict[str, str] = {
    "faster-whisper-large-v3-turbo": "large-v3-turbo",
    "faster-whisper-small-int8": "small",
}

OPENAI_TTS_VOICES: tuple[str, ...] = (
    "alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer",
)

_HTTP_CLIENTS: dict[float, httpx.Client] = {}
_HTTP_CLIENTS_LOCK = threading.Lock()
_REAL_HTTPX_CLIENT = httpx.Client

# Models discovered at runtime to reject MP3 and require raw PCM on OpenRouter.
_OPENROUTER_PCM_ONLY_MODELS: set[str] = set()


def _get_http_client(timeout: float = CLOUD_TIMEOUT_SECONDS) -> httpx.Client:
    key = float(timeout)
    if httpx.Client is not _REAL_HTTPX_CLIENT:
        return httpx.Client(timeout=key)
    with _HTTP_CLIENTS_LOCK:
        client = _HTTP_CLIENTS.get(key)
        if client is None or client.is_closed:
            client = httpx.Client(
                timeout=key,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            )
            _HTTP_CLIENTS[key] = client
        return client


def close_http_clients() -> None:
    with _HTTP_CLIENTS_LOCK:
        clients = list(_HTTP_CLIENTS.values())
        _HTTP_CLIENTS.clear()
    for client in clients:
        try:
            client.close()
        except Exception:
            pass


def normalize_ptbr_instructions(
    instructions: str | None = None,
    *,
    language: str = "pt-BR",
    previous_text: str | None = None,
) -> str:
    raw = (instructions or "").strip()
    lang_norm = (language or "pt-BR").strip().lower()
    if not lang_norm.startswith("pt"):
        return raw

    raw_lower = raw.lower()
    has_pt_guard = "nunca use sotaque de portugal" in raw_lower
    has_es_it_guard = (
        ("espanhol" in raw_lower or "spanish" in raw_lower)
        and ("italiano" in raw_lower or "italian" in raw_lower)
    )
    if has_pt_guard and has_es_it_guard:
        combined = raw
    elif has_pt_guard:
        combined = (
            f"{raw} Speak strictly in native Brazilian Portuguese (pt-BR). "
            "Never use European Portuguese (pt-PT), Spanish (es), or Italian (it) pronunciation. "
            "Nunca use sotaque de Portugal (pt-PT), espanhol ou italiano."
        )
    elif raw:
        combined = f"{PTBR_DEFAULT_TTS_INSTRUCTIONS} {raw}"
    else:
        combined = PTBR_DEFAULT_TTS_INSTRUCTIONS

    prev = (previous_text or "").strip()
    if prev and "Contexto imediatamente anterior" not in combined:
        combined = (
            f"{combined} Contexto imediatamente anterior já falado "
            f'(mantenha o mesmo tom e ritmo): "{prev[-160:]}"'
        )
    return combined[:500]


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
        language: str = "pt-BR",
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
                        "prompt_hint": prompt_hint or "",
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


def synthesize_local(
    runtime: Any,
    engine: str,
    text: str,
    *,
    voice: str,
    speed: float,
    language: str = "pt-BR",
) -> tuple[bytes, int]:
    """Kokoro or Chatterbox through the isolated runtime: raw PCM16 and its sample rate."""
    from agentos.live_voice.local_runtime import LocalEngineError

    lang_norm = (language or "pt-BR").strip().lower()
    payload: dict[str, Any] = {"op": "synthesize", "text": text, "speed": speed}
    if engine == "kokoro":
        payload.update({"voice": voice or "pf_dora", "language": "pt-br" if lang_norm.startswith("pt") else language})
    else:
        clip = runtime.voice_clip_path(voice) if voice and voice != "default" else None
        payload.update({"voice_path": str(clip) if clip else "", "language": lang_norm.split("-")[0] or "pt"})
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
    language: str = "pt-BR",
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
    url = f"{credential.base_url.rstrip('/')}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {credential.api_key}"}
    files = {"file": ("speech.wav", pcm16_to_wav(pcm_bytes, sample_rate), "audio/wav")}
    try:
        if transport is not None:
            with httpx.Client(timeout=CLOUD_TIMEOUT_SECONDS, transport=transport) as client:
                response = client.post(url, headers=headers, data=data, files=files)
        else:
            client = _get_http_client(CLOUD_TIMEOUT_SECONDS)
            response = client.post(url, headers=headers, data=data, files=files)
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
    language: str = "pt-BR",
    instructions: str | None = None,
    previous_text: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    """Return raw 24 kHz mono PCM16 for `text`."""
    effective_model = (model_id or "gpt-4o-mini-tts").strip()
    if (language or "pt-BR").strip().lower().startswith("pt") and effective_model in {"tts-1", "tts-1-hd"}:
        effective_model = "gpt-4o-mini-tts"

    body: dict[str, Any] = {
        "model": effective_model,
        "input": text,
        "voice": voice if voice in OPENAI_TTS_VOICES else "coral",
        "response_format": "pcm",
        "speed": max(0.5, min(2.0, float(speed or 1.0))),
    }
    norm_instructions = normalize_ptbr_instructions(
        instructions,
        language=language,
        previous_text=previous_text,
    )
    if norm_instructions and "gpt-4o" in effective_model:
        body["instructions"] = norm_instructions[:500]
    url = f"{credential.base_url.rstrip('/')}/audio/speech"
    headers = {"Authorization": f"Bearer {credential.api_key}"}
    try:
        if transport is not None:
            with httpx.Client(timeout=CLOUD_TIMEOUT_SECONDS, transport=transport) as client:
                response = client.post(url, headers=headers, json=body)
        else:
            client = _get_http_client(CLOUD_TIMEOUT_SECONDS)
            response = client.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise VoiceEngineError("cloud_tts_failed", f"Não consegui falar com {credential.provider}: {exc.__class__.__name__}.") from exc
    if response.status_code >= 400:
        raise VoiceEngineError("cloud_tts_failed", f"{credential.provider} recusou a síntese de voz: {_cloud_error_message(response)}")
    if not response.content:
        raise VoiceEngineError("cloud_tts_failed", f"{credential.provider} devolveu áudio vazio.")
    return response.content


# Models that read the `instructions` field (tone, pacing, Brazilian Portuguese enforcement).
_INSTRUCTION_PREFIXES = ("openai/", "google/", "fish-audio/", "elevenlabs/", "minimax/", "cartesia/")

_PREFERRED_PTBR_SPEECH_MODELS: tuple[str, ...] = (
    "elevenlabs/eleven-multilingual-v2",
    "elevenlabs/eleven-v3",
    "elevenlabs/eleven-flash-v2.5",
    "elevenlabs/eleven-turbo-v2.5",
    "openai/gpt-4o-mini-tts",
    "google/gemini-2.5-flash-preview-tts",
)

_ENGLISH_ONLY_SPEECH_MODELS: frozenset[str] = frozenset(
    {
        "elevenlabs/eleven-flash-v2",
        "elevenlabs/eleven-turbo-v2",
        "openai/tts-1",
        "openai/tts-1-hd",
    }
)

_PTBR_OPENROUTER_MODEL_UPGRADES: dict[str, str] = {
    "openai/tts-1": "openai/gpt-4o-mini-tts",
    "openai/tts-1-hd": "openai/gpt-4o-mini-tts",
    "elevenlabs/eleven-flash-v2": "elevenlabs/eleven-flash-v2.5",
    "elevenlabs/eleven-turbo-v2": "elevenlabs/eleven-turbo-v2.5",
}


def _speech_model_sort_key(m: dict[str, Any]) -> tuple[int, int, str, str]:
    mid = str(m.get("id") or "")
    if mid in _PREFERRED_PTBR_SPEECH_MODELS:
        return (0, _PREFERRED_PTBR_SPEECH_MODELS.index(mid), m["vendor"], m["name"])
    if mid in _ENGLISH_ONLY_SPEECH_MODELS:
        return (3, 0, m["vendor"], m["name"])
    if m.get("supports_instructions"):
        return (1, 0, m["vendor"], m["name"])
    return (2, 0, m["vendor"], m["name"])


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
            if self._transport is not None:
                with httpx.Client(timeout=20.0, transport=self._transport) as client:
                    response = client.get(f"{self._base_url}/models", params={"output_modalities": modality})
            else:
                client = _get_http_client(20.0)
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
        if modality == "speech":
            models.sort(key=_speech_model_sort_key)
        else:
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
    language: str = "pt-BR",
    instructions: str | None = None,
    previous_text: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> tuple[bytes, str, int]:
    """Return (audio bytes, encoding, sample rate); encoding is `mp3` or `pcm16`.

    MP3 is asked for first because its sample rate travels with it; models that
    only produce PCM (Gemini, HeyGen) answer 400 and are retried as 16-bit PCM
    and remembered in `_OPENROUTER_PCM_ONLY_MODELS`.
    """
    lang_norm = (language or "pt-BR").strip().lower()
    effective_model = (model_id or "").strip()
    if lang_norm.startswith("pt") and effective_model in _PTBR_OPENROUTER_MODEL_UPGRADES:
        effective_model = _PTBR_OPENROUTER_MODEL_UPGRADES[effective_model]

    minimal_body: dict[str, Any] = {"model": effective_model, "input": text}
    if voice:
        minimal_body["voice"] = voice
    if effective_model.startswith("openai/") and abs(float(speed or 1.0) - 1.0) > 0.01:
        minimal_body["speed"] = max(0.5, min(2.0, float(speed)))

    norm_instructions = normalize_ptbr_instructions(
        instructions,
        language=language,
        previous_text=previous_text,
    )
    instructed_body: dict[str, Any] = dict(minimal_body)
    if norm_instructions:
        instructed_body["instructions"] = norm_instructions[:500]

    body: dict[str, Any] = dict(instructed_body)
    if effective_model.startswith("elevenlabs/"):
        if lang_norm.startswith("pt"):
            body["language_code"] = "pt"
        if previous_text and previous_text.strip():
            body["previous_text"] = previous_text.strip()[:300]

    preferred_fmt = "pcm" if effective_model in _OPENROUTER_PCM_ONLY_MODELS else "mp3"
    url = f"{credential.base_url.rstrip('/')}/audio/speech"
    headers = _openrouter_headers(credential)

    def _post_with_body_cascade(client: httpx.Client, fmt: str) -> httpx.Response:
        resp = client.post(url, headers=headers, json={**body, "response_format": fmt})
        if resp.status_code not in (400, 422):
            return resp
        err_lower = _cloud_error_message(resp).lower()
        is_format_err = any(tok in err_lower for tok in ("pcm", "mp3", "response_format", "format"))
        if fmt == "mp3" and is_format_err:
            return resp
        if body != instructed_body:
            resp = client.post(url, headers=headers, json={**instructed_body, "response_format": fmt})
            if resp.status_code not in (400, 422):
                return resp
            err_lower = _cloud_error_message(resp).lower()
            if fmt == "mp3" and any(tok in err_lower for tok in ("pcm", "mp3", "response_format", "format")):
                return resp
        if instructed_body != minimal_body:
            resp = client.post(url, headers=headers, json={**minimal_body, "response_format": fmt})
        return resp

    def _run_requests(client: httpx.Client) -> httpx.Response:
        resp = _post_with_body_cascade(client, preferred_fmt)
        if resp.status_code in (400, 422) and preferred_fmt == "mp3":
            resp = _post_with_body_cascade(client, "pcm")
            if resp.status_code < 400:
                _OPENROUTER_PCM_ONLY_MODELS.add(effective_model)
        return resp

    try:
        if transport is not None:
            with httpx.Client(timeout=CLOUD_TIMEOUT_SECONDS, transport=transport) as client:
                response = _run_requests(client)
        else:
            client = _get_http_client(CLOUD_TIMEOUT_SECONDS)
            response = _run_requests(client)
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
    language: str = "pt-BR",
    model_id: str = "openai/whisper-large-v3-turbo",
    prompt_hint: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> str:
    body: dict[str, Any] = {
        "model": model_id,
        "input_audio": {"data": base64.b64encode(pcm16_to_wav(pcm_bytes, sample_rate)).decode("ascii"), "format": "wav"},
        "language": (language or "pt").split("-")[0],
    }
    if prompt_hint:
        body["prompt"] = prompt_hint[:400]
    url = f"{credential.base_url.rstrip('/')}/audio/transcriptions"
    headers = _openrouter_headers(credential)
    try:
        if transport is not None:
            with httpx.Client(timeout=CLOUD_TIMEOUT_SECONDS, transport=transport) as client:
                response = client.post(url, headers=headers, json=body)
        else:
            client = _get_http_client(CLOUD_TIMEOUT_SECONDS)
            response = client.post(url, headers=headers, json=body)
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
    "PTBR_DEFAULT_TTS_INSTRUCTIONS",
    "VoiceEngineError",
    "_OPENROUTER_PCM_ONLY_MODELS",
    "_get_http_client",
    "close_http_clients",
    "normalize_ptbr_instructions",
    "pcm16_to_float32",
    "pcm16_to_wav",
    "synthesize_local",
    "synthesize_openai_speech",
    "synthesize_openrouter_speech",
    "transcribe_openrouter",
    "transcribe_openai_compatible",
]

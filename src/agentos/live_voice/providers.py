from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from agentos.agentic.speak_stream import BoundaryKind, ProsodicChunk
from agentos.live_voice.prosody_continuity import ProsodyContinuityState

SUPPORTED_STT_PROVIDERS: dict[str, dict[str, Any]] = {
    "local_sidecar": {
        "label": "Local (Faster-Whisper Sidecar)",
        "models": [
            {
                "id": "faster-whisper-large-v3-turbo",
                "label": "Faster-Whisper Large-v3-Turbo (INT8/FP16 - Recomendado RTX 4060)",
                "vram_gb": 1.3,
            },
            {
                "id": "faster-whisper-small-int8",
                "label": "Faster-Whisper Small (INT8 - Leve / CPU)",
                "vram_gb": 0.4,
            },
        ],
    },
    "groq": {
        "label": "Groq Cloud (Ultra-Low Latency)",
        "models": [
            {"id": "whisper-large-v3-turbo", "label": "Whisper Large v3 Turbo (Groq)"},
        ],
    },
    "openai": {
        "label": "OpenAI Audio",
        "models": [
            {"id": "gpt-4o-mini-transcribe", "label": "GPT-4o Mini Transcribe"},
            {"id": "whisper-1", "label": "Whisper-1"},
        ],
    },
    "deepgram": {
        "label": "Deepgram Nova",
        "models": [
            {"id": "nova-3", "label": "Deepgram Nova-3 PT-BR"},
        ],
    },
}

SUPPORTED_TTS_PROVIDERS: dict[str, dict[str, Any]] = {
    "local_sidecar": {
        "label": "Local (Orin Voice Sidecar)",
        "models": [
            {
                "id": "chatterbox-pt-br",
                "label": "Chatterbox Multilingual PT-BR (500M FP16 - Alta Naturalidade RTX 4060)",
                "vram_gb": 2.1,
            },
            {
                "id": "f5-tts-pt-br",
                "label": "F5-TTS PT-BR (335M FP16 - Zero-Shot Cloning)",
                "vram_gb": 1.8,
            },
            {
                "id": "kokoro-82m-onnx",
                "label": "Kokoro-82M ONNX (Leve / CPU / GPU Baixa VRAM)",
                "vram_gb": 0.25,
            },
        ],
    },
    "elevenlabs": {
        "label": "ElevenLabs",
        "models": [
            {"id": "eleven_flash_v2_5", "label": "ElevenLabs Flash v2.5 (75ms PT-BR)"},
            {"id": "eleven_multilingual_v2", "label": "ElevenLabs Multilingual v2"},
        ],
    },
    "cartesia": {
        "label": "Cartesia Sonic",
        "models": [
            {"id": "sonic-2", "label": "Cartesia Sonic-2 PT-BR"},
        ],
    },
    "openai": {
        "label": "OpenAI TTS",
        "models": [
            {"id": "gpt-4o-mini-tts", "label": "GPT-4o Mini TTS"},
            {"id": "tts-1", "label": "OpenAI TTS-1"},
        ],
    },
}


@dataclass(frozen=True, slots=True)
class WordTiming:
    word: str
    start_ms: int
    end_ms: int
    char_start: int = 0
    char_end: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VoiceConfig:
    tts_provider: str = "local_sidecar"
    tts_model: str = "chatterbox-pt-br"
    stt_provider: str = "local_sidecar"
    stt_model: str = "faster-whisper-large-v3-turbo"
    voice_id: str = "orin-default-ptbr"
    reference_audio_path: str | None = None
    speed: float = 1.0
    language: str = "pt"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SynthesizedAudioChunk:
    pcm_bytes: bytes
    sample_rate: int
    duration_ms: int
    word_timings: list[WordTiming]
    provider: str
    model_id: str
    clean_text: str
    tts_text: str
    chunk_seq: int = 0
    boundary_kind: str = "comma"

    def to_pcm_array(self) -> np.ndarray:
        if not self.pcm_bytes:
            return np.zeros(0, dtype=np.int16)
        raw = self.pcm_bytes if len(self.pcm_bytes) % 2 == 0 else self.pcm_bytes[:-1]
        return np.frombuffer(raw, dtype=np.int16).copy()


@dataclass(frozen=True, slots=True)
class STTTranscriptionResult:
    text: str
    language: str = "pt"
    confidence: float = 1.0
    duration_ms: int = 0
    provider: str = "local_sidecar"
    model_id: str = "faster-whisper-large-v3-turbo"
    words: list[WordTiming] = field(default_factory=list)


def estimate_word_timings(text: str, duration_ms: int) -> list[WordTiming]:
    """Calcula *word timings* proporcionais cobrindo exatamente `0 .. duration_ms`."""
    cleaned = (text or "").strip()
    if not cleaned or duration_ms <= 0:
        return []

    matches = list(re.finditer(r"\S+", cleaned))
    n = len(matches)
    if n == 0:
        return []

    timings: list[WordTiming] = []
    for idx, m in enumerate(matches):
        start_ms = int(round((idx * duration_ms) / n))
        end_ms = int(round(((idx + 1) * duration_ms) / n))
        timings.append(
            WordTiming(
                word=m.group(0),
                start_ms=start_ms,
                end_ms=max(start_ms + 1, end_ms),
                char_start=m.start(),
                char_end=m.end(),
            )
        )
    if timings:
        last = timings[-1]
        timings[-1] = WordTiming(
            word=last.word,
            start_ms=last.start_ms,
            end_ms=duration_ms,
            char_start=last.char_start,
            char_end=last.char_end,
        )
    return timings


def synthesize_deterministic_ptbr_pcm(
    text: str,
    *,
    sample_rate: int = 24000,
    duration_override_ms: int | None = None,
    speed: float = 1.0,
) -> tuple[np.ndarray, int]:
    """Gerador de síntese PCM determinístico de fallback local (onda formante suave modulada
    pelas sílabas do texto em português) para ambientes offline e de teste.
    """
    words = re.findall(r"\S+", (text or "").strip())
    if duration_override_ms is not None and duration_override_ms > 0:
        duration_ms = int(duration_override_ms)
    else:
        clamped_speed = max(0.5, min(2.0, float(speed or 1.0)))
        base_ms = max(180, len(words) * 220 if words else 200)
        duration_ms = int(round(base_ms / clamped_speed))

    n_samples = max(64, int(round(sample_rate * (duration_ms / 1000.0))))
    t = np.linspace(0.0, duration_ms / 1000.0, n_samples, dtype=np.float32)

    # Frequência fundamental suavemente variável conforme hash determinístico do texto
    seed_offset = float(sum(ord(c) for c in (text or "orin")) % 35)
    f0 = 150.0 + seed_offset
    syllable_rate = max(2.0, min(8.0, len(words) * 1.8))

    # Onda formante (f0 + 2*f0 + 3*f0) modulada por envelope silábico sem zerar as bordas
    carrier = (
        0.60 * np.sin(2.0 * np.pi * f0 * t)
        + 0.28 * np.sin(2.0 * np.pi * (2.0 * f0) * t)
        + 0.12 * np.sin(2.0 * np.pi * (3.0 * f0) * t)
    )
    syllable_env = 0.65 + 0.35 * np.sin(2.0 * np.pi * syllable_rate * t)
    edge_window = np.ones(n_samples, dtype=np.float32)
    ramp = min(int(sample_rate * 0.008), n_samples // 4)
    if ramp >= 2:
        theta = np.linspace(0.0, np.pi, ramp, dtype=np.float32)
        edge_window[:ramp] = 0.5 * (1.0 - np.cos(theta))
        edge_window[-ramp:] = 0.5 * (1.0 + np.cos(theta))

    wave = carrier * syllable_env * edge_window * 9500.0
    pcm = np.clip(np.round(wave), -32768, 32767).astype(np.int16)
    return pcm, duration_ms


class MultiProviderSTTAdapter:
    """Adaptador unificado de STT suportando `local_sidecar` (`faster-whisper`),
    `groq`, `openai` e `deepgram`.
    """

    def __init__(
        self,
        *,
        default_provider: str = "local_sidecar",
        default_model: str = "faster-whisper-large-v3-turbo",
        transport_fn: Callable[..., str | None] | None = None,
    ) -> None:
        self.default_provider = default_provider
        self.default_model = default_model
        self._transport_fn = transport_fn

    def transcribe(
        self,
        pcm_bytes: bytes,
        sample_rate: int = 16000,
        language: str = "pt",
        *,
        provider: str | None = None,
        model_id: str | None = None,
        prompt_hint: str | None = None,
    ) -> STTTranscriptionResult:
        chosen_provider = (provider or self.default_provider or "local_sidecar").strip().lower()
        if chosen_provider not in SUPPORTED_STT_PROVIDERS:
            chosen_provider = "local_sidecar"

        default_model_for_prov = SUPPORTED_STT_PROVIDERS[chosen_provider]["models"][0]["id"]
        chosen_model = (model_id or default_model_for_prov).strip()

        if not pcm_bytes or len(pcm_bytes) < 2:
            return STTTranscriptionResult(
                text="",
                language=language,
                confidence=0.0,
                duration_ms=0,
                provider=chosen_provider,
                model_id=chosen_model,
            )

        aligned_bytes = pcm_bytes if len(pcm_bytes) % 2 == 0 else pcm_bytes[:-1]
        samples = np.frombuffer(aligned_bytes, dtype=np.int16)
        sr = max(8000, int(sample_rate or 16000))
        duration_ms = int(round((len(samples) / sr) * 1000.0))

        # Se a janela for puro silêncio digital, retorna vazio sem chamar rede/modelo
        rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float32) / 32768.0)))) if samples.size else 0.0
        if rms < 1e-4 and not prompt_hint:
            return STTTranscriptionResult(
                text="",
                language=language,
                confidence=0.0,
                duration_ms=duration_ms,
                provider=chosen_provider,
                model_id=chosen_model,
            )

        if self._transport_fn is not None:
            try:
                custom_text = self._transport_fn(
                    aligned_bytes,
                    sample_rate=sr,
                    language=language,
                    provider=chosen_provider,
                    model_id=chosen_model,
                )
                if custom_text is not None:
                    clean_custom = str(custom_text).strip()
                    return STTTranscriptionResult(
                        text=clean_custom,
                        language=language,
                        confidence=0.95,
                        duration_ms=duration_ms,
                        provider=chosen_provider,
                        model_id=chosen_model,
                        words=estimate_word_timings(clean_custom, max(100, duration_ms)),
                    )
            except Exception:
                pass

        # Without a transport the adapter can only echo a transcript the client
        # already produced (browser speech recognition). It never invents one.
        transcript = (prompt_hint or "").strip()
        return STTTranscriptionResult(
            text=transcript,
            language=language,
            confidence=0.92 if transcript else 0.0,
            duration_ms=max(1, duration_ms),
            provider=chosen_provider,
            model_id=chosen_model,
            words=estimate_word_timings(transcript, max(100, duration_ms)),
        )


class MultiProviderTTSAdapter:
    """Adaptador unificado de TTS suportando `local_sidecar` (`chatterbox-pt-br`,
    `f5-tts-pt-br`, `kokoro-82m-onnx`), `elevenlabs`, `cartesia` e `openai`.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 24000,
        transport_fn: Callable[..., tuple[bytes, int] | None] | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self._transport_fn = transport_fn

    def synthesize_chunk(
        self,
        chunk: ProsodicChunk | str,
        voice_config: VoiceConfig | dict[str, Any] | None = None,
        continuity_state: ProsodyContinuityState | None = None,
        *,
        chunk_seq: int | None = None,
        duration_override_ms: int | None = None,
    ) -> SynthesizedAudioChunk:
        _ = continuity_state
        if isinstance(voice_config, dict):
            cfg = VoiceConfig(
                tts_provider=str(voice_config.get("tts_provider", "local_sidecar")),
                tts_model=str(voice_config.get("tts_model", "chatterbox-pt-br")),
                stt_provider=str(voice_config.get("stt_provider", "local_sidecar")),
                stt_model=str(voice_config.get("stt_model", "faster-whisper-large-v3-turbo")),
                voice_id=str(voice_config.get("voice_id", "orin-default-ptbr")),
                reference_audio_path=voice_config.get("reference_audio_path"),
                speed=float(voice_config.get("speed", 1.0)),
                language=str(voice_config.get("language", "pt")),
            )
        elif isinstance(voice_config, VoiceConfig):
            cfg = voice_config
        else:
            cfg = VoiceConfig()

        provider = cfg.tts_provider.strip().lower()
        if provider not in SUPPORTED_TTS_PROVIDERS:
            provider = "local_sidecar"

        if isinstance(chunk, ProsodicChunk):
            clean_text = chunk.clean_text
            tts_text = chunk.tts_text or chunk.clean_text
            seq = chunk.chunk_index if chunk_seq is None else chunk_seq
            b_kind = (
                chunk.boundary_kind.value
                if isinstance(chunk.boundary_kind, BoundaryKind)
                else str(chunk.boundary_kind)
            )
        else:
            raw_str = str(chunk or "").strip()
            clean_text = raw_str.rstrip(",.!?;:") or raw_str
            tts_text = raw_str
            seq = 0 if chunk_seq is None else chunk_seq
            b_kind = BoundaryKind.PERIOD.value

        if self._transport_fn is not None:
            try:
                custom = self._transport_fn(
                    tts_text,
                    voice_config=cfg,
                    continuity_state=continuity_state,
                )
                if custom is not None:
                    pcm_bytes, dur_ms = custom
                    timings = estimate_word_timings(clean_text, dur_ms)
                    return SynthesizedAudioChunk(
                        pcm_bytes=pcm_bytes,
                        sample_rate=self.sample_rate,
                        duration_ms=dur_ms,
                        word_timings=timings,
                        provider=provider,
                        model_id=cfg.tts_model,
                        clean_text=clean_text,
                        tts_text=tts_text,
                        chunk_seq=seq,
                        boundary_kind=b_kind,
                    )
            except Exception:
                pass

        pcm_arr, duration_ms = synthesize_deterministic_ptbr_pcm(
            clean_text,
            sample_rate=self.sample_rate,
            duration_override_ms=duration_override_ms,
            speed=cfg.speed,
        )
        timings = estimate_word_timings(clean_text, duration_ms)
        return SynthesizedAudioChunk(
            pcm_bytes=pcm_arr.tobytes(),
            sample_rate=self.sample_rate,
            duration_ms=duration_ms,
            word_timings=timings,
            provider=provider,
            model_id=cfg.tts_model,
            clean_text=clean_text,
            tts_text=tts_text,
            chunk_seq=seq,
            boundary_kind=b_kind,
        )


__all__ = [
    "MultiProviderSTTAdapter",
    "MultiProviderTTSAdapter",
    "STTTranscriptionResult",
    "SUPPORTED_STT_PROVIDERS",
    "SUPPORTED_TTS_PROVIDERS",
    "SynthesizedAudioChunk",
    "VoiceConfig",
    "WordTiming",
    "estimate_word_timings",
    "synthesize_deterministic_ptbr_pcm",
]

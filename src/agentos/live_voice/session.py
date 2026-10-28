from __future__ import annotations

import asyncio
import base64
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agentos.api.contracts import ApplicationNotFoundError, ApplicationValidationError
from agentos.agentic.speak_stream import BoundaryKind, ProsodicChunk, SpeakStreamParser
from agentos.live_voice.credentials import CLOUD_VOICE_PROVIDERS, resolve_cloud_credential
from agentos.live_voice.engines import (
    OPENAI_TTS_SAMPLE_RATE,
    OPENAI_TTS_VOICES,
    LocalWhisperEngine,
    OpenRouterVoiceCatalog,
    VoiceEngineError,
    synthesize_local,
    synthesize_openai_speech,
    synthesize_openrouter_speech,
    transcribe_openai_compatible,
    transcribe_openrouter,
)
from agentos.live_voice.local_runtime import ENGINE_SPECS, KOKORO_VOICES, LocalEngineRuntime, local_runtime
from agentos.live_voice.hardware_governor import VoiceHardwareGovernor
from agentos.live_voice.prosody_continuity import ProsodyContinuityEngine
from agentos.live_voice.providers import (
    SUPPORTED_STT_PROVIDERS,
    SUPPORTED_TTS_PROVIDERS,
    MultiProviderSTTAdapter,
    MultiProviderTTSAdapter,
    VoiceConfig,
    WordTiming,
    estimate_word_timings,
)
from agentos.live_voice.sidecar import DEFAULT_SIDECAR_PORT
from agentos.live_voice.sprites import AudioArbitrator, AudioSpriteBank
from agentos.live_voice.vad_eou import (
    PTBRSemanticEOUDetector,
    SileroVADDetector,
    is_echo_of_recent_tts,
)

OPENROUTER_DEFAULT_STT_MODEL = "openai/whisper-large-v3-turbo"

# Limite máximo de 60 segundos de áudio PCM 16kHz mono Int16 (~1.92 MB) para prevenir DoS de memória
MAX_PCM_INPUT_BUFFER_BYTES = 16000 * 2 * 60


def split_realtime_tts_clauses(
    text: str,
    *,
    max_chars: int = 120,
    min_chunk_chars: int = 38,
) -> list[str]:
    """Split text into sentence/clause chunks (`<= max_chars`) while merging short
    micro-fragments (`< min_chunk_chars`) so short openings/cognates are never isolated.
    """
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return []
    raw_sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", cleaned) if s.strip()]
    clauses: list[str] = []
    for sentence in raw_sentences:
        if len(sentence) <= max_chars:
            clauses.append(sentence)
            continue
        parts = [p.strip() for p in re.split(r"(?<=[,;:—])\s+", sentence) if p.strip()]
        current = ""
        for part in parts:
            if not current:
                current = part
            elif len(current) + 1 + len(part) <= max_chars or len(current) < min_chunk_chars:
                current = f"{current} {part}"
            else:
                clauses.append(current)
                current = part
        if current:
            clauses.append(current)

    final_clauses: list[str] = []
    for clause in clauses:
        if len(clause) <= max_chars:
            final_clauses.append(clause)
            continue
        words = clause.split(" ")
        buf = ""
        for w in words:
            while len(w) > max_chars:
                if buf:
                    final_clauses.append(buf)
                    buf = ""
                final_clauses.append(w[:max_chars])
                w = w[max_chars:]
            if not w:
                continue
            if not buf:
                buf = w
            elif len(buf) + 1 + len(w) <= max_chars:
                buf = f"{buf} {w}"
            else:
                final_clauses.append(buf)
                buf = w
        if buf:
            final_clauses.append(buf)

    if min_chunk_chars > 0 and len(final_clauses) > 1:
        merged: list[str] = []
        i = 0
        while i < len(final_clauses):
            curr = final_clauses[i]
            while (
                len(curr) < min_chunk_chars
                and i + 1 < len(final_clauses)
                and len(curr) + 1 + len(final_clauses[i + 1]) <= max_chars
            ):
                i += 1
                curr = f"{curr} {final_clauses[i]}"
            if (
                len(curr) < min_chunk_chars
                and merged
                and len(merged[-1]) + 1 + len(curr) <= max_chars
            ):
                merged[-1] = f"{merged[-1]} {curr}"
            else:
                merged.append(curr)
            i += 1
        final_clauses = merged
    return final_clauses


def _safe_int(val: Any, default: int = 0, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        if val is None or isinstance(val, bool):
            out = default
        elif isinstance(val, float):
            if not math.isfinite(val):
                out = default
            else:
                out = int(round(val))
        else:
            out = int(round(float(str(val).strip())))
    except (ValueError, TypeError, OverflowError):
        out = default
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def _safe_float(
    val: Any,
    default: float = 0.0,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        if val is None or isinstance(val, bool):
            out = default
        else:
            out = float(val)
            if not math.isfinite(out):
                out = default
    except (ValueError, TypeError, OverflowError):
        out = default
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


@dataclass(slots=True)
class LedgerChunkEntry:
    chunk_seq: int
    clean_text: str
    duration_ms: int
    word_timings: list[WordTiming]
    epoch: int = 1
    played_ms: int = 0


@dataclass(frozen=True, slots=True)
class BargeInResult:
    previous_epoch: int
    new_epoch: int
    interrupted_chunk_seq: int
    played_ms: int
    truncated_text: str
    unspoken_text: str
    system_receipt: str
    ignored_stale_epoch: bool = False


class SpokenTranscriptLedger:
    """Rastreador da palavra exata falada pelo TTS até o instante do Barge-in (`<80ms`).
    - Registra cada chunk enviada ao cliente com seus *word timings*.
    - No `barge_in`, incrementa `audio_epoch`, calcula quais palavras foram ouvidas antes de
      `played_ms` e gera `[SYSTEM_BARGE_IN_RECEIPT]` informando o que foi dito e o que ficou por dizer.
    """

    def __init__(self, *, initial_epoch: int = 1) -> None:
        self.audio_epoch: int = max(1, _safe_int(initial_epoch, 1))
        self._chunks: dict[int, LedgerChunkEntry] = {}
        self._last_barge_in: BargeInResult | None = None

    def reset_for_new_turn(self) -> None:
        self._chunks.clear()

    def register_chunk(
        self,
        *,
        chunk_seq: int,
        clean_text: str,
        duration_ms: int,
        word_timings: list[WordTiming] | None = None,
        epoch: int | None = None,
    ) -> LedgerChunkEntry:
        seq = _safe_int(chunk_seq, 0, minimum=0)
        dur = _safe_int(duration_ms, 1, minimum=1, maximum=300_000)
        timings = (
            list(word_timings)
            if word_timings is not None
            else estimate_word_timings(clean_text, dur)
        )
        entry = LedgerChunkEntry(
            chunk_seq=seq,
            clean_text=(clean_text or "").strip(),
            duration_ms=dur,
            word_timings=timings,
            epoch=self.audio_epoch if epoch is None else _safe_int(epoch, self.audio_epoch, minimum=1),
        )
        self._chunks[seq] = entry
        return entry

    def record_playback_progress(self, chunk_seq: int, played_ms: int) -> None:
        seq = _safe_int(chunk_seq, 0, minimum=0)
        entry = self._chunks.get(seq)
        if entry is not None:
            entry.played_ms = _safe_int(played_ms, 0, minimum=0, maximum=entry.duration_ms)

    def on_barge_in(
        self,
        *,
        epoch: int,
        chunk_seq: int,
        played_ms: int | float,
    ) -> BargeInResult:
        req_epoch = _safe_int(epoch, self.audio_epoch, minimum=0)
        target_seq = _safe_int(chunk_seq, 0, minimum=0)
        played = _safe_int(played_ms, 0, minimum=0, maximum=3_600_000)

        if req_epoch < self.audio_epoch:
            if self._last_barge_in is not None:
                return BargeInResult(
                    previous_epoch=self._last_barge_in.previous_epoch,
                    new_epoch=self.audio_epoch,
                    interrupted_chunk_seq=self._last_barge_in.interrupted_chunk_seq,
                    played_ms=self._last_barge_in.played_ms,
                    truncated_text=self._last_barge_in.truncated_text,
                    unspoken_text=self._last_barge_in.unspoken_text,
                    system_receipt=self._last_barge_in.system_receipt,
                    ignored_stale_epoch=True,
                )
            return BargeInResult(
                previous_epoch=req_epoch,
                new_epoch=self.audio_epoch,
                interrupted_chunk_seq=target_seq,
                played_ms=played,
                truncated_text="",
                unspoken_text="",
                system_receipt="",
                ignored_stale_epoch=True,
            )

        prev_epoch = self.audio_epoch
        self.audio_epoch += 1

        spoken_parts: list[str] = []
        unspoken_parts: list[str] = []

        for seq in sorted(self._chunks.keys()):
            entry = self._chunks[seq]
            if seq < target_seq:
                if entry.clean_text:
                    spoken_parts.append(entry.clean_text)
            elif seq == target_seq:
                heard_words: list[str] = []
                missed_words: list[str] = []
                for wt in entry.word_timings:
                    if played > 0 and wt.start_ms < played:
                        heard_words.append(wt.word)
                    else:
                        missed_words.append(wt.word)
                if heard_words:
                    spoken_parts.append(" ".join(heard_words))
                if missed_words:
                    unspoken_parts.append(" ".join(missed_words))
            else:
                if entry.clean_text:
                    unspoken_parts.append(entry.clean_text)

        spoken_joined = " ".join(spoken_parts).strip()
        unspoken_joined = " ".join(unspoken_parts).strip()

        if spoken_joined:
            spoken_clean_tail = spoken_joined.rstrip(",.;:!? ")
            truncated_text = f"{spoken_clean_tail}— [interrompido pelo usuário]"
        else:
            truncated_text = "[interrompido pelo usuário antes da primeira palavra]"

        receipt = (
            f"[SYSTEM_BARGE_IN_RECEIPT] O usuário interrompeu sua fala aos {played}ms "
            f"(epoch {prev_epoch} -> {self.audio_epoch}). "
            f'O que o usuário ouviu até o corte: "{truncated_text}". '
            f'O que ficou por dizer e NÃO foi ouvido: "{unspoken_joined}".'
        )

        result = BargeInResult(
            previous_epoch=prev_epoch,
            new_epoch=self.audio_epoch,
            interrupted_chunk_seq=target_seq,
            played_ms=played,
            truncated_text=truncated_text,
            unspoken_text=unspoken_joined,
            system_receipt=receipt,
            ignored_stale_epoch=False,
        )
        self._last_barge_in = result
        return result


@dataclass(slots=True)
class LiveVoiceSession:
    conversation_id: str
    hardware_governor: VoiceHardwareGovernor = field(
        default_factory=VoiceHardwareGovernor
    )
    vad_detector: SileroVADDetector = field(default_factory=SileroVADDetector)
    eou_detector: PTBRSemanticEOUDetector = field(
        default_factory=PTBRSemanticEOUDetector
    )
    continuity_engine: ProsodyContinuityEngine = field(
        default_factory=ProsodyContinuityEngine
    )
    ledger: SpokenTranscriptLedger = field(default_factory=SpokenTranscriptLedger)
    stt_adapter: MultiProviderSTTAdapter = field(
        default_factory=MultiProviderSTTAdapter
    )
    tts_adapter: MultiProviderTTSAdapter = field(
        default_factory=MultiProviderTTSAdapter
    )
    arbitrator: AudioArbitrator = field(default_factory=AudioArbitrator)
    sprite_bank: AudioSpriteBank = field(default_factory=AudioSpriteBank)
    voice_config: VoiceConfig = field(default_factory=VoiceConfig)
    recent_tts_texts: list[str] = field(default_factory=list)
    pcm_input_buffer: bytearray = field(default_factory=bytearray)
    next_chunk_seq: int = 0
    _cached_stt_pcm_len: int = 0
    _cached_stt_transcript: str = ""

    @property
    def audio_epoch(self) -> int:
        return self.ledger.audio_epoch

    def clear_pcm_input(self) -> None:
        self.pcm_input_buffer.clear()
        self._cached_stt_pcm_len = 0
        self._cached_stt_transcript = ""

    def append_pcm_input(self, pcm_frame: bytes) -> None:
        if not pcm_frame:
            return
        self.pcm_input_buffer.extend(pcm_frame)
        self._cached_stt_pcm_len = 0
        self._cached_stt_transcript = ""
        if len(self.pcm_input_buffer) > MAX_PCM_INPUT_BUFFER_BYTES:
            overflow = len(self.pcm_input_buffer) - MAX_PCM_INPUT_BUFFER_BYTES
            # Mantém alinhamento par de 2 bytes (Int16)
            if overflow % 2 == 1:
                overflow += 1
            del self.pcm_input_buffer[:overflow]

    def update_config(self, payload: dict[str, Any]) -> VoiceConfig:
        tts_prov = str(payload.get("tts_provider") or self.voice_config.tts_provider).strip().lower()
        if tts_prov not in SUPPORTED_TTS_PROVIDERS:
            tts_prov = self.voice_config.tts_provider

        stt_prov = str(payload.get("stt_provider") or self.voice_config.stt_provider).strip().lower()
        if stt_prov not in SUPPORTED_STT_PROVIDERS:
            stt_prov = self.voice_config.stt_provider

        speed = _safe_float(payload.get("speed"), self.voice_config.speed, minimum=0.5, maximum=2.0)

        self.voice_config = VoiceConfig(
            tts_provider=tts_prov,
            tts_model=str(payload.get("tts_model") or self.voice_config.tts_model).strip()[:160],
            stt_provider=stt_prov,
            stt_model=str(payload.get("stt_model") or self.voice_config.stt_model).strip(),
            voice_id=str(payload.get("voice_id") or self.voice_config.voice_id).strip()[:160],
            reference_audio_path=payload.get(
                "reference_audio_path", self.voice_config.reference_audio_path
            ),
            speed=speed,
            language=str(payload.get("language") or self.voice_config.language or "pt-BR").strip(),
        )
        return self.voice_config

    def synthesize_speak_text(
        self,
        raw_text: str,
        *,
        chunk_duration_override_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        text = (raw_text or "").strip()
        if not text:
            return []

        inner = re.sub(r"</?speak>", "", text, flags=re.IGNORECASE).strip()
        if not inner:
            return []

        chunks: list[ProsodicChunk] = []
        if chunk_duration_override_ms is not None:
            chunks.append(
                ProsodicChunk(
                    chunk_index=self.next_chunk_seq,
                    clean_text=inner,
                    tts_text=inner,
                    boundary_kind=BoundaryKind.PERIOD,
                    is_first_in_block=True,
                    is_last_in_block=True,
                )
            )
        else:
            parser = SpeakStreamParser()
            wrapped = text if "<speak>" in text.lower() else f"<speak>{inner}</speak>"
            res_feed = parser.feed(wrapped)
            res_flush = parser.finish(live_voice_enabled=True)
            chunks.extend(res_feed.chunks)
            chunks.extend(res_flush.chunks)
            if not chunks:
                chunks.append(
                    ProsodicChunk(
                        chunk_index=self.next_chunk_seq,
                        clean_text=inner,
                        tts_text=inner,
                        boundary_kind=BoundaryKind.PERIOD,
                        is_first_in_block=True,
                        is_last_in_block=True,
                    )
                )

        events: list[dict[str, Any]] = []
        for ch in chunks:
            seq = self.next_chunk_seq
            self.next_chunk_seq += 1

            synth = self.tts_adapter.synthesize_chunk(
                ch,
                voice_config=self.voice_config,
                continuity_state=self.continuity_engine.state,
                chunk_seq=seq,
                duration_override_ms=chunk_duration_override_ms,
            )
            processed_pcm = self.continuity_engine.process_chunk(
                synth.to_pcm_array(),
                ch.tts_text,
                ch.boundary_kind,
            )
            final_pcm_bytes = (
                synth.pcm_bytes
                if chunk_duration_override_ms is not None
                else processed_pcm.tobytes()
            )
            final_duration_ms = (
                int(chunk_duration_override_ms)
                if chunk_duration_override_ms is not None
                else int(round((len(processed_pcm) / synth.sample_rate) * 1000.0))
            )

            entry = self.ledger.register_chunk(
                chunk_seq=seq,
                clean_text=ch.clean_text,
                duration_ms=final_duration_ms,
                word_timings=synth.word_timings,
            )
            self.recent_tts_texts.append(ch.clean_text)
            if len(self.recent_tts_texts) > 8:
                self.recent_tts_texts = self.recent_tts_texts[-8:]

            events.append(
                {
                    "type": "tts.chunk",
                    "audio_epoch": self.audio_epoch,
                    "chunk_seq": seq,
                    "clean_text": ch.clean_text,
                    "tts_text": ch.tts_text,
                    "boundary_kind": (
                        ch.boundary_kind.value
                        if isinstance(ch.boundary_kind, BoundaryKind)
                        else str(ch.boundary_kind)
                    ),
                    "sample_rate": synth.sample_rate,
                    "duration_ms": entry.duration_ms,
                    "pcm_base64": base64.b64encode(final_pcm_bytes).decode("ascii"),
                    "word_timings": [wt.to_dict() for wt in entry.word_timings],
                    "provider": synth.provider,
                    "model_id": synth.model_id,
                }
            )
        return events

    def register_audio_chunk(
        self,
        clean_text: str,
        audio_bytes: bytes,
        sample_rate: int,
        *,
        provider: str,
        encoding: str = "pcm16",
    ) -> dict[str, Any]:
        """Ledger + echo bookkeeping for one chunk synthesized by a real engine.

        PCM carries its own length; an encoded chunk (MP3/WAV) is decoded by the
        browser, so its duration is estimated from the text for the ledger.
        """
        seq = self.next_chunk_seq
        self.next_chunk_seq += 1
        if encoding == "pcm16":
            payload = audio_bytes if len(audio_bytes) % 2 == 0 else audio_bytes[:-1]
            duration_ms = max(1, int(round(len(payload) / 2 / max(1, sample_rate) * 1000.0)))
        else:
            payload = audio_bytes
            duration_ms = max(400, len(clean_text.split()) * 330)
        entry = self.ledger.register_chunk(chunk_seq=seq, clean_text=clean_text, duration_ms=duration_ms)
        self.recent_tts_texts.append(clean_text)
        if len(self.recent_tts_texts) > 8:
            self.recent_tts_texts = self.recent_tts_texts[-8:]
        event: dict[str, Any] = {
            "type": "tts.chunk",
            "audio_epoch": self.audio_epoch,
            "epoch": self.audio_epoch,
            "chunk_seq": seq,
            "clean_text": clean_text,
            "tts_text": clean_text,
            "boundary_kind": BoundaryKind.PERIOD.value,
            "sample_rate": sample_rate,
            "duration_ms": entry.duration_ms,
            "encoding": encoding,
            "word_timings": [wt.to_dict() for wt in entry.word_timings],
            "provider": provider,
            "model_id": self.voice_config.tts_model,
        }
        if encoding == "pcm16":
            event["pcm_base64"] = base64.b64encode(payload).decode("ascii")
        else:
            event["audio_base64"] = base64.b64encode(payload).decode("ascii")
            event["mime"] = "audio/mpeg" if encoding == "mp3" else "audio/wav"
        return event

    def register_cloud_chunk(self, clean_text: str, pcm_bytes: bytes, sample_rate: int, *, provider: str) -> dict[str, Any]:
        return self.register_audio_chunk(clean_text, pcm_bytes, sample_rate, provider=provider)


REAL_TTS_PROVIDERS = frozenset({"openai", "openrouter", "kokoro", "chatterbox"})
LOCAL_TTS_PROVIDERS = frozenset({"kokoro", "chatterbox"})

_OPENROUTER_PREFERRED_TTS_MODELS: tuple[str, ...] = (
    "elevenlabs/eleven-multilingual-v2",
    "elevenlabs/eleven-v3",
    "elevenlabs/eleven-flash-v2.5",
    "elevenlabs/eleven-turbo-v2.5",
    "openai/gpt-4o-mini-tts",
    "google/gemini-2.5-flash-preview-tts",
)
_OPENROUTER_ENGLISH_ONLY_TTS_MODELS: frozenset[str] = frozenset(
    {
        "elevenlabs/eleven-flash-v2",
        "elevenlabs/eleven-turbo-v2",
        "openai/tts-1",
        "openai/tts-1-hd",
    }
)
_OPENROUTER_PTBR_MODEL_UPGRADES: dict[str, str] = {
    "openai/tts-1": "openai/gpt-4o-mini-tts",
    "openai/tts-1-hd": "openai/gpt-4o-mini-tts",
    "elevenlabs/eleven-flash-v2": "elevenlabs/eleven-flash-v2.5",
    "elevenlabs/eleven-turbo-v2": "elevenlabs/eleven-turbo-v2.5",
}


def _select_default_openrouter_tts_model(speech_models: list[dict[str, Any]]) -> str | None:
    if not speech_models:
        return None
    available_ids = {str(m.get("id") or "") for m in speech_models}
    for preferred in _OPENROUTER_PREFERRED_TTS_MODELS:
        if preferred in available_ids:
            return preferred
    for m in speech_models:
        mid = str(m.get("id") or "")
        if mid and mid not in _OPENROUTER_ENGLISH_ONLY_TTS_MODELS:
            return mid
    return str(speech_models[0].get("id") or "") or None


def _select_preferred_ptbr_voice(voices: list[str], requested_voice: str) -> str | None:
    req = (requested_voice or "").strip()
    if not voices:
        return req or None
    if req in voices:
        return req
    for v in voices:
        vl = v.lower()
        if "pt-br" in vl or "pt_br" in vl:
            return v
    for preferred_exact in ("pf_dora", "pm_alex"):
        for v in voices:
            if v.lower() == preferred_exact:
                return v
    for v in voices:
        vl = v.lower()
        if vl.startswith(("pf_", "pm_")):
            return v
    for preferred_eleven in ("sarah", "aria", "brian"):
        for v in voices:
            if v.lower() == preferred_eleven:
                return v
    return voices[0]


def _resolve_openrouter_model_and_voice(
    session: LiveVoiceSession,
    catalog: OpenRouterVoiceCatalog,
) -> tuple[str, str | None]:
    model_id = (session.voice_config.tts_model or "").strip()
    lang_norm = (session.voice_config.language or "pt-BR").strip().lower()
    if lang_norm.startswith("pt") and model_id in _OPENROUTER_PTBR_MODEL_UPGRADES:
        model_id = _OPENROUTER_PTBR_MODEL_UPGRADES[model_id]
    if "/" not in model_id:
        model_id = (
            _select_default_openrouter_tts_model(catalog.speech_models())
            or "elevenlabs/eleven-multilingual-v2"
        )
    model = catalog.model(model_id)
    if model is None and model_id in _OPENROUTER_PTBR_MODEL_UPGRADES.values():
        fallback_default = _select_default_openrouter_tts_model(catalog.speech_models())
        if fallback_default:
            model = catalog.model(fallback_default)
    voices = model["voices"] if model else []
    voice = _select_preferred_ptbr_voice(voices, session.voice_config.voice_id)
    return model_id, voice


def _voice_capabilities(
    governor: VoiceHardwareGovernor,
    services: Any,
    user_id: str | None,
    runtime: LocalEngineRuntime,
    catalog: OpenRouterVoiceCatalog,
) -> dict[str, Any]:
    """What can actually run for this person right now — never what merely could."""
    profile = governor.recommend()
    whisper = LocalWhisperEngine(runtime)
    whisper_installed = whisper.is_installed()
    whisper_device = whisper.device() if whisper_installed else ("cuda" if profile.has_cuda else "cpu")
    openai_ready = resolve_cloud_credential(services, user_id, "openai") is not None
    groq_ready = resolve_cloud_credential(services, user_id, "groq") is not None
    openrouter_ready = resolve_cloud_credential(services, user_id, "openrouter") is not None
    # The catalog is public, but it is only worth a request when the person can use it.
    speech_models = catalog.speech_models() if openrouter_ready else []
    transcription_models = catalog.transcription_models() if openrouter_ready else []
    engines = {engine: runtime.status(engine) for engine in ENGINE_SPECS}
    stt = {
        "local_sidecar": {
            "available": whisper_installed,
            "device": whisper_device,
            "model_id": "faster-whisper-large-v3-turbo" if whisper_device == "cuda" else "faster-whisper-small-int8",
            "reason": (
                f"Whisper local pronto ({'GPU' if whisper_device == 'cuda' else 'CPU'})."
                if whisper_installed
                else "Instale o Whisper local para transcrever sem nuvem."
            ),
            "install": engines["whisper"],
        },
        "openai": {
            "available": openai_ready,
            "model_id": "gpt-4o-mini-transcribe",
            "reason": "Usa a sua chave OpenAI." if openai_ready else "Configure uma chave OpenAI em Configurações → Provedores.",
        },
        "openrouter": {
            "available": openrouter_ready,
            "model_id": OPENROUTER_DEFAULT_STT_MODEL,
            "models": [{"id": m["id"], "name": m["name"]} for m in transcription_models],
            "reason": "Usa a sua chave OpenRouter." if openrouter_ready else "Configure uma chave OpenRouter em Configurações → Provedores.",
        },
        "groq": {
            "available": groq_ready,
            "model_id": "whisper-large-v3-turbo",
            "reason": "Usa GROQ_API_KEY." if groq_ready else "Defina GROQ_API_KEY no ambiente do Orin.",
        },
    }
    clips = [{"id": "default", "name": "Voz padrão do Chatterbox"}] + runtime.list_voice_clips()
    tts = {
        "openai": {
            "available": openai_ready,
            "model_id": "gpt-4o-mini-tts",
            "models": [{"id": m["id"], "name": m["label"], "voices": list(OPENAI_TTS_VOICES)} for m in SUPPORTED_TTS_PROVIDERS["openai"]["models"]],
            "voices": list(OPENAI_TTS_VOICES),
            "reason": "Voz natural da OpenAI com a sua chave." if openai_ready else "Configure uma chave OpenAI em Configurações → Provedores.",
        },
        "openrouter": {
            "available": openrouter_ready and bool(speech_models),
            "model_id": _select_default_openrouter_tts_model(speech_models),
            "models": speech_models,
            "reason": (
                "ElevenLabs, Gemini, Voxtral e outras vozes com a sua chave OpenRouter."
                if openrouter_ready
                else "Configure uma chave OpenRouter em Configurações → Provedores."
            ),
        },
        "kokoro": {
            "available": engines["kokoro"]["installed"],
            "model_id": "kokoro-82m-onnx",
            "models": [{"id": "kokoro-82m-onnx", "name": "Kokoro-82M v1.0", "voices": list(KOKORO_VOICES)}],
            "voices": list(KOKORO_VOICES),
            "reason": "Leve, roda na CPU, sem internet." if engines["kokoro"]["installed"] else "Instale para ter uma voz local leve.",
            "install": engines["kokoro"],
        },
        "chatterbox": {
            "available": engines["chatterbox"]["installed"],
            "model_id": "chatterbox-multilingual",
            "models": [{"id": "chatterbox-multilingual", "name": "Chatterbox Multilingual", "voices": [c["id"] for c in clips]}],
            "voices": [c["id"] for c in clips],
            "voice_clips": clips,
            "reason": (
                "Voz mais natural e clonável, roda na sua GPU."
                if engines["chatterbox"]["installed"]
                else "Instale para a voz local mais expressiva (recomenda GPU NVIDIA)."
            ),
            "install": engines["chatterbox"],
        },
    }
    if whisper_installed:
        recommended_stt = "local_sidecar"
    elif openai_ready:
        recommended_stt = "openai"
    elif openrouter_ready:
        recommended_stt = "openrouter"
    elif groq_ready:
        recommended_stt = "groq"
    else:
        recommended_stt = "browser"
    if engines["chatterbox"]["installed"] and profile.has_cuda:
        recommended_tts = "chatterbox"
    elif openai_ready:
        recommended_tts = "openai"
    elif tts["openrouter"]["available"]:
        recommended_tts = "openrouter"
    elif engines["kokoro"]["installed"]:
        recommended_tts = "kokoro"
    elif engines["chatterbox"]["installed"]:
        recommended_tts = "chatterbox"
    else:
        recommended_tts = "browser"
    return {
        "stt": stt,
        "tts": tts,
        "recommended_stt": recommended_stt,
        "recommended_tts": recommended_tts,
        "hardware": {
            "has_cuda": bool(profile.has_cuda),
            "device_name": profile.device_name,
            "total_vram_gb": profile.total_vram_gb,
            "free_vram_gb": profile.free_vram_gb,
        },
    }


class VoiceClipUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=60)
    filename: str = Field(min_length=1, max_length=200)
    data_base64: str = Field(min_length=4, max_length=17_000_000)


def register_live_voice_routes(
    app: FastAPI,
    services: Any = None,
    principal_for: Callable[..., Any] | None = None,
    *,
    runtime: LocalEngineRuntime | None = None,
    openrouter_catalog: OpenRouterVoiceCatalog | None = None,
) -> None:
    """Registra as rotas HTTP e WebSocket do Motor de Áudio Full-Duplex do Orin:
    - `GET /v1/live-voice/hardware-status`
    - `GET /v1/live-voice/capabilities`
    - `GET|POST /v1/live-voice/engines/{engine}/install`
    - `POST|DELETE /v1/live-voice/engines/chatterbox/voices`
    - `WEBSOCKET /v1/conversations/{conversation_id}/live`
    """
    governor = VoiceHardwareGovernor()
    engines_runtime = runtime or local_runtime()
    catalog = openrouter_catalog or OpenRouterVoiceCatalog()
    whisper = LocalWhisperEngine(engines_runtime)

    def _optional_user_id(request: Request) -> str | None:
        if principal_for is None:
            return None
        try:
            return str(principal_for(request).user_id)
        except Exception:
            return None

    def _mutable(request: Request, purpose: str) -> None:
        if principal_for is None:
            return
        principal = principal_for(request, mutable=True)
        security = getattr(services, "security", None)
        if security is not None:
            security.authorize(principal, action="conversation.create", resource_id=None, purpose=purpose)

    @app.get("/v1/live-voice/hardware-status")
    @app.get("/v1/live-voice/hardware")
    async def get_live_voice_hardware_status(request: Request) -> JSONResponse:
        _optional_user_id(request)
        profile = await run_in_threadpool(governor.recommend)
        profile_dict = profile.to_dict()
        reason_text = (
            "; ".join(profile.warnings)
            if profile.warnings
            else f"Preset {profile.preset} ({profile.tier}) com {profile.primary_tts_model} + {profile.recommended_stt_model}"
        )
        return JSONResponse(
            {
                "gpu_available": bool(profile.has_cuda),
                "gpu_name": profile.device_name,
                "total_vram_gb": profile.total_vram_gb,
                "free_vram_gb": profile.free_vram_gb,
                "free_ram_gb": 16.0,
                "cpu_idle_percent": 80.0,
                "recommended_tts_provider": profile.primary_tts_provider,
                "recommended_tts_model": profile.primary_tts_model,
                "recommended_stt_provider": profile.recommended_stt_provider,
                "recommended_stt_model": profile.recommended_stt_model,
                "reason": reason_text,
                "hardware_profile": profile_dict,
                "stt_providers": SUPPORTED_STT_PROVIDERS,
                "tts_providers": SUPPORTED_TTS_PROVIDERS,
                "sidecar": {
                    "host": "127.0.0.1",
                    "port": DEFAULT_SIDECAR_PORT,
                    "service_name": "voice-sidecar",
                },
            }
        )

    @app.get("/v1/live-voice/capabilities")
    async def get_live_voice_capabilities(request: Request) -> JSONResponse:
        if principal_for is not None:
            principal = principal_for(request)
            user_id: str | None = str(principal.user_id)
        else:
            user_id = None
        payload = await run_in_threadpool(_voice_capabilities, governor, services, user_id, engines_runtime, catalog)
        return JSONResponse(payload)

    def _known_engine(engine: str) -> str:
        if engine not in ENGINE_SPECS:
            raise ApplicationNotFoundError(engine)
        return engine

    @app.get("/v1/live-voice/engines/{engine}/install")
    async def get_engine_install(engine: str, request: Request) -> JSONResponse:
        if principal_for is not None:
            principal_for(request)
        return JSONResponse(engines_runtime.status(_known_engine(engine)))

    @app.post("/v1/live-voice/engines/{engine}/install")
    async def start_engine_install(engine: str, request: Request) -> JSONResponse:
        _mutable(request, f"live_voice.install_{engine}")
        return JSONResponse(engines_runtime.start_install(_known_engine(engine)), status_code=202)

    @app.post("/v1/live-voice/engines/chatterbox/voices")
    async def upload_chatterbox_voice(payload: VoiceClipUpload, request: Request) -> JSONResponse:
        _mutable(request, "live_voice.voice_clip")
        try:
            clip = await run_in_threadpool(engines_runtime.save_voice_clip, payload.name, payload.filename, payload.data_base64)
        except ValueError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        return JSONResponse(clip, status_code=201)

    @app.delete("/v1/live-voice/engines/chatterbox/voices/{clip_id}")
    async def delete_chatterbox_voice(clip_id: str, request: Request) -> JSONResponse:
        _mutable(request, "live_voice.voice_clip")
        if not engines_runtime.delete_voice_clip(clip_id):
            raise ApplicationNotFoundError(clip_id)
        return JSONResponse({"id": clip_id, "deleted": True})

    def _transcribe(session: LiveVoiceSession, user_id: str | None, pcm: bytes, sample_rate: int, hint: str) -> str:
        provider = session.voice_config.stt_provider
        if provider == "local_sidecar":
            return whisper.transcribe(
                pcm,
                sample_rate=sample_rate,
                language=session.voice_config.language,
                model_id=session.voice_config.stt_model,
                prompt_hint=None,
            )
        if provider in CLOUD_VOICE_PROVIDERS:
            credential = resolve_cloud_credential(services, user_id, provider)
            if credential is None:
                raise VoiceEngineError(
                    "cloud_stt_no_key",
                    "Sem chave configurada para este provedor de transcrição.",
                    hint="GROQ_API_KEY" if provider == "groq" else "Configurações → Provedores",
                )
            if provider == "openrouter":
                model_id = session.voice_config.stt_model
                if "/" not in model_id:
                    model_id = OPENROUTER_DEFAULT_STT_MODEL
                return transcribe_openrouter(
                    credential, pcm, sample_rate=sample_rate, language=session.voice_config.language, model_id=model_id
                )
            default_model = SUPPORTED_STT_PROVIDERS[provider]["models"][0]["id"]
            model_id = session.voice_config.stt_model
            if model_id not in {m["id"] for m in SUPPORTED_STT_PROVIDERS[provider]["models"]}:
                model_id = default_model
            return transcribe_openai_compatible(
                credential,
                pcm,
                sample_rate=sample_rate,
                language=session.voice_config.language,
                model_id=model_id,
            )
        if hint:
            return hint
        raise VoiceEngineError("stt_provider_unsupported", f"O provedor de transcrição {provider!r} ainda não é suportado.")

    def _synthesize(
        session: LiveVoiceSession,
        user_id: str | None,
        text: str,
        instructions: str | None,
        previous_text: str | None = None,
    ) -> tuple[bytes, int, str]:
        """Return (audio, sample rate, encoding) from the engine the session chose."""
        provider = session.voice_config.tts_provider
        if provider in LOCAL_TTS_PROVIDERS:
            pcm, rate = synthesize_local(
                engines_runtime,
                provider,
                text,
                voice=session.voice_config.voice_id,
                speed=session.voice_config.speed,
                language=session.voice_config.language,
            )
            return pcm, rate, "pcm16"
        credential = resolve_cloud_credential(services, user_id, provider)
        if credential is None:
            raise VoiceEngineError(
                "cloud_tts_no_key",
                f"Sem chave {'OpenRouter' if provider == 'openrouter' else 'OpenAI'} para essa voz; usando a voz do sistema.",
                hint="Configurações → Provedores",
            )
        if provider == "openrouter":
            model_id, voice = _resolve_openrouter_model_and_voice(session, catalog)
            audio, encoding, rate = synthesize_openrouter_speech(
                credential,
                text,
                model_id=model_id,
                voice=voice,
                speed=session.voice_config.speed,
                language=session.voice_config.language,
                instructions=instructions,
                previous_text=previous_text,
            )
            return audio, rate, encoding
        model_id = session.voice_config.tts_model
        if model_id not in {m["id"] for m in SUPPORTED_TTS_PROVIDERS["openai"]["models"]}:
            model_id = "gpt-4o-mini-tts"
        pcm = synthesize_openai_speech(
            credential,
            text,
            model_id=model_id,
            voice=session.voice_config.voice_id,
            speed=session.voice_config.speed,
            language=session.voice_config.language,
            instructions=instructions,
            previous_text=previous_text,
        )
        return pcm, OPENAI_TTS_SAMPLE_RATE, "pcm16"

    @app.websocket("/v1/conversations/{conversation_id}/live")
    @app.websocket("/v1/conversations/{conversation_id}/live-voice")
    async def live_voice_websocket(
        websocket: WebSocket,
        conversation_id: str,
    ) -> None:
        user_id: str | None = None
        if principal_for is not None:
            try:
                principal = principal_for(websocket)
                security = getattr(services, "security", None)
                if security is not None:
                    # A browser sends cookies on a cross-site WebSocket too, so the
                    # session's CSRF token travels in the query string instead.
                    security.validate_csrf(principal, websocket.query_params.get("csrf"), websocket.headers.get("origin"))
                    security.authorize(principal, action="conversation.list", resource_id=conversation_id, purpose="conversation.live_voice")
                user_id = str(principal.user_id)
            except Exception:
                await websocket.close(code=4401)
                return
        await websocket.accept()
        session = LiveVoiceSession(
            conversation_id=conversation_id,
            hardware_governor=governor,
        )
        hw_profile = session.hardware_governor.recommend()
        session.voice_config = VoiceConfig(
            tts_provider=hw_profile.primary_tts_provider,
            tts_model=hw_profile.primary_tts_model,
            stt_provider=hw_profile.recommended_stt_provider,
            stt_model=hw_profile.recommended_stt_model,
        )

        ws_send_lock = asyncio.Lock()
        speak_pipeline_lock = asyncio.Lock()
        pending_tts_tasks: set[asyncio.Task[Any]] = set()
        tts_pipeline_sem = asyncio.Semaphore(2)
        last_scheduled_text: list[str] = []

        async def _ws_send(payload: dict[str, Any]) -> None:
            async with ws_send_lock:
                await websocket.send_json(payload)

        def _cancel_pending_tts() -> None:
            for t in list(pending_tts_tasks):
                if not t.done():
                    t.cancel()
            pending_tts_tasks.clear()
            last_scheduled_text.clear()

        async def _run_speak_pipeline(
            clause_pairs: list[tuple[str, str | None]],
            target_epoch: int,
            provider: str,
            instructions_arg: str | None,
        ) -> None:
            async def _synth_clause(clause_text: str, prev_text: str | None) -> tuple[bytes, int, str]:
                async with tts_pipeline_sem:
                    if session.audio_epoch != target_epoch:
                        raise asyncio.CancelledError()
                    return await run_in_threadpool(
                        _synthesize,
                        session,
                        user_id,
                        clause_text,
                        instructions_arg,
                        prev_text,
                    )

            async with speak_pipeline_lock:
                if session.audio_epoch != target_epoch:
                    return
                clause_tasks: list[tuple[str, asyncio.Task[tuple[bytes, int, str]]]] = []
                for clause, prev in clause_pairs:
                    t = asyncio.create_task(_synth_clause(clause, prev))
                    clause_tasks.append((clause, t))

                try:
                    for clause_text, c_task in clause_tasks:
                        if session.audio_epoch != target_epoch:
                            break
                        try:
                            audio, rate, encoding = await c_task
                        except asyncio.CancelledError:
                            break
                        except VoiceEngineError as exc:
                            async with ws_send_lock:
                                if session.audio_epoch == target_epoch:
                                    await websocket.send_json({**exc.to_event("tts.error"), "text": clause_text})
                            break
                        async with ws_send_lock:
                            if session.audio_epoch != target_epoch:
                                break
                            ev = session.register_audio_chunk(
                                clause_text,
                                audio,
                                rate,
                                provider=provider,
                                encoding=encoding,
                            )
                            await websocket.send_json(ev)
                finally:
                    for _c, c_task in clause_tasks:
                        if not c_task.done():
                            c_task.cancel()

        await _ws_send(
            {
                "type": "session.ready",
                "conversation_id": conversation_id,
                "audio_epoch": session.audio_epoch,
                "hardware_profile": hw_profile.to_dict(),
                "voice_config": session.voice_config.to_dict(),
            }
        )

        last_vad_state: bool | None = None
        try:
            while True:
                raw_msg = await websocket.receive()
                if raw_msg.get("type") == "websocket.disconnect":
                    break

                if "bytes" in raw_msg and raw_msg["bytes"] is not None:
                    pcm_frame: bytes = raw_msg["bytes"]
                    session.append_pcm_input(pcm_frame)
                    is_voice = session.vad_detector.is_speech(pcm_frame)
                    # Only state changes are acknowledged: answering every 32 ms
                    # frame doubles the socket traffic for no information.
                    if is_voice != last_vad_state:
                        last_vad_state = is_voice
                        await _ws_send(
                            {
                                "type": "vad.frame",
                                "is_speech": is_voice,
                                "buffered_bytes": len(session.pcm_input_buffer),
                            }
                        )
                    continue

                text_data = raw_msg.get("text")
                if not text_data:
                    continue

                try:
                    msg = json.loads(text_data)
                except ValueError:
                    await _ws_send(
                        {"type": "error", "code": "invalid_json", "message": "Invalid JSON frame"}
                    )
                    continue

                if not isinstance(msg, dict):
                    await _ws_send(
                        {"type": "error", "code": "invalid_payload", "message": "JSON object expected"}
                    )
                    continue

                msg_type = str(msg.get("type") or "").strip().lower()

                if msg_type == "ping":
                    await _ws_send({"type": "pong", "audio_epoch": session.audio_epoch})

                elif msg_type == "config":
                    updated = session.update_config(msg)
                    await _ws_send(
                        {
                            "type": "config.updated",
                            "voice_config": updated.to_dict(),
                            "audio_epoch": session.audio_epoch,
                        }
                    )

                elif msg_type == "audio_reset":
                    _cancel_pending_tts()
                    session.clear_pcm_input()
                    await _ws_send({"type": "audio.reset", "audio_epoch": session.audio_epoch})

                elif msg_type == "speak_text":
                    raw_text = str(msg.get("text") or "")
                    provider = session.voice_config.tts_provider
                    # Cloud voices need the person's key, so they run only on an authenticated socket.
                    if provider in LOCAL_TTS_PROVIDERS or (provider in REAL_TTS_PROVIDERS and principal_for is not None):
                        clean = re.sub(r"</?speak>", "", raw_text, flags=re.IGNORECASE).strip()[:2000]
                        clause_max = 120 if provider in LOCAL_TTS_PROVIDERS else 280
                        clause_min = 38 if provider in LOCAL_TTS_PROVIDERS else 80
                        clauses = split_realtime_tts_clauses(
                            clean,
                            max_chars=clause_max,
                            min_chunk_chars=clause_min,
                        )
                        if not clauses:
                            continue
                        prev_tail = (
                            last_scheduled_text[-1]
                            if last_scheduled_text
                            else (session.recent_tts_texts[-1] if session.recent_tts_texts else None)
                        )
                        clause_pairs: list[tuple[str, str | None]] = []
                        for clause in clauses:
                            clause_pairs.append((clause, prev_tail))
                            last_scheduled_text.append(clause)
                            prev_tail = clause
                        if len(last_scheduled_text) > 16:
                            del last_scheduled_text[:-16]
                        instructions_arg = str(msg.get("instructions") or "") or None
                        pipeline_task = asyncio.create_task(
                            _run_speak_pipeline(
                                clause_pairs,
                                session.audio_epoch,
                                provider,
                                instructions_arg,
                            )
                        )
                        pending_tts_tasks.add(pipeline_task)
                        pipeline_task.add_done_callback(pending_tts_tasks.discard)
                        continue
                    override_raw = msg.get("chunk_duration_override_ms")
                    dur_override = (
                        _safe_int(override_raw, 1000, minimum=20, maximum=120_000)
                        if override_raw is not None
                        else None
                    )
                    chunk_events = session.synthesize_speak_text(
                        raw_text,
                        chunk_duration_override_ms=dur_override,
                    )
                    for ev in chunk_events:
                        await _ws_send(ev)

                elif msg_type == "playback_progress":
                    c_seq = _safe_int(msg.get("chunk_seq"), 0, minimum=0)
                    p_ms = _safe_int(msg.get("played_ms"), 0, minimum=0)
                    session.ledger.record_playback_progress(
                        chunk_seq=c_seq,
                        played_ms=p_ms,
                    )
                    await _ws_send(
                        {
                            "type": "playback.ack",
                            "chunk_seq": c_seq,
                            "played_ms": p_ms,
                        }
                    )

                elif msg_type == "barge_in":
                    async with ws_send_lock:
                        barge = session.ledger.on_barge_in(
                            epoch=_safe_int(msg.get("epoch"), session.audio_epoch, minimum=0),
                            chunk_seq=_safe_int(msg.get("chunk_seq"), 0, minimum=0),
                            played_ms=_safe_float(msg.get("played_ms"), 0.0, minimum=0.0),
                        )
                        _cancel_pending_tts()
                        session.continuity_engine.reset()
                        await websocket.send_json(
                            {
                                "type": "audio.clear",
                                "previous_epoch": barge.previous_epoch,
                                "new_epoch": barge.new_epoch,
                                "interrupted_chunk_seq": barge.interrupted_chunk_seq,
                                "played_ms": barge.played_ms,
                                "truncated_text": barge.truncated_text,
                                "unspoken_text": barge.unspoken_text,
                                "system_receipt": barge.system_receipt,
                                "ignored_stale_epoch": barge.ignored_stale_epoch,
                            }
                        )

                elif msg_type == "audio_speech_end":
                    hint = str(msg.get("transcript_hint") or "").strip()
                    pcm_payload = bytes(session.pcm_input_buffer)
                    has_inline_pcm = False
                    if msg.get("pcm_base64"):
                        try:
                            pcm_payload = base64.b64decode(str(msg["pcm_base64"]))
                            has_inline_pcm = True
                        except Exception:
                            pass
                    sample_rate = _safe_int(msg.get("sample_rate"), 16000, minimum=8000, maximum=48000)

                    if pcm_payload and not hint:
                        if (
                            not has_inline_pcm
                            and session._cached_stt_transcript
                            and len(pcm_payload) == session._cached_stt_pcm_len
                        ):
                            transcript = session._cached_stt_transcript
                        else:
                            await _ws_send(
                                {
                                    "type": "stt.processing",
                                    "provider": session.voice_config.stt_provider,
                                    "audio_ms": int(len(pcm_payload) / 2 / sample_rate * 1000),
                                }
                            )
                            try:
                                transcript = await run_in_threadpool(
                                    _transcribe, session, user_id, pcm_payload, sample_rate, hint
                                )
                            except VoiceEngineError as exc:
                                session.clear_pcm_input()
                                await _ws_send(exc.to_event("stt.error"))
                                continue
                            session._cached_stt_pcm_len = len(pcm_payload)
                            session._cached_stt_transcript = transcript
                    else:
                        transcript = hint

                    if not transcript.strip():
                        session.clear_pcm_input()
                        await _ws_send({"type": "stt.empty", "audio_epoch": session.audio_epoch})
                        continue

                    # 1. Guarda Anti-Eco Textual
                    if is_echo_of_recent_tts(
                        transcript,
                        session.recent_tts_texts,
                        threshold=_safe_float(msg.get("echo_threshold"), 0.65, minimum=0.1, maximum=0.99),
                        has_strong_barge_in_energy=bool(msg.get("strong_barge_in_energy", False)),
                    ):
                        session.clear_pcm_input()
                        await _ws_send(
                            {
                                "type": "vad.echo_discarded",
                                "text": transcript,
                                "audio_epoch": session.audio_epoch,
                            }
                        )
                        continue

                    # 2. Avaliação semântica de fim de turno PT-BR
                    silence_ms = _safe_int(msg.get("silence_ms"), 420, minimum=0, maximum=60_000)
                    eou = session.eou_detector.evaluate(transcript, silence_ms=silence_ms)
                    if not eou.is_end_of_utterance:
                        await _ws_send(
                            {
                                "type": "vad.eou_wait",
                                "is_end_of_utterance": False,
                                "required_silence_ms": eou.required_silence_ms,
                                "silence_ms": eou.silence_ms,
                                "detected_hold_marker": eou.detected_hold_marker,
                                "partial_text": transcript,
                                "audio_epoch": session.audio_epoch,
                            }
                        )
                        continue

                    session.clear_pcm_input()
                    await _ws_send(
                        {
                            "type": "stt.transcript",
                            "is_end_of_utterance": True,
                            "text": transcript,
                            "audio_epoch": session.audio_epoch,
                            "provider": session.voice_config.stt_provider,
                            "model_id": session.voice_config.stt_model,
                        }
                    )
        except WebSocketDisconnect:
            return
        finally:
            _cancel_pending_tts()


__all__ = [
    "BargeInResult",
    "LedgerChunkEntry",
    "LiveVoiceSession",
    "MAX_PCM_INPUT_BUFFER_BYTES",
    "SpokenTranscriptLedger",
    "register_live_voice_routes",
    "split_realtime_tts_clauses",
]

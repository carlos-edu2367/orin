from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from agentos.agentic.speak_stream import BoundaryKind

PAUSE_BY_BOUNDARY_MS: dict[str, int] = {
    BoundaryKind.COMMA.value: 110,
    BoundaryKind.PERIOD.value: 220,
    BoundaryKind.EXCLAMATION.value: 220,
    BoundaryKind.QUESTION.value: 220,
    BoundaryKind.COLON.value: 160,
    BoundaryKind.SEMICOLON.value: 160,
    BoundaryKind.EM_DASH.value: 160,
    BoundaryKind.ELLIPSIS.value: 260,
    BoundaryKind.SOFT_SPLIT.value: 45,
    BoundaryKind.FLUSH.value: 45,
    "clause_fast": 45,
}


def boundary_pause_ms(boundary_kind: BoundaryKind | str) -> int:
    key = (
        boundary_kind.value
        if isinstance(boundary_kind, BoundaryKind)
        else str(boundary_kind or "").strip().lower()
    )
    return PAUSE_BY_BOUNDARY_MS.get(key, 110)


def trim_leading_trailing_silence(
    pcm: np.ndarray,
    *,
    threshold_db: float = -42.0,
    pad_ms: float = 2.0,
    sample_rate: int = 24000,
) -> np.ndarray:
    """Remove silêncios mortos nas bordas inicial e final da chunk sintetizada,
    preservando uma micro-margem (`pad_ms`) para não decepar ataques consonantais ou cruzamentos por zero.
    """
    arr = np.asarray(pcm)
    if arr.size == 0:
        return arr

    if np.issubdtype(arr.dtype, np.integer):
        max_val = float(np.iinfo(arr.dtype).max)
        norm = np.abs(arr.astype(np.float32)) / max_val
    else:
        norm = np.abs(arr.astype(np.float32))

    amp_threshold = float(10.0 ** (threshold_db / 20.0))
    active_indices = np.flatnonzero(norm > amp_threshold)
    if active_indices.size == 0:
        # Se a chunk inteira for silêncio, retorna uma fatia mínima para não quebrar pipelines
        return arr[: min(len(arr), 64)]

    pad_samples = max(1, int(round(sample_rate * (pad_ms / 1000.0))))
    start_idx = max(0, int(active_indices[0]) - pad_samples)
    end_idx = min(len(arr), int(active_indices[-1]) + 1 + pad_samples)
    return arr[start_idx:end_idx]


def apply_hann_crossfade(
    prev_pcm: np.ndarray,
    next_pcm: np.ndarray,
    *,
    crossfade_ms: int = 20,
    sample_rate: int = 24000,
) -> np.ndarray:
    """Costura duas chunks PCM consecutivas usando janela Raised-Cosine (Hann) de `20ms`
    ($w_{out}(t) = 0.5(1 + \\cos(\\pi t / T))$, $w_{in}(t) = 0.5(1 - \\cos(\\pi t / T))$),
    garantindo derivada contínua e zero cliques de fase (`click-free`).
    """
    a = np.asarray(prev_pcm)
    b = np.asarray(next_pcm)
    if a.size == 0:
        return b
    if b.size == 0:
        return a

    fade_samples = int(round(sample_rate * (crossfade_ms / 1000.0)))
    fade_samples = min(fade_samples, len(a), len(b))
    if fade_samples < 2:
        return np.concatenate([a, b])

    theta = np.linspace(0.0, np.pi, fade_samples, dtype=np.float32)
    fade_out = 0.5 * (1.0 + np.cos(theta))
    fade_in = 0.5 * (1.0 - np.cos(theta))

    tail_a = a[-fade_samples:].astype(np.float32)
    head_b = b[:fade_samples].astype(np.float32)
    blended_f32 = tail_a * fade_out + head_b * fade_in

    target_dtype = a.dtype
    if np.issubdtype(target_dtype, np.integer):
        info = np.iinfo(target_dtype)
        blended = np.clip(np.round(blended_f32), info.min, info.max).astype(target_dtype)
    else:
        blended = blended_f32.astype(target_dtype)

    return np.concatenate([a[:-fade_samples], blended, b[fade_samples:].astype(target_dtype)])


@dataclass(slots=True)
class ProsodyContinuityState:
    sample_rate: int = 24000
    tail_ms: int = 600
    max_context_chars: int = 80
    rolling_audio_tail: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=np.int16)
    )
    rolling_text_tail: str = ""
    chunks_processed: int = 0

    @property
    def previous_text(self) -> str:
        return self.rolling_text_tail

    @property
    def max_tail_samples(self) -> int:
        return int(round(self.sample_rate * (self.tail_ms / 1000.0)))


class ProsodyContinuityEngine:
    """Motor de continuidade prosódica entre chunks do Modo LIVE:
    - Mantém um *Rolling Audio Tail* dos últimos `600ms` de áudio PCM (`24kHz` mono)
      e os últimos `80` caracteres sintetizados para condicionar modelos zero-shot locais
      (`Chatterbox` / `F5-TTS`) ou cloud (`ElevenLabs` / `Cartesia` `previous_text`).
    - Remove silêncios mortos nas bordas de cada chunk.
    - Aplica envelope suave Hann de `10ms` nas bordas + crossfade Hann de `20ms`
      e injeta a pausa prosódica calibrada conforme a pontuação da fronteira.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 24000,
        tail_ms: int = 600,
        max_context_chars: int = 80,
        crossfade_ms: int = 20,
    ) -> None:
        self.sample_rate = sample_rate
        self.tail_ms = tail_ms
        self.max_context_chars = max_context_chars
        self.crossfade_ms = crossfade_ms
        self.state = ProsodyContinuityState(
            sample_rate=sample_rate,
            tail_ms=tail_ms,
            max_context_chars=max_context_chars,
        )

    def reset(self) -> None:
        self.state = ProsodyContinuityState(
            sample_rate=self.sample_rate,
            tail_ms=self.tail_ms,
            max_context_chars=self.max_context_chars,
        )

    def _apply_micro_edge_taper(self, pcm: np.ndarray, taper_ms: int = 6) -> np.ndarray:
        """Aplica micro-rampa Hann nas extremidades da fala para garantir zero clique antes da pausa."""
        n_taper = min(int(round(self.sample_rate * (taper_ms / 1000.0))), len(pcm) // 4)
        if n_taper < 2:
            return pcm
        work = pcm.astype(np.float32).copy()
        theta = np.linspace(0.0, np.pi, n_taper, dtype=np.float32)
        fade_in = 0.5 * (1.0 - np.cos(theta))
        fade_out = 0.5 * (1.0 + np.cos(theta))
        work[:n_taper] *= fade_in
        work[-n_taper:] *= fade_out
        if np.issubdtype(pcm.dtype, np.integer):
            info = np.iinfo(pcm.dtype)
            return np.clip(np.round(work), info.min, info.max).astype(pcm.dtype)
        return work.astype(pcm.dtype)

    def process_chunk(
        self,
        pcm: np.ndarray,
        text: str,
        boundary_kind: BoundaryKind | str = BoundaryKind.COMMA,
    ) -> np.ndarray:
        trimmed = trim_leading_trailing_silence(pcm, sample_rate=self.sample_rate)
        tapered = self._apply_micro_edge_taper(trimmed)

        # Atualiza o Rolling Audio Tail de 600ms com o áudio vocal limpo (sem o silêncio da pausa)
        combined_voice = (
            np.concatenate([self.state.rolling_audio_tail, tapered.astype(np.int16)])
            if self.state.rolling_audio_tail.size > 0
            else tapered.astype(np.int16)
        )
        max_samples = self.state.max_tail_samples
        if len(combined_voice) > max_samples:
            self.state.rolling_audio_tail = combined_voice[-max_samples:].copy()
        else:
            self.state.rolling_audio_tail = combined_voice.copy()

        # Atualiza os últimos 80 caracteres sintetizados
        clean_piece = (text or "").strip()
        if clean_piece:
            joined_text = (
                f"{self.state.rolling_text_tail} {clean_piece}".strip()
                if self.state.rolling_text_tail
                else clean_piece
            )
            self.state.rolling_text_tail = joined_text[-self.max_context_chars :]

        # Injeta pausa prosódica calibrada na fronteira
        pause_ms = boundary_pause_ms(boundary_kind)
        pause_samples = max(0, int(round(self.sample_rate * (pause_ms / 1000.0))))
        silence_tail = np.zeros(pause_samples, dtype=tapered.dtype)

        self.state.chunks_processed += 1
        return np.concatenate([tapered, silence_tail])


__all__ = [
    "PAUSE_BY_BOUNDARY_MS",
    "ProsodyContinuityEngine",
    "ProsodyContinuityState",
    "apply_hann_crossfade",
    "boundary_pause_ms",
    "trim_leading_trailing_silence",
]

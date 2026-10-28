from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

PTBR_HOLD_MARKERS_MULTIWORD: tuple[str, ...] = (
    "ou seja",
    "é que",
    "isto é",
    "no entanto",
    "por exemplo",
    "a fim de",
    "para que",
    "já que",
    "visto que",
    "desde que",
    "a gente",
)

PTBR_HOLD_MARKERS_SINGLE: frozenset[str] = frozenset(
    {
        "mas",
        "porque",
        "porquê",
        "então",
        "tipo",
        "que",
        "de",
        "do",
        "da",
        "dos",
        "das",
        "para",
        "pra",
        "pro",
        "e",
        "ou",
        "hum",
        "hmm",
        "ahn",
        "uhm",
        "eh",

        "se",

        "quando",
        "enquanto",


        "quais",
        "porém",
        "contudo",
        "entretanto",
        "portanto",
        "aliás",

        "com",
        "sem",
        "por",
        "em",
        "no",
        "na",
    }
)


@dataclass(frozen=True, slots=True)
class EOUAssessment:
    is_end_of_utterance: bool
    required_silence_ms: int
    silence_ms: int
    reason: str
    detected_hold_marker: str | None = None
    confidence: float = 0.9


def _strip_accents_and_punct(text: str) -> str:
    if not text:
        return ""
    norm = unicodedata.normalize("NFD", text.lower())
    without_accents = "".join(ch for ch in norm if unicodedata.category(ch) != "Mn")
    cleaned = re.sub(r"[^a-z0-9\s]", " ", without_accents)
    return re.sub(r"\s+", " ", cleaned).strip()


def _char_trigrams(text: str) -> set[str]:
    padded = f"  {text}  "
    if len(padded) < 3:
        return {padded}
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def compute_lexical_echo_similarity(candidate: str, reference: str) -> float:
    norm_cand = _strip_accents_and_punct(candidate)
    norm_ref = _strip_accents_and_punct(reference)
    if not norm_cand or not norm_ref:
        return 0.0

    # Se a transcrição captada pelo microfone é substring direta do que o TTS acabou de falar
    if len(norm_cand) >= 6 and norm_cand in norm_ref:
        return 0.95

    cand_words = norm_cand.split()
    ref_words = set(norm_ref.split())
    if not cand_words or not ref_words:
        return 0.0

    # Proporção de palavras da transcrição presentes na fala recente do Orin (containment)
    word_containment = sum(1 for w in cand_words if w in ref_words) / len(cand_words)

    # Similaridade de trigramas de caracteres (Dice coefficient)
    tri_c = _char_trigrams(norm_cand)
    tri_r = _char_trigrams(norm_ref)
    dice_trigram = (2.0 * len(tri_c & tri_r)) / max(1, len(tri_c) + len(tri_r))

    return float(max(word_containment * 0.85 + dice_trigram * 0.15, dice_trigram))


def is_echo_of_recent_tts(
    transcript: str,
    recent_tts_texts: Sequence[str],
    threshold: float = 0.65,
    *,
    has_strong_barge_in_energy: bool = False,
) -> bool:
    """Guarda Anti-Eco Textual:
    Se o microfone captar a própria voz do Orin (similaridade lexical/n-grama >= threshold
    com o que o Orin falou nos últimos segundos sem barge-in acústico forte), descarta como eco.
    """
    if has_strong_barge_in_energy:
        return False
    if not transcript or not transcript.strip() or not recent_tts_texts:
        return False

    clamped_threshold = float(min(max(threshold, 0.1), 0.99))
    for tts_text in recent_tts_texts:
        if not tts_text:
            continue
        sim = compute_lexical_echo_similarity(transcript, tts_text)
        if sim >= clamped_threshold:
            return True
    return False


class PTBRSemanticEOUDetector:
    """Detector semântico de fim de turno (End-of-Utterance) calibrado para Português Brasileiro:
    - Frase completa com pontuação terminal (`.`, `?`, `!`) ou queda de pitch fecha em `fast_timeout_ms` (~380ms).
    - Frase terminando em *Hold Marker* do português (`mas`, `porque`, `então`, `tipo`, `ou seja`,
      `que`, `de`, `para`, `e`, `hum`, `é que`, reticências `...`) estende a espera para `hold_timeout_ms` (~1400ms).
    """

    def __init__(
        self,
        *,
        fast_timeout_ms: int = 380,
        normal_timeout_ms: int = 520,
        hold_timeout_ms: int = 1400,
    ) -> None:
        self.fast_timeout_ms = int(fast_timeout_ms)
        self.normal_timeout_ms = int(normal_timeout_ms)
        self.hold_timeout_ms = int(hold_timeout_ms)

    @staticmethod
    def detect_hold_marker(transcript: str) -> str | None:
        stripped = (transcript or "").strip()
        if not stripped:
            return None

        # Reticências ou travessão suspenso no fim da fala
        if stripped.endswith(("...", "…", "—", "--", ",")):
            return "..."

        # Remove pontuação leve de hesitação para inspecionar as palavras finais
        cleaned = re.sub(r"[,:;—\-]+$", "", stripped).strip().lower()
        # Se termina com pontuação terminal explícita (. ? !) e não é "...", não é hold marker
        if cleaned.endswith((".", "?", "!")):
            return None

        words = re.findall(r"[a-zA-ZÀ-ÿ0-9]+", cleaned)
        if not words:
            return None

        tail_two = " ".join(words[-2:]) if len(words) >= 2 else ""
        tail_three = " ".join(words[-3:]) if len(words) >= 3 else ""

        for multi in PTBR_HOLD_MARKERS_MULTIWORD:
            if tail_three == multi or tail_two == multi:
                return multi

        last_word = words[-1]
        if last_word in PTBR_HOLD_MARKERS_SINGLE:
            return last_word

        return None

    def evaluate(
        self,
        transcript: str,
        silence_ms: int | float,
        *,
        pitch_slope: float | None = None,
    ) -> EOUAssessment:
        sil = int(round(max(0.0, float(silence_ms))))
        text = (transcript or "").strip()

        if not text:
            return EOUAssessment(
                is_end_of_utterance=False,
                required_silence_ms=self.normal_timeout_ms,
                silence_ms=sil,
                reason="empty_transcript",
                detected_hold_marker=None,
                confidence=0.0,
            )

        hold_marker = self.detect_hold_marker(text)
        if hold_marker is not None:
            is_eou = sil >= self.hold_timeout_ms
            return EOUAssessment(
                is_end_of_utterance=is_eou,
                required_silence_ms=self.hold_timeout_ms,
                silence_ms=sil,
                reason="hold_marker_timeout_reached" if is_eou else "holding_on_ptbr_connector",
                detected_hold_marker=hold_marker,
                confidence=0.88 if is_eou else 0.94,
            )

        has_terminal_punct = text.endswith((".", "?", "!")) and not text.endswith("...")
        has_falling_pitch = pitch_slope is not None and pitch_slope < -5.0

        if has_terminal_punct or has_falling_pitch:
            req = self.fast_timeout_ms
            is_eou = sil >= req
            return EOUAssessment(
                is_end_of_utterance=is_eou,
                required_silence_ms=req,
                silence_ms=sil,
                reason="terminal_punctuation_or_falling_pitch",
                detected_hold_marker=None,
                confidence=0.96,
            )

        # Frase sem pontuação explícita mas sem Hold Marker (ex.: STT parcial sem ponto final)
        words = re.findall(r"[a-zA-ZÀ-ÿ0-9]+", text)
        req = self.fast_timeout_ms if len(words) >= 4 else self.normal_timeout_ms
        is_eou = sil >= req
        return EOUAssessment(
            is_end_of_utterance=is_eou,
            required_silence_ms=req,
            silence_ms=sil,
            reason="clause_complete",
            detected_hold_marker=None,
            confidence=0.85,
        )

    @staticmethod
    def is_echo_of_recent_tts(
        transcript: str,
        recent_tts_texts: Sequence[str],
        threshold: float = 0.65,
        *,
        has_strong_barge_in_energy: bool = False,
    ) -> bool:
        return is_echo_of_recent_tts(
            transcript,
            recent_tts_texts,
            threshold=threshold,
            has_strong_barge_in_energy=has_strong_barge_in_energy,
        )


class SileroVADDetector:
    """Detector de atividade de voz (VAD) para janelas PCM 16kHz (~32ms = 512 samples)
    com suporte a *Audio Ducking* (elevação automática de limiar quando o Orin está falando
    para evitar falsos barge-ins por vazamento acústico).
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        base_rms_threshold: float = 0.022,
        ducking_multiplier: float = 2.6,
    ) -> None:
        self.sample_rate = sample_rate
        self.base_rms_threshold = base_rms_threshold
        self.ducking_multiplier = ducking_multiplier

    def frame_probability(self, pcm_frame: bytes | np.ndarray) -> float:
        if isinstance(pcm_frame, (bytes, bytearray, memoryview)):
            raw = bytes(pcm_frame)
            if len(raw) < 2:
                return 0.0
            if len(raw) % 2 == 1:
                raw = raw[:-1]
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        else:
            arr = np.asarray(pcm_frame)
            if arr.size == 0:
                return 0.0
            if np.issubdtype(arr.dtype, np.integer):
                samples = arr.astype(np.float32) / 32768.0
            else:
                samples = arr.astype(np.float32)

        rms = float(np.sqrt(np.mean(np.square(samples))))
        if rms <= 1e-5:
            return 0.0
        # Mapeia RMS vocal típico (0.02..0.25) para probabilidade [0.0..1.0]
        prob = min(1.0, rms / 0.12)
        return float(prob)

    def is_speech(
        self,
        pcm_frame: bytes | np.ndarray,
        *,
        is_assistant_speaking: bool = False,
    ) -> bool:
        prob = self.frame_probability(pcm_frame)
        threshold = self.base_rms_threshold / 0.12
        if is_assistant_speaking:
            threshold = min(0.85, threshold * self.ducking_multiplier)
        return prob >= threshold


__all__ = [
    "EOUAssessment",
    "PTBRSemanticEOUDetector",
    "SileroVADDetector",
    "compute_lexical_echo_similarity",
    "is_echo_of_recent_tts",
]

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np


class SpriteCategory(StrEnum):
    BREATH_IN = "breath_in"
    THINKING_SHORT = "thinking_short"
    TOOL_ACK_FAST = "tool_ack_fast"
    TOOL_HOLD_LONG = "tool_hold_long"
    BACKCHANNEL = "backchannel"


class AudioPriority(StrEnum):
    P0_BARGE_IN = "p0_barge_in"
    P1_SPEAK_CHUNK = "p1_speak_chunk"
    P2_FILLER = "p2_filler"


class ArbitrationAction(StrEnum):
    PLAY_IMMEDIATELY = "play_immediately"
    CANCEL_PENDING_FILLER = "cancel_pending_filler"
    GRACE_WAIT = "grace_wait"
    COSINE_FADEOUT = "cosine_fadeout"


@dataclass(frozen=True, slots=True)
class AudioSprite:
    sprite_id: str
    category: SpriteCategory
    label_ptbr: str
    duration_ms: int
    sample_rate: int = 24000
    file_path: Path | None = None
    pcm_samples: np.ndarray | None = None

    def render_pcm_int16(self, sample_rate: int | None = None) -> np.ndarray:
        """Retorna os samples PCM Int16 do sprite em `<1ms` (pré-carregado ou síntese suave de envelope)."""
        sr = sample_rate or self.sample_rate
        if self.pcm_samples is not None and len(self.pcm_samples) > 0:
            if self.pcm_samples.dtype == np.int16:
                return self.pcm_samples
            return np.clip(self.pcm_samples * 32767.0, -32768, 32767).astype(np.int16)

        n_samples = max(1, int(round(sr * (self.duration_ms / 1000.0))))
        t = np.linspace(0.0, self.duration_ms / 1000.0, n_samples, dtype=np.float32)
        window = np.sin(np.linspace(0.0, np.pi, n_samples, dtype=np.float32))
        # Formante vocal suave (~145Hz fundamental + 290Hz harmônico com baixa amplitude)
        wave = 0.08 * np.sin(2.0 * np.pi * 145.0 * t) + 0.04 * np.sin(2.0 * np.pi * 290.0 * t)
        pcm = np.clip(wave * window * 32767.0, -32768, 32767).astype(np.int16)
        return pcm


DEFAULT_PTBR_SPRITES: tuple[AudioSprite, ...] = (
    # BREATH_IN (3)
    AudioSprite("breath_soft_01", SpriteCategory.BREATH_IN, "[inalação suave]", 170),
    AudioSprite("breath_soft_02", SpriteCategory.BREATH_IN, "[respiro curto]", 190),
    AudioSprite("breath_soft_03", SpriteCategory.BREATH_IN, "[inspiração leve]", 160),
    # THINKING_SHORT (5)
    AudioSprite("think_hmm_01", SpriteCategory.THINKING_SHORT, "Hmm...", 340),
    AudioSprite("think_ahn_02", SpriteCategory.THINKING_SHORT, "Ahn...", 310),
    AudioSprite("think_vejamos_03", SpriteCategory.THINKING_SHORT, "Vejamos...", 420),
    AudioSprite("think_bom_04", SpriteCategory.THINKING_SHORT, "Bom...", 290),
    AudioSprite("think_ora_05", SpriteCategory.THINKING_SHORT, "Olha...", 330),
    # TOOL_ACK_FAST (4)
    AudioSprite("tool_fast_deixa_ver_01", SpriteCategory.TOOL_ACK_FAST, "Deixa eu ver aqui...", 780),
    AudioSprite("tool_fast_checando_02", SpriteCategory.TOOL_ACK_FAST, "Já estou conferindo...", 820),
    AudioSprite("tool_fast_um_segundo_03", SpriteCategory.TOOL_ACK_FAST, "Um segundo, deixa eu olhar...", 860),
    AudioSprite("tool_fast_buscando_04", SpriteCategory.TOOL_ACK_FAST, "Consultando isso agora...", 790),
    # TOOL_HOLD_LONG (3)
    AudioSprite("tool_long_instante_01", SpriteCategory.TOOL_HOLD_LONG, "Só um instante...", 920),
    AudioSprite("tool_long_quase_la_02", SpriteCategory.TOOL_HOLD_LONG, "Quase lá, só mais um momento...", 1150),
    AudioSprite("tool_long_processando_03", SpriteCategory.TOOL_HOLD_LONG, "Ainda processando aqui, já te digo...", 1200),
    # BACKCHANNEL (3)
    AudioSprite("back_uhum_01", SpriteCategory.BACKCHANNEL, "Uhum...", 260),
    AudioSprite("back_entendi_02", SpriteCategory.BACKCHANNEL, "Entendi...", 380),
    AudioSprite("back_claro_03", SpriteCategory.BACKCHANNEL, "Certo...", 320),
)


class AudioSpriteBank:
    """Banco de Audio Sprites com Fisher-Yates Shuffle Bag e garantia anti-repetição
    consecutiva (nunca sorteia o mesmo `sprite_id` duas vezes seguidas na mesma categoria).
    """

    def __init__(
        self,
        sprites: Sequence[AudioSprite] | None = None,
        *,
        seed: int | None = None,
        sprites_dir: Path | None = None,
    ) -> None:
        self._rng = random.Random(seed)
        self._sprites_dir = sprites_dir
        catalog = list(sprites) if sprites is not None else list(DEFAULT_PTBR_SPRITES)

        self._by_category: dict[SpriteCategory, list[AudioSprite]] = {
            cat: [] for cat in SpriteCategory
        }
        for sp in catalog:
            self._by_category[sp.category].append(sp)

        self._bags: dict[SpriteCategory, list[AudioSprite]] = {}
        self._last_picked_id: dict[SpriteCategory, str | None] = {
            cat: None for cat in SpriteCategory
        }
        for cat in SpriteCategory:
            self._refill_and_shuffle_bag(cat)

    def _fisher_yates_shuffle(self, items: list[AudioSprite]) -> None:
        n = len(items)
        for i in range(n - 1, 0, -1):
            j = self._rng.randint(0, i)
            items[i], items[j] = items[j], items[i]

    def _refill_and_shuffle_bag(self, category: SpriteCategory) -> None:
        pool = list(self._by_category.get(category, []))
        if not pool:
            self._bags[category] = []
            return

        self._fisher_yates_shuffle(pool)
        last_id = self._last_picked_id.get(category)
        # No pop() tiramos do final da lista (pool[-1]), então garantimos que pool[-1] != last_id
        if len(pool) >= 2 and last_id is not None and pool[-1].sprite_id == last_id:
            swap_idx = self._rng.randint(0, len(pool) - 2)
            pool[-1], pool[swap_idx] = pool[swap_idx], pool[-1]

        self._bags[category] = pool

    def list_category(self, category: SpriteCategory) -> list[AudioSprite]:
        return list(self._by_category.get(category, []))

    def pick(self, category: SpriteCategory) -> AudioSprite:
        bag = self._bags.get(category)
        if not bag:
            self._refill_and_shuffle_bag(category)
            bag = self._bags.get(category)
        if not bag:
            raise ValueError(f"No audio sprites registered for category {category}")

        sprite = bag.pop()
        # Defesa extra caso bag tenha sido manipulada externamente
        if (
            len(self._by_category[category]) >= 2
            and sprite.sprite_id == self._last_picked_id.get(category)
        ):
            if not bag:
                self._refill_and_shuffle_bag(category)
                bag = self._bags[category]
            alt = bag.pop()
            bag.insert(0, sprite)
            sprite = alt

        self._last_picked_id[category] = sprite.sprite_id
        return sprite

    def pick_for_inline_marker(self, marker: str) -> AudioSprite | None:
        clean = marker.strip("[] \t\r\n").lower()
        if clean in {"breath", "sigh", "gasp"}:
            return self.pick(SpriteCategory.BREATH_IN)
        if clean in {"hmm", "uhm", "ahn"}:
            return self.pick(SpriteCategory.THINKING_SHORT)
        if clean in {"laugh", "chuckle"}:
            return self.pick(SpriteCategory.BACKCHANNEL)
        return None


class ThinkingVocalizationController:
    """Controlador de vocalizações de pensamento e espera de tools/MCPs.
    - Emite `THINKING_SHORT` quando `AGENT_THOUGHT_DELTA` excede `thinking_threshold_ms` (320ms) sem `<speak>` aberto.
    - Emite `TOOL_ACK_FAST` (>2200ms) e `TOOL_HOLD_LONG` (>5500ms) quando tools demoram sem `<speak>` aberto.
    - Bloqueia imediatamente novos fillers no instante em que `SPEAK_OPENED` é emitido.
    """

    def __init__(
        self,
        sprite_bank: AudioSpriteBank | None = None,
        *,
        thinking_threshold_ms: float = 320.0,
        tool_fast_threshold_ms: float = 2200.0,
        tool_long_threshold_ms: float = 5500.0,
    ) -> None:
        self.sprite_bank = sprite_bank or AudioSpriteBank()
        self.thinking_threshold_ms = thinking_threshold_ms
        self.tool_fast_threshold_ms = tool_fast_threshold_ms
        self.tool_long_threshold_ms = tool_long_threshold_ms

        self._turn_started_at_ms: float = 0.0
        self._speak_is_open: bool = False
        self._has_spoken_in_turn: bool = False
        self._thinking_filler_emitted: bool = False
        self._active_tool_name: str | None = None
        self._active_tool_started_at_ms: float | None = None
        self._tool_fast_emitted: bool = False
        self._tool_long_emitted: bool = False
        self._last_emitted_sprite: AudioSprite | None = None

    @property
    def speak_is_open(self) -> bool:
        return self._speak_is_open

    @property
    def last_emitted_sprite(self) -> AudioSprite | None:
        return self._last_emitted_sprite

    def on_turn_started(self, now_ms: float = 0.0) -> None:
        self._turn_started_at_ms = float(now_ms)
        self._speak_is_open = False
        self._has_spoken_in_turn = False
        self._thinking_filler_emitted = False
        self._active_tool_name = None
        self._active_tool_started_at_ms = None
        self._tool_fast_emitted = False
        self._tool_long_emitted = False
        self._last_emitted_sprite = None

    def on_speak_opened(self, now_ms: float = 0.0) -> None:
        _ = now_ms
        self._speak_is_open = True
        self._has_spoken_in_turn = True

    def on_speak_closed(self, now_ms: float = 0.0) -> None:
        _ = now_ms
        self._speak_is_open = False

    def on_thinking_delta(
        self,
        now_ms: float | None = None,
        *,
        elapsed_ms: float | None = None,
    ) -> AudioSprite | None:
        if self._speak_is_open or self._has_spoken_in_turn:
            return None
        if self._thinking_filler_emitted:
            return None

        if elapsed_ms is not None:
            elapsed = float(elapsed_ms)
        elif now_ms is not None:
            elapsed = float(now_ms) - self._turn_started_at_ms
        else:
            elapsed = 0.0

        if elapsed > self.thinking_threshold_ms:
            self._thinking_filler_emitted = True
            sprite = self.sprite_bank.pick(SpriteCategory.THINKING_SHORT)
            self._last_emitted_sprite = sprite
            return sprite
        return None

    def on_tool_started(
        self,
        tool_name: str,
        now_ms: float = 0.0,
    ) -> AudioSprite | None:
        if self._speak_is_open:
            return None
        self._active_tool_name = tool_name
        self._active_tool_started_at_ms = float(now_ms)
        self._tool_fast_emitted = False
        self._tool_long_emitted = False
        return None

    def on_tool_progressed(
        self,
        now_ms: float | None = None,
        *,
        elapsed_ms: float | None = None,
    ) -> AudioSprite | None:
        if self._speak_is_open or self._active_tool_started_at_ms is None:
            return None

        if elapsed_ms is not None:
            tool_elapsed = float(elapsed_ms)
        elif now_ms is not None:
            tool_elapsed = float(now_ms) - self._active_tool_started_at_ms
        else:
            tool_elapsed = 0.0

        if tool_elapsed > self.tool_long_threshold_ms and not self._tool_long_emitted:
            self._tool_long_emitted = True
            self._tool_fast_emitted = True
            sprite = self.sprite_bank.pick(SpriteCategory.TOOL_HOLD_LONG)
            self._last_emitted_sprite = sprite
            return sprite

        if (
            tool_elapsed > self.tool_fast_threshold_ms
            and not self._tool_fast_emitted
            and not self._has_spoken_in_turn
        ):
            self._tool_fast_emitted = True
            sprite = self.sprite_bank.pick(SpriteCategory.TOOL_ACK_FAST)
            self._last_emitted_sprite = sprite
            return sprite

        return None


@dataclass(frozen=True, slots=True)
class ArbitrationDecision:
    action: ArbitrationAction
    wait_ms: int
    fade_out_ms: int
    breath_gap_ms: int
    remaining_ms: int
    cosine_envelope: np.ndarray | None = None


class AudioArbitrator:
    """Resolve a transição entre um `P2_FILLER` em reprodução e a chegada de um `P1_SPEAK_CHUNK`:
    - Quando `SPEAK_OPENED` chega, bloqueia imediatamente qualquer filler pendente (`CANCEL_PENDING_FILLER`).
    - Se `0 < remaining_ms <= 200`: aplica `GRACE_WAIT` (`wait_ms = remaining_ms + 40`, `breath_gap_ms = 40`).
    - Se `remaining_ms > 200`: aplica `COSINE_FADEOUT` (`fade_out_ms = 90`, `breath_gap_ms = 45`, `wait_ms = 135`)
      e gera o envelope numérico numpy de atenuação cosseno ($0.5 \\cdot (1 + \\cos(\\pi t / T))$).
    """

    GRACE_WINDOW_MAX_MS: int = 200
    GRACE_BREATH_GAP_MS: int = 40
    COSINE_FADEOUT_MS: int = 90
    COSINE_BREATH_GAP_MS: int = 45

    def __init__(self) -> None:
        self._filler_suppressed: bool = False

    def reset_for_new_turn(self) -> None:
        self._filler_suppressed = False

    def can_emit_filler(self) -> bool:
        return not self._filler_suppressed

    def on_speak_opened(self) -> ArbitrationDecision:
        self._filler_suppressed = True
        return ArbitrationDecision(
            action=ArbitrationAction.CANCEL_PENDING_FILLER,
            wait_ms=0,
            fade_out_ms=0,
            breath_gap_ms=0,
            remaining_ms=0,
            cosine_envelope=None,
        )

    @classmethod
    def build_cosine_fadeout_envelope(
        cls,
        fade_out_ms: int = COSINE_FADEOUT_MS,
        sample_rate: int = 24000,
    ) -> np.ndarray:
        n_samples = max(2, int(round(sample_rate * (fade_out_ms / 1000.0))))
        theta = np.linspace(0.0, np.pi, n_samples, dtype=np.float32)
        envelope = (0.5 * (1.0 + np.cos(theta))).astype(np.float32)
        return envelope

    def resolve_speak_chunk_arrival(
        self,
        remaining_ms: int | float | None = None,
        *,
        sample_rate: int = 24000,
    ) -> ArbitrationDecision:
        self._filler_suppressed = True
        if remaining_ms is None or remaining_ms <= 0:
            return ArbitrationDecision(
                action=ArbitrationAction.PLAY_IMMEDIATELY,
                wait_ms=0,
                fade_out_ms=0,
                breath_gap_ms=0,
                remaining_ms=0,
                cosine_envelope=None,
            )

        rem = int(round(remaining_ms))
        if rem <= self.GRACE_WINDOW_MAX_MS:
            gap = self.GRACE_BREATH_GAP_MS
            return ArbitrationDecision(
                action=ArbitrationAction.GRACE_WAIT,
                wait_ms=rem + gap,
                fade_out_ms=0,
                breath_gap_ms=gap,
                remaining_ms=rem,
                cosine_envelope=None,
            )

        fade_ms = self.COSINE_FADEOUT_MS
        gap_ms = self.COSINE_BREATH_GAP_MS
        envelope = self.build_cosine_fadeout_envelope(
            fade_out_ms=fade_ms,
            sample_rate=sample_rate,
        )
        return ArbitrationDecision(
            action=ArbitrationAction.COSINE_FADEOUT,
            wait_ms=fade_ms + gap_ms,
            fade_out_ms=fade_ms,
            breath_gap_ms=gap_ms,
            remaining_ms=rem,
            cosine_envelope=envelope,
        )

    def arbitrate(
        self,
        remaining_ms: int | float | None = None,
        *,
        sample_rate: int = 24000,
    ) -> ArbitrationDecision:
        return self.resolve_speak_chunk_arrival(
            remaining_ms=remaining_ms,
            sample_rate=sample_rate,
        )

    @classmethod
    def apply_fadeout_to_pcm(
        cls,
        pcm: np.ndarray,
        *,
        fade_out_ms: int = COSINE_FADEOUT_MS,
        breath_gap_ms: int = COSINE_BREATH_GAP_MS,
        sample_rate: int = 24000,
    ) -> np.ndarray:
        """Aplica o envelope de atenuação cosseno de `fade_out_ms` ao buffer PCM em andamento
        e anexa `breath_gap_ms` de silêncio digital antes de entrar o `P1_SPEAK_CHUNK`."""
        fade_samples = max(2, int(round(sample_rate * (fade_out_ms / 1000.0))))
        gap_samples = max(0, int(round(sample_rate * (breath_gap_ms / 1000.0))))

        slice_len = min(len(pcm), fade_samples)
        envelope = cls.build_cosine_fadeout_envelope(
            fade_out_ms=fade_out_ms,
            sample_rate=sample_rate,
        )[:slice_len]

        active_slice = pcm[:slice_len].astype(np.float32) * envelope
        silence = np.zeros(gap_samples, dtype=pcm.dtype)

        if np.issubdtype(pcm.dtype, np.integer):
            info = np.iinfo(pcm.dtype)
            faded = np.clip(np.round(active_slice), info.min, info.max).astype(pcm.dtype)
        else:
            faded = active_slice.astype(pcm.dtype)

        return np.concatenate([faded, silence])

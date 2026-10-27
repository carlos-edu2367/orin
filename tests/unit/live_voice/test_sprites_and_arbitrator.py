from __future__ import annotations

import numpy as np

from agentos.live_voice.sprites import (
    ArbitrationAction,
    AudioArbitrator,
    AudioSpriteBank,
    SpriteCategory,
    ThinkingVocalizationController,
)


class TestAudioSpriteBankShuffleBag:
    def test_sprite_bank_has_default_sprites_for_all_categories(self) -> None:
        bank = AudioSpriteBank(seed=42)
        for category in SpriteCategory:
            sprites = bank.list_category(category)
            assert len(sprites) >= 2, f"Category {category} should have at least 2 sprites"

    def test_fisher_yates_shuffle_bag_never_repeats_consecutively(self) -> None:
        bank = AudioSpriteBank(seed=7)
        for category in SpriteCategory:
            previous_id: str | None = None
            for _ in range(30):
                sprite = bank.pick(category)
                assert sprite.category == category
                assert sprite.sprite_id != previous_id, (
                    f"Consecutive repeat detected for {category}: {sprite.sprite_id}"
                )
                previous_id = sprite.sprite_id


class TestThinkingVocalizationController:
    def test_emits_thinking_short_when_thought_delta_exceeds_320ms_without_speak(self) -> None:
        bank = AudioSpriteBank(seed=123)
        ctrl = ThinkingVocalizationController(sprite_bank=bank)
        ctrl.on_turn_started(now_ms=0.0)

        # Before 320ms: no sprite
        assert ctrl.on_thinking_delta(now_ms=150.0) is None
        assert ctrl.on_thinking_delta(now_ms=320.0) is None

        # After 320ms: emits THINKING_SHORT ("Hmm..." / "Ahn...")
        sprite = ctrl.on_thinking_delta(now_ms=350.0)
        assert sprite is not None
        assert sprite.category == SpriteCategory.THINKING_SHORT

        # Subsequent thought deltas in the same thinking block do not spam another thinking sprite
        assert ctrl.on_thinking_delta(now_ms=500.0) is None

    def test_blocks_all_new_fillers_immediately_when_speak_opened(self) -> None:
        bank = AudioSpriteBank(seed=99)
        ctrl = ThinkingVocalizationController(sprite_bank=bank)
        ctrl.on_turn_started(now_ms=0.0)

        ctrl.on_speak_opened(now_ms=100.0)
        assert ctrl.on_thinking_delta(now_ms=450.0) is None
        assert ctrl.on_tool_started("web_search", now_ms=500.0) is None
        assert ctrl.on_tool_progressed(now_ms=3000.0) is None

    def test_tool_vocalization_fast_and_long_thresholds(self) -> None:
        bank = AudioSpriteBank(seed=55)
        ctrl = ThinkingVocalizationController(sprite_bank=bank)
        ctrl.on_turn_started(now_ms=0.0)

        ctrl.on_tool_started("mcp_search", now_ms=100.0)
        assert ctrl.on_tool_progressed(now_ms=1500.0) is None

        # > 2.2s (2200ms) -> TOOL_ACK_FAST
        fast_sprite = ctrl.on_tool_progressed(now_ms=2400.0)
        assert fast_sprite is not None
        assert fast_sprite.category == SpriteCategory.TOOL_ACK_FAST

        # > 5.5s (5500ms) -> TOOL_HOLD_LONG
        long_sprite = ctrl.on_tool_progressed(now_ms=5700.0)
        assert long_sprite is not None
        assert long_sprite.category == SpriteCategory.TOOL_HOLD_LONG


class TestAudioArbitrator:
    def test_suppresses_pending_filler_on_speak_opened(self) -> None:
        arb = AudioArbitrator()
        assert arb.can_emit_filler() is True

        decision = arb.on_speak_opened()
        assert decision.action == ArbitrationAction.CANCEL_PENDING_FILLER
        assert arb.can_emit_filler() is False

    def test_grace_window_when_remaining_ms_is_140ms_le_200ms(self) -> None:
        arb = AudioArbitrator()
        decision = arb.resolve_speak_chunk_arrival(remaining_ms=140, sample_rate=24000)

        assert decision.action == ArbitrationAction.GRACE_WAIT
        assert decision.remaining_ms == 140
        assert decision.breath_gap_ms == 40
        assert decision.fade_out_ms == 0
        assert decision.wait_ms == 180  # 140ms + 40ms
        assert decision.cosine_envelope is None

    def test_cosine_fadeout_when_remaining_ms_is_600ms_gt_200ms(self) -> None:
        arb = AudioArbitrator()
        decision = arb.resolve_speak_chunk_arrival(remaining_ms=600, sample_rate=24000)

        assert decision.action == ArbitrationAction.COSINE_FADEOUT
        assert decision.remaining_ms == 600
        assert decision.fade_out_ms == 90
        assert decision.breath_gap_ms == 45
        assert decision.wait_ms == 135  # 90ms + 45ms
        assert decision.cosine_envelope is not None

        expected_samples = int(24000 * 0.090)
        assert decision.cosine_envelope.shape == (expected_samples,)
        assert np.isclose(decision.cosine_envelope[0], 1.0, atol=1e-5)
        assert np.isclose(decision.cosine_envelope[-1], 0.0, atol=1e-5)
        # Monotonically non-increasing
        diffs = np.diff(decision.cosine_envelope)
        assert np.all(diffs <= 1e-6)

        # Also test applying the fade-out envelope + silence gap to an int16 PCM buffer
        raw_pcm = np.full(int(24000 * 0.600), 10000, dtype=np.int16)
        faded_pcm = arb.apply_fadeout_to_pcm(raw_pcm, sample_rate=24000)
        gap_samples = int(24000 * 0.045)
        assert faded_pcm.shape == (expected_samples + gap_samples,)
        assert faded_pcm[0] == 10000
        assert faded_pcm[expected_samples - 1] == 0
        assert np.all(faded_pcm[expected_samples:] == 0)

    def test_play_immediately_when_no_filler_active(self) -> None:
        arb = AudioArbitrator()
        decision = arb.resolve_speak_chunk_arrival(remaining_ms=0)
        assert decision.action == ArbitrationAction.PLAY_IMMEDIATELY
        assert decision.wait_ms == 0

    def test_sprite_pcm_rendering_and_inline_marker_mapping(self) -> None:
        bank = AudioSpriteBank(seed=11)
        breath = bank.pick_for_inline_marker("[breath]")
        assert breath is not None
        assert breath.category == SpriteCategory.BREATH_IN
        pcm = breath.render_pcm_int16(sample_rate=24000)
        assert pcm.dtype == np.int16
        assert len(pcm) == int(24000 * (breath.duration_ms / 1000.0))

        hmm = bank.pick_for_inline_marker("[hmm]")
        assert hmm is not None
        assert hmm.category == SpriteCategory.THINKING_SHORT

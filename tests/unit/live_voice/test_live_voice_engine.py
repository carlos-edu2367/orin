from __future__ import annotations

import base64
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentos.agentic.speak_stream import BoundaryKind, ProsodicChunk
from agentos.launcher import internal as launcher_internal
from agentos.live_voice.hardware_governor import (
    GPUDeviceInfo,
    VoiceHardwareGovernor,
)
from agentos.live_voice.prosody_continuity import (
    ProsodyContinuityEngine,
    apply_hann_crossfade,
    boundary_pause_ms,
    trim_leading_trailing_silence,
)
from agentos.live_voice.providers import (
    SUPPORTED_STT_PROVIDERS,
    SUPPORTED_TTS_PROVIDERS,
    MultiProviderSTTAdapter,
    MultiProviderTTSAdapter,
    SynthesizedAudioChunk,
    VoiceConfig,
)
from agentos.live_voice.session import (
    SpokenTranscriptLedger,
    register_live_voice_routes,
)
from agentos.live_voice.sidecar import (
    DEFAULT_SIDECAR_PORT,
    VoiceSidecarSupervisor,
    create_sidecar_app,
)
from agentos.live_voice.vad_eou import (
    PTBRSemanticEOUDetector,
    SileroVADDetector,
    is_echo_of_recent_tts,
)


# ===========================================================================
# 1. VoiceHardwareGovernor (RTX 4060 8GB & Fallback CPU/Low-VRAM)
# ===========================================================================


def test_hardware_governor_recommends_rtx_4060_8gb_high_naturalness_preset() -> None:
    governor = VoiceHardwareGovernor(
        probe_fn=lambda: GPUDeviceInfo(
            has_cuda=True,
            device_name="NVIDIA GeForce RTX 4060",
            total_vram_gb=8.0,
            free_vram_gb=6.8,
            cpu_cores=16,
            ram_gb=32.0,
        )
    )
    profile = governor.recommend()

    assert profile.tier == "local_gpu_8gb"
    assert profile.preset == "local_high_naturalness"
    assert profile.primary_tts_model == "chatterbox-pt-br"
    assert profile.alternative_tts_model == "f5-tts-pt-br"
    assert profile.recommended_stt_model == "faster-whisper-large-v3-turbo"
    assert profile.stt_compute_type == "int8_float16"
    assert 3.0 <= profile.estimated_vram_gb <= 4.0
    assert profile.total_vram_gb == pytest.approx(8.0)
    assert profile.fallback_applied is False


def test_hardware_governor_graceful_fallback_when_vram_low_or_cpu_only() -> None:
    # Caso 1: RTX 4060 com VRAM ocupada por jogo ou Ollama pesado (< 1.8 GB livres)
    governor_low_vram = VoiceHardwareGovernor(
        probe_fn=lambda: GPUDeviceInfo(
            has_cuda=True,
            device_name="NVIDIA GeForce RTX 4060",
            total_vram_gb=8.0,
            free_vram_gb=1.3,
            cpu_cores=12,
            ram_gb=16.0,
        )
    )
    low_vram_profile = governor_low_vram.recommend()
    assert low_vram_profile.primary_tts_model == "kokoro-82m-onnx"
    assert low_vram_profile.recommended_stt_model == "faster-whisper-small-int8"
    assert low_vram_profile.fallback_applied is True
    assert any("VRAM" in w or "memória" in w.lower() for w in low_vram_profile.warnings)

    # Caso 2: Máquina sem GPU NVIDIA (CPU-only)
    governor_cpu = VoiceHardwareGovernor(
        probe_fn=lambda: GPUDeviceInfo(
            has_cuda=False,
            device_name=None,
            total_vram_gb=0.0,
            free_vram_gb=0.0,
            cpu_cores=8,
            ram_gb=16.0,
        )
    )
    cpu_profile = governor_cpu.recommend()
    assert cpu_profile.tier == "cpu_only"
    assert cpu_profile.primary_tts_model == "kokoro-82m-onnx"
    assert cpu_profile.recommended_stt_model == "faster-whisper-small-int8"
    assert cpu_profile.fallback_applied is True


def test_hardware_governor_handles_cuda_oom_recovery_without_crashing() -> None:
    governor = VoiceHardwareGovernor(
        probe_fn=lambda: GPUDeviceInfo(
            has_cuda=True,
            device_name="NVIDIA GeForce RTX 4060",
            total_vram_gb=8.0,
            free_vram_gb=5.5,
            cpu_cores=16,
            ram_gb=32.0,
        )
    )
    downgraded = governor.recover_from_oom(failed_model="chatterbox-pt-br")
    assert downgraded.primary_tts_model == "kokoro-82m-onnx"
    assert downgraded.recommended_stt_model == "faster-whisper-small-int8"
    assert downgraded.fallback_applied is True


# ===========================================================================
# 2. PTBRSemanticEOUDetector, SileroVADDetector & Echo Guard
# ===========================================================================


def test_ptbr_semantic_eou_detector_fast_completion_on_terminal_punctuation() -> None:
    detector = PTBRSemanticEOUDetector(fast_timeout_ms=380, hold_timeout_ms=1400)

    res_period = detector.evaluate("Quero criar uma rota de login.", silence_ms=390)
    assert res_period.is_end_of_utterance is True
    assert res_period.required_silence_ms == 380

    res_question = detector.evaluate("Pode rodar os testes?", silence_ms=380)
    assert res_question.is_end_of_utterance is True

    # Antes de 380ms ainda aguarda
    res_early = detector.evaluate("Pode rodar os testes?", silence_ms=220)
    assert res_early.is_end_of_utterance is False


@pytest.mark.parametrize(
    "suspended_phrase",
    [
        "Eu quero alterar a tabela, mas",
        "Estava pensando aqui porque",
        "A gente pode fazer isso, então",
        "Queria criar um componente tipo",
        "Ele retorna erro 500, ou seja",
        "Preciso de uma função que",
        "Vamos mudar o serviço de",
        "Vou mandar o arquivo para",
        "Roda o linter e",
        "Hum",
        "É que",
        "Eu estava pensando se a gente pode usar...",
    ],
)
def test_ptbr_semantic_eou_detector_holds_turn_on_portuguese_hold_markers(
    suspended_phrase: str,
) -> None:
    detector = PTBRSemanticEOUDetector(fast_timeout_ms=380, hold_timeout_ms=1400)

    # Em 500ms de silêncio, NÃO deve encerrar o turno (usuário está pensando)
    decision_500 = detector.evaluate(suspended_phrase, silence_ms=500)
    assert decision_500.is_end_of_utterance is False
    assert decision_500.required_silence_ms == 1400
    assert decision_500.detected_hold_marker is not None

    # Após 1450ms de silêncio, libera o turno para não travar indefinidamente
    decision_1450 = detector.evaluate(suspended_phrase, silence_ms=1450)
    assert decision_1450.is_end_of_utterance is True


def test_echo_guard_discards_acoustic_feedback_from_recent_tts() -> None:
    recent_tts = [
        "Olá, meu nome é Orin e posso te auxiliar com várias coisas hoje!",
        "Já estou conferindo os arquivos do projeto para você.",
    ]

    # Microfone captou parte da fala do próprio Orin
    assert (
        is_echo_of_recent_tts(
            "meu nome é Orin e posso te auxiliar com várias coisas",
            recent_tts,
            threshold=0.65,
        )
        is True
    )

    # Fala legítima do usuário
    assert (
        is_echo_of_recent_tts(
            "Não, na verdade quero criar uma migration nova no banco.",
            recent_tts,
            threshold=0.65,
        )
        is False
    )

    # Se houve energia forte de barge-in humano, não descarta
    assert (
        is_echo_of_recent_tts(
            "meu nome é Orin",
            recent_tts,
            threshold=0.65,
            has_strong_barge_in_energy=True,
        )
        is False
    )


def test_silero_vad_detector_frame_activity_and_ducking() -> None:
    vad = SileroVADDetector(sample_rate=16000)

    silence_frame = np.zeros(512, dtype=np.int16).tobytes()
    assert vad.is_speech(silence_frame, is_assistant_speaking=False) is False

    t = np.linspace(0.0, 0.032, 512, dtype=np.float32)
    voice_frame = (np.sin(2.0 * np.pi * 180.0 * t) * 14000).astype(np.int16).tobytes()
    assert vad.is_speech(voice_frame, is_assistant_speaking=False) is True


# ===========================================================================
# 3. ProsodyContinuityEngine (Rolling Tail 600ms, Pauses, 20ms Hann Crossfade)
# ===========================================================================


def test_prosody_continuity_engine_rolling_tail_silence_trim_and_pauses() -> None:
    engine = ProsodyContinuityEngine(sample_rate=24000, tail_ms=600, max_context_chars=80)

    assert boundary_pause_ms(BoundaryKind.COMMA) == 110
    assert boundary_pause_ms(BoundaryKind.PERIOD) == 220
    assert boundary_pause_ms(BoundaryKind.COLON) == 160
    assert boundary_pause_ms(BoundaryKind.SEMICOLON) == 160
    assert boundary_pause_ms(BoundaryKind.SOFT_SPLIT) == 45
    assert boundary_pause_ms("clause_fast") == 45

    # Cria um sinal com 50ms de silêncio morto no início e fim + 400ms de tom ativo no meio
    sr = 24000
    lead_zeros = np.zeros(int(sr * 0.05), dtype=np.int16)
    trail_zeros = np.zeros(int(sr * 0.05), dtype=np.int16)
    t = np.linspace(0.0, 0.4, int(sr * 0.4), dtype=np.float32)
    tone = (np.sin(2.0 * np.pi * 200.0 * t) * 12000).astype(np.int16)
    raw_pcm = np.concatenate([lead_zeros, tone, trail_zeros])

    trimmed = trim_leading_trailing_silence(raw_pcm, sample_rate=sr)
    assert len(trimmed) < len(raw_pcm)
    assert len(trimmed) >= len(tone)

    # Processa duas chunks consecutivas para alimentar o Rolling Audio Tail de 600ms (14400 samples)
    out1 = engine.process_chunk(raw_pcm, "Olá, meu nome é Orin,", BoundaryKind.COMMA)
    out2 = engine.process_chunk(
        raw_pcm,
        "posso te auxiliar com várias coisas no seu projeto agora mesmo!",
        BoundaryKind.EXCLAMATION,
    )

    assert len(out1) > len(trimmed)  # inclui pausa de 110ms
    assert len(out2) > len(trimmed)  # inclui pausa de 220ms
    assert len(engine.state.rolling_audio_tail) <= int(sr * 0.6)
    assert len(engine.state.rolling_audio_tail) > 0
    assert len(engine.state.rolling_text_tail) <= 80
    assert "projeto agora mesmo" in engine.state.rolling_text_tail


def test_hann_crossfade_20ms_is_click_free_and_phase_continuous() -> None:
    sr = 24000
    n = int(sr * 0.1)  # 100ms cada
    t = np.linspace(0.0, 0.1, n, dtype=np.float32)
    chunk_a = (np.sin(2.0 * np.pi * 220.0 * t) * 10000).astype(np.int16)
    chunk_b = (np.cos(2.0 * np.pi * 220.0 * t) * 10000).astype(np.int16)  # salto abrupto em t=0

    joined = apply_hann_crossfade(chunk_a, chunk_b, crossfade_ms=20, sample_rate=sr)
    fade_samples = int(sr * 0.020)  # 480 samples
    assert len(joined) == len(chunk_a) + len(chunk_b) - fade_samples

    # Verifica ausência de degrau abrupto (click) na região de transição
    transition_region = joined[len(chunk_a) - fade_samples - 2 : len(chunk_a) + 2].astype(np.float32)
    max_step = float(np.max(np.abs(np.diff(transition_region))))
    # Sem crossfade o salto seria de ~10000; com Hann 20ms o passo máximo entre samples vizinhos é suave (< 1200)
    assert max_step < 1200.0


# ===========================================================================
# 4. SpokenTranscriptLedger & Barge-in (<80ms)
# ===========================================================================


def test_spoken_transcript_ledger_truncates_exact_word_on_barge_in() -> None:
    ledger = SpokenTranscriptLedger(initial_epoch=1)

    # Chunk seq=0 com 8 palavras e duração total 2000ms (250ms por palavra)
    ledger.register_chunk(
        chunk_seq=0,
        clean_text="Olá, meu nome é Orin e posso ajudar",
        duration_ms=2000,
    )

    # Cliente envia barge_in aos 750ms (3 palavras ouvidas: "Olá, meu nome")
    barge_result = ledger.on_barge_in(epoch=1, chunk_seq=0, played_ms=750)

    assert ledger.audio_epoch == 2
    assert barge_result.new_epoch == 2
    assert barge_result.truncated_text == "Olá, meu nome— [interrompido pelo usuário]"
    assert barge_result.unspoken_text == "é Orin e posso ajudar"
    assert "[SYSTEM_BARGE_IN_RECEIPT]" in barge_result.system_receipt
    assert "Olá, meu nome" in barge_result.system_receipt
    assert "é Orin e posso ajudar" in barge_result.system_receipt


def test_spoken_transcript_ledger_ignores_stale_epoch_barge_in() -> None:
    ledger = SpokenTranscriptLedger(initial_epoch=1)
    ledger.register_chunk(
        chunk_seq=0,
        clean_text="Olá, meu nome é Orin e posso ajudar",
        duration_ms=2000,
    )
    first = ledger.on_barge_in(epoch=1, chunk_seq=0, played_ms=750)
    assert first.new_epoch == 2

    # Segundo barge_in atrasado com epoch=1 antigo não deve incrementar para 3 nem corromper o estado
    stale = ledger.on_barge_in(epoch=1, chunk_seq=0, played_ms=250)
    assert stale.ignored_stale_epoch is True
    assert ledger.audio_epoch == 2


# ===========================================================================
# 5. Multiproviders STT & TTS + Sidecar + Launcher Internal Service
# ===========================================================================


@pytest.mark.parametrize("stt_provider", ["local_sidecar", "groq", "openai", "deepgram"])
def test_multi_provider_stt_adapter_supports_all_providers_with_safe_fallback(
    stt_provider: str,
) -> None:
    assert stt_provider in SUPPORTED_STT_PROVIDERS
    adapter = MultiProviderSTTAdapter()

    sr = 16000
    t = np.linspace(0.0, 0.5, int(sr * 0.5), dtype=np.float32)
    pcm_bytes = (np.sin(2.0 * np.pi * 180.0 * t) * 10000).astype(np.int16).tobytes()

    result = adapter.transcribe(
        pcm_bytes,
        sample_rate=sr,
        language="pt",
        provider=stt_provider,
        prompt_hint="Testando transcrição em português",
    )
    assert result.provider == stt_provider
    assert result.language == "pt"
    assert isinstance(result.text, str) and len(result.text) > 0

    # Red-Team: bytes ímpares / truncados ou vazios não devem levantar exceção
    empty_res = adapter.transcribe(b"", sample_rate=sr, provider=stt_provider)
    assert empty_res.text == ""
    odd_res = adapter.transcribe(b"\x01\x02\x03", sample_rate=sr, provider=stt_provider)
    assert isinstance(odd_res.text, str)


@pytest.mark.parametrize(
    ("tts_provider", "tts_model"),
    [
        ("local_sidecar", "chatterbox-pt-br"),
        ("local_sidecar", "f5-tts-pt-br"),
        ("local_sidecar", "kokoro-82m-onnx"),
        ("elevenlabs", "eleven_flash_v2_5"),
        ("cartesia", "sonic-2"),
        ("openai", "gpt-4o-mini-tts"),
    ],
)
def test_multi_provider_tts_adapter_synthesizes_24khz_pcm_and_word_timings(
    tts_provider: str,
    tts_model: str,
) -> None:
    assert tts_provider in SUPPORTED_TTS_PROVIDERS
    adapter = MultiProviderTTSAdapter()
    continuity = ProsodyContinuityEngine(sample_rate=24000)

    chunk = ProsodicChunk(
        chunk_index=0,
        clean_text="Olá, meu nome é Orin",
        tts_text="Olá, meu nome é Orin,",
        boundary_kind=BoundaryKind.COMMA,
        is_first_in_block=True,
    )
    config = VoiceConfig(tts_provider=tts_provider, tts_model=tts_model)
    synth: SynthesizedAudioChunk = adapter.synthesize_chunk(
        chunk,
        voice_config=config,
        continuity_state=continuity.state,
    )

    assert synth.sample_rate == 24000
    assert synth.duration_ms > 0
    assert len(synth.pcm_bytes) > 0
    assert len(synth.pcm_bytes) % 2 == 0  # PCM Int16 alinhado
    assert len(synth.word_timings) == 5  # Olá, / meu / nome / é / Orin
    assert synth.word_timings[0].start_ms == 0
    assert synth.word_timings[-1].end_ms == synth.duration_ms


def test_voice_sidecar_app_and_launcher_internal_service_registration() -> None:
    assert "voice-sidecar" in launcher_internal.SERVICES
    assert DEFAULT_SIDECAR_PORT == 20129

    sidecar_app = create_sidecar_app()
    client = TestClient(sidecar_app)

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    tts_resp = client.post(
        "/v1/tts/synthesize",
        json={
            "text": "Olá, testando o sidecar local!",
            "model_id": "chatterbox-pt-br",
            "voice_id": "orin-default-ptbr",
        },
    )
    assert tts_resp.status_code == 200
    data = tts_resp.json()
    assert data["sample_rate"] == 24000
    assert data["duration_ms"] > 0
    assert len(base64.b64decode(data["pcm_base64"])) > 0

    supervisor = VoiceSidecarSupervisor(port=DEFAULT_SIDECAR_PORT)
    assert supervisor.base_url == f"http://127.0.0.1:{DEFAULT_SIDECAR_PORT}"


# ===========================================================================
# 6. WebSocket `/v1/conversations/{id}/live` & `GET /v1/live-voice/hardware-status`
# ===========================================================================


def _build_test_app() -> FastAPI:
    app = FastAPI()
    register_live_voice_routes(app, services=None, principal_for=None)
    return app


def test_get_live_voice_hardware_status_endpoint() -> None:
    app = _build_test_app()
    client = TestClient(app)

    resp = client.get("/v1/live-voice/hardware-status")
    assert resp.status_code == 200
    payload: dict[str, Any] = resp.json()

    assert "hardware_profile" in payload
    assert "stt_providers" in payload
    assert "tts_providers" in payload
    assert "local_sidecar" in payload["stt_providers"]
    assert "local_sidecar" in payload["tts_providers"]
    assert "elevenlabs" in payload["tts_providers"]


def test_websocket_live_voice_full_duplex_speak_barge_in_and_eou() -> None:
    app = _build_test_app()
    client = TestClient(app)

    with client.websocket_connect("/v1/conversations/conv_friend_01/live") as ws:
        # 1. Evento inicial session.ready
        ready = ws.receive_json()
        assert ready["type"] == "session.ready"
        assert ready["conversation_id"] == "conv_friend_01"
        assert ready["audio_epoch"] == 1
        assert "hardware_profile" in ready

        # 2. Atualiza configuração de voz
        ws.send_json(
            {
                "type": "config",
                "tts_provider": "local_sidecar",
                "tts_model": "chatterbox-pt-br",
                "stt_provider": "local_sidecar",
                "stt_model": "faster-whisper-large-v3-turbo",
            }
        )
        cfg_ack = ws.receive_json()
        assert cfg_ack["type"] == "config.updated"
        assert cfg_ack["voice_config"]["tts_model"] == "chatterbox-pt-br"

        # 3. Envia speak_text com duração controlada de 2000ms para testar o ledger
        ws.send_json(
            {
                "type": "speak_text",
                "text": "<speak>Olá, meu nome é Orin e posso ajudar</speak>",
                "chunk_duration_override_ms": 2000,
            }
        )
        tts_msg = ws.receive_json()
        assert tts_msg["type"] == "tts.chunk"
        assert tts_msg["audio_epoch"] == 1
        assert tts_msg["chunk_seq"] == 0
        assert tts_msg["clean_text"] == "Olá, meu nome é Orin e posso ajudar"
        assert len(tts_msg["word_timings"]) == 8
        assert len(base64.b64decode(tts_msg["pcm_base64"])) > 0

        # 4. Cliente interrompe (barge_in) aos 750ms
        ws.send_json(
            {
                "type": "barge_in",
                "epoch": 1,
                "chunk_seq": 0,
                "played_ms": 750,
            }
        )
        clear_msg = ws.receive_json()
        assert clear_msg["type"] == "audio.clear"
        assert clear_msg["new_epoch"] == 2
        assert clear_msg["truncated_text"] == "Olá, meu nome— [interrompido pelo usuário]"
        assert "[SYSTEM_BARGE_IN_RECEIPT]" in clear_msg["system_receipt"]

        # 5. Cliente envia fim de fala com Hold Marker ("Eu queria saber mas") em 500ms -> aguarda
        ws.send_json(
            {
                "type": "audio_speech_end",
                "transcript_hint": "Eu queria saber mas",
                "silence_ms": 500,
            }
        )
        hold_msg = ws.receive_json()
        assert hold_msg["type"] == "vad.eou_wait"
        assert hold_msg["is_end_of_utterance"] is False
        assert hold_msg["required_silence_ms"] == 1400

        # 6. Cliente envia frase completa com 420ms de silêncio -> emite stt.transcript
        ws.send_json(
            {
                "type": "audio_speech_end",
                "transcript_hint": "Pode rodar a suíte de testes agora?",
                "silence_ms": 420,
            }
        )
        stt_msg = ws.receive_json()
        assert stt_msg["type"] == "stt.transcript"
        assert stt_msg["is_end_of_utterance"] is True
        assert stt_msg["text"] == "Pode rodar a suíte de testes agora?"


def test_websocket_red_team_malformed_inputs_echo_guard_and_buffer_cap() -> None:
    app = _build_test_app()
    client = TestClient(app)

    with client.websocket_connect("/v1/conversations/conv_redteam/live") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "session.ready"

        # 1. JSON malformado não derruba o WebSocket
        ws.send_text("{broken_json")
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "invalid_json"

        # 2. Valores malformados / extremos em config e barge_in são higienizados
        ws.send_json({"type": "config", "tts_provider": "unknown_prov", "speed": 999.0})
        cfg = ws.receive_json()
        assert cfg["type"] == "config.updated"
        assert cfg["voice_config"]["speed"] == 2.0

        # 3. Speak multi-chunk (3 chunks prosódicos PT-BR) + Echo Guard no WebSocket
        ws.send_json(
            {
                "type": "speak_text",
                "text": "<speak>Olá, meu nome é Orin, posso te auxiliar com várias coisas!</speak>",
            }
        )
        c0 = ws.receive_json()
        c1 = ws.receive_json()
        c2 = ws.receive_json()
        assert [c0["clean_text"], c1["clean_text"], c2["clean_text"]] == [
            "Olá",
            "meu nome é Orin",
            "posso te auxiliar com várias coisas",
        ]

        # Microfone capta eco da fala recente do Orin -> vad.echo_discarded
        ws.send_json(
            {
                "type": "audio_speech_end",
                "transcript_hint": "posso te auxiliar com várias coisas",
                "silence_ms": 500,
            }
        )
        echo_ev = ws.receive_json()
        assert echo_ev["type"] == "vad.echo_discarded"


@pytest.mark.parametrize(
    "complete_phrase",
    [
        "Você sabe qual é",
        "Me explica como",
        "Você viu onde",
        "Me diz qual",
        "Quero deixar assim",
    ],
)
def test_ptbr_semantic_eou_detector_does_not_hold_on_complete_question_endings(
    complete_phrase: str,
) -> None:
    detector = PTBRSemanticEOUDetector(fast_timeout_ms=380, normal_timeout_ms=520, hold_timeout_ms=1400)
    decision = detector.evaluate(complete_phrase, silence_ms=520)
    assert decision.detected_hold_marker is None
    assert decision.is_end_of_utterance is True


def test_websocket_eou_wait_reuses_cached_stt_transcript_without_double_transcription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.live_voice.engines import LocalWhisperEngine

    transcribe_calls = 0

    def fake_transcribe(self: LocalWhisperEngine, pcm: bytes, **kwargs: Any) -> str:
        nonlocal transcribe_calls
        transcribe_calls += 1
        return "Eu queria saber mas" if transcribe_calls == 1 else "Nova frase completa agora."

    monkeypatch.setattr(LocalWhisperEngine, "transcribe", fake_transcribe)
    t = np.linspace(0.0, 0.4, int(16000 * 0.4), dtype=np.float32)
    speech_pcm = (np.sin(2.0 * np.pi * 200.0 * t) * 10000).astype(np.int16).tobytes()

    app = _build_test_app()
    client = TestClient(app)
    with client.websocket_connect("/v1/conversations/conv_eou_cache/live") as ws:
        ws.receive_json()  # session.ready
        ws.send_bytes(speech_pcm)
        assert ws.receive_json()["type"] == "vad.frame"

        # 1st audio_speech_end at 500ms -> runs STT once and enters vad.eou_wait
        ws.send_json({"type": "audio_speech_end", "silence_ms": 500, "sample_rate": 16000})
        assert ws.receive_json()["type"] == "stt.processing"
        wait_ev = ws.receive_json()
        assert wait_ev["type"] == "vad.eou_wait"
        assert wait_ev["partial_text"] == "Eu queria saber mas"
        assert transcribe_calls == 1

        # 2nd audio_speech_end at 1500ms without new speech -> MUST reuse cached transcript (0 new STT calls!)
        ws.send_json({"type": "audio_speech_end", "silence_ms": 1500, "sample_rate": 16000})
        final_ev = ws.receive_json()
        assert final_ev["type"] == "stt.transcript"
        assert final_ev["text"] == "Eu queria saber mas"
        assert transcribe_calls == 1

        # New speech frame arrives -> invalidates cache and calls STT for new utterance
        ws.send_bytes(speech_pcm)
        ws.send_json({"type": "audio_speech_end", "silence_ms": 600, "sample_rate": 16000})
        first_after = ws.receive_json()
        if first_after["type"] == "vad.frame":
            first_after = ws.receive_json()
        assert first_after["type"] == "stt.processing"
        second_final = ws.receive_json()
        assert second_final["type"] == "stt.transcript"
        assert second_final["text"] == "Nova frase completa agora."
        assert transcribe_calls == 2


def test_websocket_speak_text_splits_long_text_and_pipelines_without_blocking_barge_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    import time as _time
    from agentos.live_voice import session as session_mod

    synth_calls: list[dict[str, Any]] = []
    in_flight = 0
    max_concurrent = 0
    lock = threading.Lock()

    def fake_synthesize_local(
        runtime: Any,
        engine: str,
        text: str,
        *,
        voice: str,
        speed: float,
        language: str = "pt-BR",
    ) -> tuple[bytes, int]:
        nonlocal in_flight, max_concurrent
        with lock:
            in_flight += 1
            if in_flight > max_concurrent:
                max_concurrent = in_flight
            synth_calls.append({"text": text, "language": language})
        _time.sleep(0.06)
        with lock:
            in_flight -= 1
        return (b"\x01\x00" * 1200), 24000

    monkeypatch.setattr(session_mod, "synthesize_local", fake_synthesize_local)
    app = _build_test_app()
    client = TestClient(app)

    with client.websocket_connect("/v1/conversations/conv_pipeline/live") as ws:
        ws.receive_json()  # session.ready
        ws.send_json({"type": "config", "tts_provider": "kokoro", "tts_model": "kokoro-82m-onnx"})
        assert ws.receive_json()["type"] == "config.updated"

        # 1. Long multi-sentence text (> 120 chars) is split into sentence clauses and pipelined (up to 2 concurrent)
        long_text = (
            "A primeira frase do Orin explica como o motor de voz funciona com baixíssima latência no Brasil. "
            "A segunda frase demonstra que o sintetizador divide textos longos e pré-carrega duas partes em paralelo."
        )
        assert len(long_text) > 120
        ws.send_json({"type": "speak_text", "text": long_text})
        c0 = ws.receive_json()
        assert c0["type"] == "tts.chunk"
        assert len(c0["clean_text"]) <= 120
        c1 = ws.receive_json()
        assert c1["type"] == "tts.chunk"
        assert len(c1["clean_text"]) <= 120
        assert c0["chunk_seq"] == 0
        assert c1["chunk_seq"] == 1
        assert "primeira frase" in c0["clean_text"]
        assert "segunda frase" in c1["clean_text"]
        assert max_concurrent >= 2

        # 2. Barge-in while synthesis is running in background is NOT blocked and suppresses stale epoch chunks
        ws.send_json(
            {
                "type": "speak_text",
                "text": (
                    "Esta frase longa está sendo sintetizada em background quando o usuário interrompe. "
                    "Esta segunda parte jamais deve ser emitida após o corte de barge-in na sessão."
                ),
            }
        )
        ws.send_json({"type": "barge_in", "epoch": 1, "chunk_seq": 1, "played_ms": 200})

        seen_clear = False
        for _ in range(4):
            ev = ws.receive_json()
            if ev["type"] == "tts.chunk":
                # Any chunk emitted before barge_in was processed must belong to epoch 1, NEVER after audio.clear
                assert not seen_clear, "Must never emit stale tts.chunk after audio.clear!"
            elif ev["type"] == "audio.clear":
                seen_clear = True
                assert ev["new_epoch"] == 2
                break
        assert seen_clear is True

        # Wait for background threads to finish and ensure no stale tts.chunk is queued after audio.clear
        _time.sleep(0.15)
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
        assert pong["type"] == "pong"
        assert pong["audio_epoch"] == 2

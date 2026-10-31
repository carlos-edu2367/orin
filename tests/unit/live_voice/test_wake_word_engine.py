from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentos.api.contracts import ApplicationValidationError
from agentos.live_voice.engines import LocalWhisperEngine, pcm16_to_wav
from agentos.live_voice.session import register_live_voice_routes
from agentos.live_voice.wake_word import (
    DEFAULT_WAKE_PROMPT_HINT,
    WakeWordMatch,
    build_wake_prompt_hint,
    match_wake_word,
    phonetic_skeleton_ptbr,
)


@pytest.mark.parametrize(
    "utterance",
    [
        "Orin",
        "orin!",
        "Oi Orin",
        "Oi, Orin!",
        "Ei Orin",
        "Ei, Orin",
        "Fala Orin",
        "Hey Orin",
        "Olá Orin",
        "Ola Orin",
        "Ô Orin",
        "O Orin",
        "Ok Orin",
        "Okay Orin",
        "Escuta Orin",
        "Ouve Orin",
        "E aí Orin",
        "E ai Orin",
        "Aí Orin",
        "Bom dia Orin",
        "Boa tarde, Orin",
        "Boa noite Orin",
        "Acorda Orin",
    ],
)
def test_match_wake_word_recognizes_direct_and_vocative_calls(utterance: str) -> None:
    res = match_wake_word(utterance)
    assert res.detected is True, f"Failed to detect wake word in {utterance!r}"
    assert res.command_remainder == ""
    assert res.confidence >= 0.82


@pytest.mark.parametrize(
    "accent_variant",
    [
        # Sotaque aberto / fechado / nasalização PT-BR
        "Órin",
        "Orim",
        "Órim",
        "Oryn",
        "Orinn",
        "Orrin",
        "Oreen",
        "Orinho",
        "Orinzinho",
        # Ditongação / labialização / aspiração (carioca, nordestino, interior)
        "Ourin",
        "Ourim",
        "Aurin",
        "Aurim",
        "Horin",
        "Horim",
        "Horyn",
        "Aoryn",
        # Substituições comuns do decoder Whisper em PT-BR
        "Orem",
        "Órem",
        "Oren",
        "Oran",
        "Oring",
        "Ori",
        "Orion",
        "Órion",
        # Redução vocálica inicial (o -> u)
        "Urin",
        "Urim",
        "Uri",
        # Troca líquida / tepe alveolar (r -> l / lh / d)
        "Olin",
        "Olim",
        "Olhin",
        "Odin",
        # R retroflexo (caipira/paulista/mineiro/inglês) e segmentação dupla
        "O rin",
        "Oh rin",
        "Ô rin",
        "Ó rin",
        "All in",
        "Hall in",
        "Ronin",
        "Arin",
        "Erin",
        # Com vocativos antes da variante de sotaque
        "Oi Orim",
        "Ei Órem",
        "Fala Ourin",
        "Hey Oryn",
        "Oi Olin",
        "Ei All in",
        "Ô O rin",
    ],
)
def test_match_wake_word_handles_regional_accents_and_stt_variants(accent_variant: str) -> None:
    res = match_wake_word(accent_variant)
    assert res.detected is True, f"Expected accent variant {accent_variant!r} to trigger wake word"
    assert res.command_remainder == ""
    assert res.confidence >= 0.80


@pytest.mark.parametrize(
    ("utterance", "expected_remainder"),
    [
        ("Orin, que horas são agora?", "que horas são agora?"),
        ("Oi Orin, me ajuda a revisar esse código", "me ajuda a revisar esse código"),
        ("Ei Orim! Abre a sessão do projeto backend.", "Abre a sessão do projeto backend."),
        ("Fala Órem, como está o andamento dos testes?", "como está o andamento dos testes?"),
        ("Hey Oryn: cria uma tarefa para amanhã", "cria uma tarefa para amanhã"),
        ("Oi o rin tudo bem por aí?", "tudo bem por aí?"),
        ("All in, qual a previsão do tempo hoje?", "qual a previsão do tempo hoje?"),
    ],
)
def test_match_wake_word_extracts_command_remainder(utterance: str, expected_remainder: str) -> None:
    res = match_wake_word(utterance)
    assert res.detected is True, f"Failed on {utterance!r}"
    assert res.command_remainder == expected_remainder
    assert res.wake_phrase != ""


def test_match_wake_word_supports_custom_companion_name_while_keeping_orin() -> None:
    res_custom = match_wake_word("Oi Sexta-Feira, status do servidor", custom_names=["Sexta-Feira", "Jarvis"])
    assert res_custom.detected is True
    assert res_custom.command_remainder == "status do servidor"

    res_orin = match_wake_word("Ei Orin, status do servidor", custom_names=["Sexta-Feira"])
    assert res_orin.detected is True
    assert res_orin.command_remainder == "status do servidor"


@pytest.mark.parametrize(
    "false_positive_phrase",
    [
        "",
        "   ",
        "...",
        "12345",
        "Olha só isso que legal",
        "Olha, eu acho melhor esperar",
        "Olho no lance",
        "Hora de almoçar pessoal",
        "Horas extras no trabalho",
        "Horário de Brasília",
        "Agora não posso falar",
        "Ordem no tribunal",
        "Onde você colocou a chave?",
        "Ontem choveu bastante",
        "Ouro e prata subiram hoje",
        "Outro dia a gente conversa",
        "Outra coisa importante",
        "Origem do problema foi o banco",
        "Original de fábrica",
        "Orientar a equipe técnica",
        "Orçamento aprovado ontem",
        "Orelha esquerda",
        "Orgulho da família",
        "Orquestra sinfônica",
        "Orla da praia",
        "Oi pessoal, tudo bem com vocês?",
        "Ei cara, me passa o sal por favor",
        "Eu estava conversando com o pessoal ontem e alguém falou orin no meio da frase longa",
    ],
)
def test_match_wake_word_rejects_common_portuguese_collisions_and_mid_sentence_mentions(
    false_positive_phrase: str,
) -> None:
    res = match_wake_word(false_positive_phrase)
    assert res.detected is False, f"False positive triggered on {false_positive_phrase!r}: {res}"


def test_phonetic_skeleton_unifies_accents_to_canonical_orn() -> None:
    for word in ("orin", "órin", "orim", "ourin", "aurin", "horin", "orem", "oren", "urin", "olin", "olhin", "oryn"):
        skel = phonetic_skeleton_ptbr(word)
        assert skel == "ORN", f"Expected ORN for {word!r}, got {skel!r}"


def test_local_whisper_engine_passes_prompt_hint_to_runtime_and_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded_calls: list[dict[str, Any]] = []

    class FakeRuntime:
        def is_installed(self, engine: str) -> bool:
            return engine == "whisper"

        def call(self, engine: str, payload: dict[str, Any]) -> dict[str, Any]:
            recorded_calls.append({"engine": engine, **payload})
            return {"text": "Oi Orin"}

    engine = LocalWhisperEngine(runtime=FakeRuntime())
    out = engine.transcribe(
        b"\x00\x00" * 1600,
        sample_rate=16000,
        language="pt-BR",
        model_id="faster-whisper-small-int8",
        prompt_hint=DEFAULT_WAKE_PROMPT_HINT,
    )
    assert out == "Oi Orin"
    assert len(recorded_calls) == 1
    assert recorded_calls[0]["prompt_hint"] == DEFAULT_WAKE_PROMPT_HINT


def test_whisper_worker_forwards_prompt_hint_to_faster_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import agentos.live_voice.local_runtime as rt_mod

    workers_dir = str(rt_mod.WORKERS_DIR)
    if workers_dir not in sys.path:
        sys.path.insert(0, workers_dir)
    import whisper_worker  # type: ignore[import-not-found]

    captured_kwargs: dict[str, Any] = {}

    class FakeSegment:
        text = "Oi Orin"

    class FakeModel:
        def transcribe(self, audio: Any, **kwargs: Any) -> tuple[list[FakeSegment], dict[str, Any]]:
            captured_kwargs.update(kwargs)
            return [FakeSegment()], {}

    monkeypatch.setattr(whisper_worker, "_model", lambda _mid: (FakeModel(), "cpu"))
    pcm_b64 = base64.b64encode(b"\x00\x00" * 800).decode("ascii")
    res = whisper_worker.transcribe(
        {
            "model": "small",
            "language": "pt-BR",
            "sample_rate": 16000,
            "pcm_base64": pcm_b64,
            "prompt_hint": "Orin. Oi Orin.",
        }
    )
    assert res["text"] == "Oi Orin"
    assert captured_kwargs.get("initial_prompt") == "Orin. Oi Orin."
    assert captured_kwargs.get("vad_filter") is True


def test_match_wake_word_handles_orinz_and_strips_repeated_wake_echo_in_remainder() -> None:
    res_z = match_wake_word("Oi Orinz.")
    assert res_z.detected is True
    assert res_z.command_remainder == ""

    res_echo1 = match_wake_word("Ei Orin, ei Orin.")
    assert res_echo1.detected is True
    assert res_echo1.command_remainder == ""

    res_echo2 = match_wake_word("Orin, ô Orin.")
    assert res_echo2.detected is True
    assert res_echo2.command_remainder == ""


def test_wake_check_endpoint_evaluates_transcript_hint_and_pcm_audio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()

    @app.exception_handler(ApplicationValidationError)
    async def _handle_validation(_req: Any, exc: ApplicationValidationError) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(exc)}, status_code=422)

    captured_prompt_hints: list[str | None] = []

    def fake_transcribe(
        self: Any,
        pcm_bytes: bytes,
        *,
        sample_rate: int,
        language: str = "pt-BR",
        model_id: str = "",
        prompt_hint: str | None = None,
    ) -> str:
        captured_prompt_hints.append(prompt_hint)
        return "Ei Orim, abre o projeto"

    monkeypatch.setattr(LocalWhisperEngine, "transcribe", fake_transcribe)
    monkeypatch.setattr(LocalWhisperEngine, "is_installed", lambda self: True)

    register_live_voice_routes(app)
    client = TestClient(app)

    # 1. Via transcript_hint (ex.: browser recognition ou teste direto)
    r1 = client.post(
        "/v1/live-voice/wake-check",
        json={"transcript_hint": "Fala Órem, como estão os testes?"},
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["detected"] is True
    assert body1["command_remainder"] == "como estão os testes?"

    # 2. Via pcm_base64 (STT local com prompt_hint de wake word)
    short_pcm = base64.b64encode(b"\x00\x10" * 8000).decode("ascii")  # 0.5s @ 16kHz voiced (int16=4096, RMS=0.125)
    r2 = client.post(
        "/v1/live-voice/wake-check",
        json={
            "pcm_base64": short_pcm,
            "sample_rate": 16000,
            "stt_provider": "local_sidecar",
            "custom_wake_words": ["Jarvis"],
        },
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["detected"] is True
    assert body2["matched"] is True
    assert body2["wake_variant"] == "orim"
    assert body2["transcript"] == "Ei Orim, abre o projeto"
    assert body2["command_remainder"] == "abre o projeto"
    assert len(captured_prompt_hints) == 1
    assert captured_prompt_hints == [None]
    assert captured_prompt_hints[0] is None

    # 2b. Via payload exato do frontend (com custom_names e stt_provider='auto')
    r2b = client.post(
        "/v1/live-voice/wake-check",
        json={
            "pcm_base64": short_pcm,
            "sample_rate": 16000,
            "stt_provider": "auto",
            "stt_model": None,
            "custom_names": ["Jarvis"],
        },
    )
    assert r2b.status_code == 200
    body2b = r2b.json()
    assert body2b["matched"] is True
    assert body2b["detected"] is True
    assert body2b["command_remainder"] == "abre o projeto"

    # 3. Falso positivo rejeitado
    r3 = client.post(
        "/v1/live-voice/wake-check",
        json={"transcript_hint": "Olha só que dia bonito hoje"},
    )
    assert r3.status_code == 200
    assert r3.json()["detected"] is False

    # 4. Guarda DoS contra payload de áudio gigante (> 6 segundos @ 16kHz)
    huge_pcm = base64.b64encode(b"\x00\x00" * (16000 * 7)).decode("ascii")
    r4 = client.post(
        "/v1/live-voice/wake-check",
        json={"pcm_base64": huge_pcm, "sample_rate": 16000},
    )
    assert r4.status_code == 422


def test_whisper_worker_and_local_engine_filter_hallucinated_segments_and_keep_vad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    import agentos.live_voice.local_runtime as rt_mod

    workers_dir = str(rt_mod.WORKERS_DIR)
    if workers_dir not in sys.path:
        sys.path.insert(0, workers_dir)
    import whisper_worker  # type: ignore[import-not-found]

    class FakeSegment:
        def __init__(self, text: str, no_speech_prob: float = 0.05, avg_logprob: float = -0.2) -> None:
            self.text = text
            self.no_speech_prob = no_speech_prob
            self.avg_logprob = avg_logprob

    captured_worker_kwargs: dict[str, Any] = {}

    class FakeWorkerModel:
        def transcribe(self, audio: Any, **kwargs: Any) -> tuple[list[FakeSegment], dict[str, Any]]:
            captured_worker_kwargs.update(kwargs)
            return [
                FakeSegment("Oi Orin", no_speech_prob=0.1, avg_logprob=-0.2),
                FakeSegment("Alucinação no silêncio", no_speech_prob=0.85, avg_logprob=-0.2),
                FakeSegment("Ruído incerto", no_speech_prob=0.2, avg_logprob=-1.35),
            ], {}

    monkeypatch.setattr(whisper_worker, "_model", lambda _mid: (FakeWorkerModel(), "cpu"))
    pcm_b64 = base64.b64encode(b"\x00\x10" * 800).decode("ascii")
    res_worker = whisper_worker.transcribe(
        {
            "model": "small",
            "language": "pt-BR",
            "sample_rate": 16000,
            "pcm_base64": pcm_b64,
            "prompt_hint": DEFAULT_WAKE_PROMPT_HINT,
        }
    )
    assert res_worker["text"] == "Oi Orin"
    assert captured_worker_kwargs.get("vad_filter") is True

    class FakeUninstalledRuntime:
        def is_installed(self, _engine: str) -> bool:
            return False

    captured_engine_kwargs: dict[str, Any] = {}

    class FakeInProcessModel:
        def transcribe(self, audio: Any, **kwargs: Any) -> tuple[list[FakeSegment], dict[str, Any]]:
            captured_engine_kwargs.update(kwargs)
            return [
                FakeSegment("Alucinação de silêncio", no_speech_prob=0.72, avg_logprob=-0.2),
                FakeSegment("Baixa confiança", no_speech_prob=0.1, avg_logprob=-1.15),
                FakeSegment("Fala Orin", no_speech_prob=0.12, avg_logprob=-0.25),
            ], {}

    engine = LocalWhisperEngine(runtime=FakeUninstalledRuntime())
    monkeypatch.setattr(engine, "importable", lambda: True)
    monkeypatch.setattr(engine, "_load_in_process", lambda _mid: FakeInProcessModel())
    text = engine.transcribe(
        b"\x00\x10" * 1600,
        sample_rate=16000,
        language="pt-BR",
        model_id="small",
        prompt_hint=DEFAULT_WAKE_PROMPT_HINT,
    )
    assert text == "Fala Orin"
    assert captured_engine_kwargs.get("vad_filter") is True


def test_wake_check_endpoint_rejects_silent_or_low_energy_pcm_without_calling_stt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    transcribe_calls = 0

    def fake_transcribe(self: Any, pcm_bytes: bytes, **kwargs: Any) -> str:
        nonlocal transcribe_calls
        transcribe_calls += 1
        return "Oi Orin"

    monkeypatch.setattr(LocalWhisperEngine, "transcribe", fake_transcribe)
    monkeypatch.setattr(LocalWhisperEngine, "is_installed", lambda self: True)

    register_live_voice_routes(app)
    client = TestClient(app)

    # 1. Silêncio / ruído de fundo baixo (RMS ~0.00048)
    silent_pcm = base64.b64encode(b"\x10\x00" * 8000).decode("ascii")
    r_silent = client.post(
        "/v1/live-voice/wake-check",
        json={"pcm_base64": silent_pcm, "sample_rate": 16000, "stt_provider": "local_sidecar"},
    )
    assert r_silent.status_code == 200
    assert r_silent.json()["matched"] is False
    assert r_silent.json()["detected"] is False
    assert transcribe_calls == 0

    # 2. Clique curto de 2 frames (64ms) cercado por 23 frames de silêncio
    click_pcm = (b"\x00\x00" * (512 * 10)) + (b"\x00\x10" * (512 * 2)) + (b"\x00\x00" * (512 * 13))
    r_click = client.post(
        "/v1/live-voice/wake-check",
        json={
            "pcm_base64": base64.b64encode(click_pcm).decode("ascii"),
            "sample_rate": 16000,
            "stt_provider": "local_sidecar",
        },
    )
    assert r_click.status_code == 200
    assert r_click.json()["matched"] is False
    assert transcribe_calls == 0


@pytest.mark.parametrize(
    "false_word",
    [
        "além",
        "alma",
        "aluno",
        "aluna",
        "eram",
        "arma",
        "aroma",
        "arena",
        "urna",
        "urina",
        "elena",
        "aline",
    ],
)
def test_phonetic_skeleton_and_match_wake_word_reject_everyday_words(false_word: str) -> None:
    assert phonetic_skeleton_ptbr(false_word) != "ORN"
    assert match_wake_word(false_word).detected is False
    assert match_wake_word(f"Oi {false_word}").detected is False


@pytest.mark.parametrize(
    "true_variant",
    ["orin", "oi orin", "orim", "oryn", "ourin", "olin", "horin", "aurin"],
)
def test_match_wake_word_still_matches_real_variants(true_variant: str) -> None:
    assert match_wake_word(true_variant).detected is True


def test_multi_provider_stt_adapter_rejects_low_rms_and_never_echoes_prompt_hint() -> None:
    from agentos.live_voice.providers import MultiProviderSTTAdapter

    transport_called = False

    def fake_transport(*_args: Any, **_kwargs: Any) -> str:
        nonlocal transport_called
        transport_called = True
        return "Oi Orin"

    adapter_with_transport = MultiProviderSTTAdapter(transport_fn=fake_transport)
    low_rms_pcm = b"\x0a\x00" * 8000
    res_low = adapter_with_transport.transcribe(
        low_rms_pcm,
        sample_rate=16000,
        language="pt",
        prompt_hint="Assistente Orin. Oi Orin.",
    )
    assert res_low.text == ""
    assert res_low.confidence == 0.0
    assert transport_called is False

    voiced_pcm = b"\x00\x10" * 8000
    adapter_no_transport = MultiProviderSTTAdapter(transport_fn=None)
    res_fallback = adapter_no_transport.transcribe(
        voiced_pcm,
        sample_rate=16000,
        language="pt",
        prompt_hint="Assistente Orin. Oi Orin.",
    )
    assert res_fallback.text == ""
    assert res_fallback.confidence == 0.0


def test_adversarial_wake_energy_gate_and_phonetic_edge_cases() -> None:
    from agentos.live_voice.session import _has_sufficient_wake_speech_energy

    # 1. Empty / 1-byte / odd-byte / sub-6-frame buffers rejected
    assert _has_sufficient_wake_speech_energy(b"") is False
    assert _has_sufficient_wake_speech_energy(b"\xff") is False
    assert _has_sufficient_wake_speech_energy(b"\x00\x20" * (512 * 5)) is False  # only 5 frames (< 6)

    # 2. 6 frames with RMS ~0.021 (>= 0.020) but peak_rms < 0.025 rejected (int16 = 700 -> 700/32768 = 0.02136)
    borderline_frame = int(700).to_bytes(2, "little", signed=True) * 512
    assert _has_sufficient_wake_speech_energy(borderline_frame * 8, sample_rate=16000) is False

    # 3. 6+ voiced frames with at least 1 peak frame >= 0.025 accepted, even with odd trailing byte
    peak_frame = int(1200).to_bytes(2, "little", signed=True) * 512  # 1200/32768 = 0.0366 >= 0.025
    valid_mix = (borderline_frame * 5) + peak_frame + b"\x7f"
    assert _has_sufficient_wake_speech_energy(valid_mix, sample_rate=16000) is True

    # 4. Phonetic skeleton edge cases (empty, only 'h', trailing vowels after nasal)
    for edge in ("", "   ", "!!!", "h", "hhh", "orna", "orne", "orno", "orma", "urano", "urbano"):
        assert phonetic_skeleton_ptbr(edge) != "ORN"
        assert match_wake_word(edge).detected is False



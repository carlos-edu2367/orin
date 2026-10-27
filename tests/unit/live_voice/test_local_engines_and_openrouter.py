from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentos.live_voice import local_runtime as runtime_module
from agentos.live_voice.engines import (
    CloudCredential,
    OpenRouterVoiceCatalog,
    VoiceEngineError,
    synthesize_local,
    synthesize_openrouter_speech,
    transcribe_openrouter,
)
from agentos.live_voice.local_runtime import (
    ENGINE_SPECS,
    PYTORCH_CUDA_INDEX,
    LocalEngineError,
    LocalEngineRuntime,
    WorkerProcess,
)
from agentos.live_voice.session import register_live_voice_routes

OPENROUTER = CloudCredential("openrouter", "https://openrouter.test/api/v1", "sk-or-test")

FAKE_TTS_WORKER = '''
import sys
from _protocol import pcm16_base64, serve

def load(_req):
    return {"device": "cpu"}

def synthesize(req):
    if req.get("text") == "boom":
        raise RuntimeError("engine exploded")
    if req.get("text") == "die":
        sys.exit(3)
    samples = [0.25] * 2400
    return {"pcm_base64": pcm16_base64(samples), "sample_rate": 24000, "echo": req}

serve({"load": load, "synthesize": synthesize})
'''


@pytest.fixture()
def fake_workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workers = tmp_path / "workers"
    workers.mkdir()
    shutil.copy(runtime_module.WORKERS_DIR / "_protocol.py", workers / "_protocol.py")
    for spec in ENGINE_SPECS.values():
        (workers / spec.worker).write_text(FAKE_TTS_WORKER, encoding="utf-8")
    monkeypatch.setattr(runtime_module, "WORKERS_DIR", workers)
    return workers


def _installed_runtime(root: Path, *engines: str) -> LocalEngineRuntime:
    runtime = LocalEngineRuntime(root=root, gpu_probe=lambda: False)
    runtime.python_path = lambda _engine: Path(sys.executable)  # type: ignore[method-assign]
    for engine in engines:
        runtime.engine_dir(engine).mkdir(parents=True, exist_ok=True)
        (runtime.engine_dir(engine) / "installed.json").write_text(json.dumps({"device": "cpu"}), encoding="utf-8")
    return runtime


def test_worker_protocol_round_trip_errors_and_crash_recovery(tmp_path: Path, fake_workers: Path) -> None:
    worker = WorkerProcess([sys.executable, str(fake_workers / "kokoro_worker.py")], log_path=tmp_path / "w.log")
    try:
        response = worker.request({"op": "synthesize", "text": "Olá"}, timeout=30)
        assert response["sample_rate"] == 24000
        assert len(base64.b64decode(response["pcm_base64"])) == 4800
        with pytest.raises(LocalEngineError, match="engine exploded"):
            worker.request({"op": "synthesize", "text": "boom"}, timeout=30)
        with pytest.raises(LocalEngineError) as crashed:
            worker.request({"op": "synthesize", "text": "die"}, timeout=30)
        assert crashed.value.code == "local_engine_crashed"
        # The next request starts a fresh worker.
        assert worker.request({"op": "ping"}, timeout=30)["pong"] is True
    finally:
        worker.stop()


def test_synthesize_local_passes_kokoro_voice_and_chatterbox_clip(tmp_path: Path, fake_workers: Path) -> None:
    runtime = _installed_runtime(tmp_path, "kokoro", "chatterbox")
    try:
        pcm, rate = synthesize_local(runtime, "kokoro", "Oi", voice="pm_alex", speed=1.1)
        assert rate == 24000 and len(pcm) == 4800
        clip = runtime.save_voice_clip("Minha voz", "ref.wav", base64.b64encode(b"RIFF....WAVE").decode())
        assert clip == {"id": "Minha voz.wav", "name": "Minha voz"}
        response = runtime.call("chatterbox", {"op": "synthesize", "text": "Oi", "voice_path": str(runtime.voice_clip_path(clip["id"]))})
        assert response["echo"]["voice_path"].endswith("Minha voz.wav")
        assert runtime.list_voice_clips() == [clip]
        assert runtime.delete_voice_clip(clip["id"]) is True
        assert runtime.voice_clip_path("../installed.json") is None
    finally:
        runtime.stop_all()


def test_synthesize_local_reports_missing_engine(tmp_path: Path) -> None:
    with pytest.raises(VoiceEngineError) as caught:
        synthesize_local(LocalEngineRuntime(root=tmp_path), "kokoro", "Oi", voice="pf_dora", speed=1.0)
    assert caught.value.code == "local_tts_unavailable"
    assert caught.value.hint == "install:kokoro"


def test_install_creates_isolated_env_adds_cuda_torch_index_and_verifies_engine(tmp_path: Path, fake_workers: Path) -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    runtime = LocalEngineRuntime(root=tmp_path, runner=runner, gpu_probe=lambda: True, downloader=lambda _u, target: target.write_bytes(b"x"))
    runtime.python_path = lambda _engine: Path(sys.executable)  # type: ignore[method-assign]
    runtime.ensure_uv = lambda: Path("/usr/bin/uv")  # type: ignore[method-assign]
    try:
        runtime.start_install("chatterbox")
        deadline = time.monotonic() + 30
        while runtime.status("chatterbox")["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
        status = runtime.status("chatterbox")
        assert status["state"] == "installed", status
        assert status["device"] == "cpu"
        install = next(argv for argv in calls if argv[1:3] == ["pip", "install"])
        assert "chatterbox-tts==0.1.7" in install
        assert install[install.index("--extra-index-url") + 1] == PYTORCH_CUDA_INDEX
        # Kokoro brings its model files with it and never touches the torch index.
        runtime.start_install("kokoro")
        while runtime.status("kokoro")["state"] == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert runtime.status("kokoro")["state"] == "installed"
        assert (runtime.engine_dir("kokoro") / "kokoro-v1.0.onnx").is_file()
        kokoro_install = [argv for argv in calls if argv[1:3] == ["pip", "install"]][-1]
        assert "--extra-index-url" not in kokoro_install
    finally:
        runtime.stop_all()


def test_failed_install_is_reported_with_its_reason(tmp_path: Path) -> None:
    def runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "error: no matching distribution for torch")

    runtime = LocalEngineRuntime(root=tmp_path, runner=runner, gpu_probe=lambda: False)
    runtime.ensure_uv = lambda: Path("/usr/bin/uv")  # type: ignore[method-assign]
    runtime.start_install("whisper")
    deadline = time.monotonic() + 10
    while runtime.status("whisper")["state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    status = runtime.status("whisper")
    assert status["state"] == "failed"
    assert "no matching distribution" in status["message"]
    assert status["installed"] is False


def test_voice_clip_upload_validates_type_and_size(tmp_path: Path) -> None:
    runtime = LocalEngineRuntime(root=tmp_path)
    with pytest.raises(ValueError):
        runtime.save_voice_clip("x", "virus.exe", base64.b64encode(b"MZ").decode())
    with pytest.raises(ValueError):
        runtime.save_voice_clip("x", "a.wav", "not base64!!")
    first = runtime.save_voice_clip("Eu", "a.wav", base64.b64encode(b"1").decode())
    second = runtime.save_voice_clip("Eu", "b.wav", base64.b64encode(b"2").decode())
    assert first["id"] != second["id"]


def _speech_catalog(request: httpx.Request) -> httpx.Response:
    modality = request.url.params.get("output_modalities")
    if modality == "speech":
        return httpx.Response(200, json={"data": [
            {"id": "elevenlabs/eleven-v4", "name": "ElevenLabs: Eleven v4", "supported_voices": ["george", "sarah"]},
            {"id": "elevenlabs/eleven-v3", "name": "ElevenLabs: Eleven v3", "supported_voices": ["adam"]},
            {"id": "google/gemini-3.8-flash-tts", "name": "Gemini TTS", "supported_voices": ["Kore", "Puck"]},
            {"id": "fish-audio/s2-pro", "name": "Fish S2", "supported_voices": None},
        ]})
    return httpx.Response(200, json={"data": [{"id": "openai/whisper-large-v3-turbo", "name": "Whisper"}]})


def test_openrouter_catalog_keeps_each_models_own_voices_and_caches() -> None:
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return _speech_catalog(request)

    catalog = OpenRouterVoiceCatalog(base_url="https://openrouter.test/api/v1", transport=httpx.MockTransport(handler))
    models = {m["id"]: m for m in catalog.speech_models()}
    assert models["elevenlabs/eleven-v4"]["voices"] == ["george", "sarah"]
    assert models["elevenlabs/eleven-v3"]["voices"] == ["adam"]
    assert models["fish-audio/s2-pro"]["voices"] == []
    assert models["google/gemini-3.8-flash-tts"]["supports_instructions"] is True
    assert models["elevenlabs/eleven-v3"]["supports_instructions"] is False
    catalog.speech_models()
    assert len(hits) == 1
    assert catalog.transcription_models()[0]["id"] == "openai/whisper-large-v3-turbo"


def test_openrouter_speech_prefers_mp3_and_falls_back_to_pcm() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        bodies.append(body)
        assert request.headers["authorization"] == "Bearer sk-or-test"
        if body["model"].startswith("google/") and body["response_format"] == "mp3":
            return httpx.Response(400, json={"error": {"message": "only pcm"}})
        if body["response_format"] == "pcm":
            return httpx.Response(200, content=b"\x00\x01" * 100, headers={"content-type": "audio/pcm;rate=24000"})
        return httpx.Response(200, content=b"ID3mp3", headers={"content-type": "audio/mpeg"})

    transport = httpx.MockTransport(handler)
    audio, encoding, _ = synthesize_openrouter_speech(OPENROUTER, "Oi", model_id="elevenlabs/eleven-v3", voice="adam", instructions="calmo", speed=1.3, transport=transport)
    assert (audio, encoding) == (b"ID3mp3", "mp3")
    assert bodies[0] == {"model": "elevenlabs/eleven-v3", "input": "Oi", "voice": "adam", "response_format": "mp3"}
    audio, encoding, rate = synthesize_openrouter_speech(OPENROUTER, "Oi", model_id="google/gemini-3.8-flash-tts", voice="Kore", instructions="calmo", transport=transport)
    assert (encoding, rate, len(audio)) == ("pcm16", 24000, 200)
    assert bodies[-1]["instructions"] == "calmo"


def test_openrouter_transcription_sends_base64_wav() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.read()))
        return httpx.Response(200, json={"text": " Tudo certo! ", "usage": {"seconds": 1}})

    pcm = (np.ones(1600, dtype=np.int16) * 1000).tobytes()
    text = transcribe_openrouter(OPENROUTER, pcm, sample_rate=16000, transport=httpx.MockTransport(handler))
    assert text == "Tudo certo!"
    assert seen["model"] == "openai/whisper-large-v3-turbo"
    assert seen["input_audio"]["format"] == "wav"
    assert base64.b64decode(seen["input_audio"]["data"])[:4] == b"RIFF"
    assert seen["language"] == "pt"


def test_websocket_speaks_with_local_kokoro_and_engine_routes(tmp_path: Path, fake_workers: Path) -> None:
    runtime = _installed_runtime(tmp_path, "kokoro")
    app = FastAPI()
    register_live_voice_routes(app, runtime=runtime, openrouter_catalog=OpenRouterVoiceCatalog(transport=httpx.MockTransport(_speech_catalog)))
    client = TestClient(app, raise_server_exceptions=False)
    try:
        assert client.get("/v1/live-voice/engines/kokoro/install").json()["installed"] is True
        assert client.get("/v1/live-voice/engines/nope/install").status_code in {404, 500}
        uploaded = client.post("/v1/live-voice/engines/chatterbox/voices", json={"name": "Carlos", "filename": "c.wav", "data_base64": base64.b64encode(b"RIFF").decode()})
        assert uploaded.status_code == 201
        caps = client.get("/v1/live-voice/capabilities").json()
        assert caps["tts"]["kokoro"]["available"] is True
        assert [c["name"] for c in caps["tts"]["chatterbox"]["voice_clips"]] == ["Voz padrão do Chatterbox", "Carlos"]
        assert client.delete(f"/v1/live-voice/engines/chatterbox/voices/{uploaded.json()['id']}").status_code == 200

        with client.websocket_connect("/v1/conversations/c1/live") as ws:
            ws.receive_json()
            ws.send_json({"type": "config", "tts_provider": "kokoro", "tts_model": "kokoro-82m-onnx", "voice_id": "pm_alex"})
            assert ws.receive_json()["voice_config"]["tts_provider"] == "kokoro"
            ws.send_json({"type": "speak_text", "text": "<speak>Olá, Carlos!</speak>"})
            chunk = ws.receive_json()
            assert chunk["type"] == "tts.chunk"
            assert chunk["encoding"] == "pcm16"
            assert chunk["sample_rate"] == 24000
            assert chunk["clean_text"] == "Olá, Carlos!"
            assert chunk["duration_ms"] == 100
    finally:
        runtime.stop_all()

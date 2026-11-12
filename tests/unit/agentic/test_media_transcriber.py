"""The audio/video transcription process: a short agy run with a Gemini model and only the gated native read_file."""
from __future__ import annotations

import json
from pathlib import Path
import textwrap

import pytest

from agentos.agentic.media_transcriber import (
    AgyMediaTranscriber,
    DEFAULT_TRANSCRIPTION_MODEL,
    MediaTranscriptionError,
    media_kind,
)
from agentos.providers.antigravity_cli import MEDIA_GATE_CONFIG_FILENAME, wire_antigravity_media_reader

_FAKE_AGY = textwrap.dedent(
    '''
    import json, os, sys
    from pathlib import Path

    MODE = {mode!r}
    argv = sys.argv[1:]
    cwd = Path.cwd()
    report = {{
        "argv": argv,
        "cwd": str(cwd),
        "settings": json.loads((cwd / ".agents" / "orin_settings.json").read_text(encoding="utf-8")),
        "gate": json.loads((cwd / ".agents" / "orin_media_gate.json").read_text(encoding="utf-8")),
        "hooks": (cwd / ".agents" / "hooks.json").is_file(),
        "agent_md": (cwd / ".agents" / "agents" / "orin.md").read_text(encoding="utf-8"),
        "media": sorted(p.name for p in (cwd / "media").iterdir()),
    }}
    Path({report!r}).write_text(json.dumps(report), encoding="utf-8")
    prompt = argv[argv.index("-p") + 1]
    media_path = next(tok for tok in prompt.split() if tok.replace("\\\\", "/").endswith(("input.wav", "input.mp4")))

    def emit(packet):
        sys.stdout.write(json.dumps(packet) + "\\n")
        sys.stdout.flush()

    conv = "conv-1"
    emit({{"event": "init", "conversation_id": conv, "init": {{"model": argv[argv.index("--model") + 1]}}}})
    step = lambda state, name, params, **extra: emit({{"event": "step_update", "step_update": {{
        "conversation_id": conv, "step_index": 1, "state": state, "step_type": "tool", "tool_name": name,
        "tool_info": {{"name": name, "parameters": params, **extra}}}}}})
    if MODE == "forbidden":
        step("ACTIVE", "run_command", {{"CommandLine": "whoami"}})
        sys.stdin.read()
        sys.exit(0)
    if MODE == "outside":
        step("ACTIVE", "read_file", {{"Path": "C:/Windows/win.ini"}})
        step("DONE", "read_file", {{"Path": "C:/Windows/win.ini"}})
        sys.stdin.read()
        sys.exit(0)
    step("ACTIVE", "read_file", {{"Path": media_path}})
    step("DONE", "read_file", {{"Path": media_path}})
    if MODE == "error":
        emit({{"event": "result", "result": {{"conversation_id": conv, "status": "ERROR", "error": "quota exceeded"}}}})
        sys.exit(1)
    text = "" if MODE == "empty" else "The blue train leaves at 9:15 from platform 3."
    emit({{"event": "result", "result": {{"conversation_id": conv, "status": "SUCCESS", "response": text,
          "duration_seconds": 0.1, "num_turns": 1, "usage": {{"input_tokens": 5, "output_tokens": 5}}}}}})
    '''
)

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64


def _fake(tmp_path: Path, mode: str = "ok") -> tuple[str, Path]:
    report = tmp_path / "report.json"
    script = tmp_path / "fake_agy.py"
    script.write_text(_FAKE_AGY.format(mode=mode, report=str(report)), encoding="utf-8")
    return str(script), report


def _audio(tmp_path: Path, name: str = "fala.wav", data: bytes = WAV) -> Path:
    path = tmp_path / "ws" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)
    return path


def test_transcription_runs_agy_with_gemini_flash_and_only_the_gated_native_read_file(tmp_path: Path) -> None:
    binary, report = _fake(tmp_path)
    source = _audio(tmp_path)
    text = AgyMediaTranscriber(binary=binary, timeout_seconds=60).transcribe(source, question="o que diz?")
    assert text == "The blue train leaves at 9:15 from platform 3."

    seen = json.loads(report.read_text(encoding="utf-8"))
    argv = seen["argv"]
    assert argv[argv.index("--model") + 1] == DEFAULT_TRANSCRIPTION_MODEL
    assert argv[argv.index("--agent") + 1] == "orin" and "--settings" in argv
    assert seen["media"] == ["input.wav"]  # a copy: the gate root holds nothing else
    assert seen["hooks"] is True
    gate = seen["gate"]
    assert gate["unrestricted"] is False and len(gate["allowed_roots"]) == 1
    assert gate["allowed_roots"][0].replace("\\", "/").endswith("/media")
    assert set(gate["allowed_extensions"]) >= {".wav", ".mp4"} and ".png" not in gate["allowed_extensions"]
    allow = seen["settings"]["permissions"]["allow"]
    assert allow == [f"read_file({Path(gate['allowed_roots'][0]).as_posix()})"]
    assert "write_file(*)" in seen["settings"]["permissions"]["deny"]
    assert "tools: [read_file]" in seen["agent_md"] and "mcpServers" not in seen["agent_md"]
    assert not Path(seen["cwd"]).exists()  # the temporary folder is gone


def test_transcription_of_a_video_uses_the_mp4_copy_and_a_visual_prompt(tmp_path: Path) -> None:
    binary, report = _fake(tmp_path)
    mp4 = _audio(tmp_path, "clip.mp4", b"\x00\x00\x00\x20ftypisom" + b"\x00" * 40)
    assert media_kind(mp4) == "video" and media_kind("a.wav") == "audio" and media_kind("a.webm") == "video"
    assert AgyMediaTranscriber(binary=binary, timeout_seconds=60).transcribe(mp4)
    seen = json.loads(report.read_text(encoding="utf-8"))
    prompt = seen["argv"][seen["argv"].index("-p") + 1]
    assert "Watch it" in prompt and seen["media"] == ["input.mp4"]


def test_a_forbidden_tool_or_a_read_outside_the_gate_stops_the_process(tmp_path: Path) -> None:
    source = _audio(tmp_path)
    for mode in ("forbidden", "outside"):
        binary, _ = _fake(tmp_path, mode)
        with pytest.raises(MediaTranscriptionError, match="ferramenta não permitida"):
            AgyMediaTranscriber(binary=binary, timeout_seconds=60).transcribe(source)


def test_failures_are_clear_errors(tmp_path: Path) -> None:
    source = _audio(tmp_path)
    binary, _ = _fake(tmp_path, "error")
    with pytest.raises(MediaTranscriptionError, match="quota exceeded"):
        AgyMediaTranscriber(binary=binary, timeout_seconds=60).transcribe(source)
    binary, _ = _fake(tmp_path, "empty")
    with pytest.raises(MediaTranscriptionError, match="não devolveu texto"):
        AgyMediaTranscriber(binary=binary, timeout_seconds=60).transcribe(source)
    with pytest.raises(MediaTranscriptionError, match="não foi encontrado"):
        AgyMediaTranscriber(binary=binary).transcribe(tmp_path / "ws" / "missing.wav")
    with pytest.raises(MediaTranscriptionError, match="Tipo de arquivo"):
        AgyMediaTranscriber(binary=binary).transcribe(_audio(tmp_path, "nota.txt", b"hello"))
    with pytest.raises(MediaTranscriptionError, match="vazio"):
        AgyMediaTranscriber(binary=binary).transcribe(_audio(tmp_path, "vazio.wav", b""))


def test_a_non_gemini_model_override_is_ignored_and_availability_follows_the_binary(tmp_path: Path) -> None:
    binary, _ = _fake(tmp_path)
    assert AgyMediaTranscriber(binary=binary, cli_model="claude-opus-5-5-high")._model() == DEFAULT_TRANSCRIPTION_MODEL
    assert AgyMediaTranscriber(binary=binary, cli_model="gemini-4-argon-high")._model() == "gemini-4-argon-high"
    assert AgyMediaTranscriber(binary=binary).available() is True


def test_the_media_reader_wiring_refuses_a_model_that_does_not_hear(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    with pytest.raises(ValueError):
        wire_antigravity_media_reader(tmp_path / "run", media_dir=media, model="claude-opus-5-5-high", instructions="x")
    wiring = wire_antigravity_media_reader(
        tmp_path / "run2", media_dir=media, model="gemini-3.8-flash-medium", instructions="Transcribe."
    )
    assert (tmp_path / "run2" / ".agents" / MEDIA_GATE_CONFIG_FILENAME) in wiring.files

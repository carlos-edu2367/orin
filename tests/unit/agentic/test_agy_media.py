"""Media on the Antigravity CLI: tool images saved in the workspace, view_file on audio/video, subagent vision,
and the attachment marker."""
from __future__ import annotations

import base64
import os
from pathlib import Path
import struct
import time
import zlib
from typing import Any

import pytest

from agentos.agentic.agent_tools import AgentToolset
from agentos.agentic.media_transcriber import MediaTranscriptionError
from agentos.agentic.native_turn import RuntimeToolExecutor, TOOL_USE_ID_META_KEY
from agentos.agentic.runtime import AgenticLimits, AgenticTurnRuntime
from agentos.agentic.session import TurnSession
from agentos.agentic.tool_media import (
    TOOL_MEDIA_DIRECTORY,
    TOOL_MEDIA_TTL_SECONDS,
    purge_tool_media,
    save_tool_images,
    tool_media_note,
)
from agentos.agentic.tools.public_names import public_text
from agentos.agentic.workspace import ConversationWorkspace
from agentos.conversations.chat import _attachment_marker
from agentos.providers.antigravity_cli.adapter import AntigravityCliAdapter
from agentos.providers.cli_tools_mcp import ToolCallRequest
from tests.unit.agentic.test_native_turn import FakeNativeProvider, FakeStore, _make_toolset
from tests.unit.agentic.test_parallel_and_background_subagents import _text_batch
from tests.unit.agentic.test_pinned_agent_models import (
    _definition,
    _Routing,
    _Scripted,
    _session,
    agents_store,  # noqa: F401 - the fixture
)


def _png(width: int = 8, height: int = 8) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# --- tool_media ----------------------------------------------------------------------------------


def test_save_tool_images_writes_under_tool_media_and_names_the_absolute_path(tmp_path: Path) -> None:
    saved = save_tool_images(
        tmp_path,
        "call_1",
        [{"data": _b64(_png()), "mime_type": "image/png"}, {"data": _b64(b"\xff\xd8\xffjpeg"), "mime_type": "image/jpeg"}],
    )
    assert [path.name for path in saved] == ["call_1-0.png", "call_1-1.jpg"]
    assert all(path.is_absolute() and path.parent == (tmp_path / TOOL_MEDIA_DIRECTORY).resolve() for path in saved)
    assert saved[0].read_bytes() == _png()
    note = tool_media_note(saved[:1])
    assert note == (
        f"Imagem salva em {saved[0].as_posix()}. "
        "Para ver, chame sua ferramenta nativa read_file com esse caminho."
    )
    assert "Imagens salvas em" in tool_media_note(saved)
    assert tool_media_note([]) == ""


def test_save_tool_images_skips_bad_data_unknown_types_and_sanitizes_the_call_id(tmp_path: Path) -> None:
    assert save_tool_images(tmp_path, "c", [{"data": "", "mime_type": "image/png"}]) == []
    assert save_tool_images(tmp_path, "c", [{"data": _b64(b"x"), "mime_type": "image/svg+xml"}]) == []
    saved = save_tool_images(tmp_path, "../../evil/id", [{"data": _b64(_png()), "mime_type": "image/png"}])
    assert len(saved) == 1
    assert saved[0].parent == (tmp_path / TOOL_MEDIA_DIRECTORY).resolve()
    assert ".." not in saved[0].name


def test_purge_tool_media_removes_only_stale_files(tmp_path: Path) -> None:
    old, fresh = save_tool_images(
        tmp_path, "c", [{"data": _b64(_png()), "mime_type": "image/png"}, {"data": _b64(_png(9, 9)), "mime_type": "image/png"}]
    )
    long_ago = time.time() - TOOL_MEDIA_TTL_SECONDS - 60
    os.utime(old, (long_ago, long_ago))
    assert purge_tool_media(tmp_path) == 1
    assert not old.exists() and fresh.exists()


# --- the executor: the image is saved, the note added, the inline block kept ----------------------


def _executor(tmp_path: Path, *, offload: bool, **toolset_kwargs: Any) -> tuple[RuntimeToolExecutor, ConversationWorkspace]:
    store = FakeStore()
    workspace = ConversationWorkspace(tmp_path, "c1")
    (workspace.root / "shot.png").write_bytes(_png(32, 32))
    toolset = _make_toolset(workspace, model_sees_images=True, **toolset_kwargs)
    runtime = AgenticTurnRuntime(
        store=store,
        provider=FakeNativeProvider(),
        toolset=toolset,
        limits=AgenticLimits(max_iterations=10, max_actions=10, context_window_tokens=1_000_000),
    )
    schemas = runtime._names.schemas_out(runtime._tool_schemas(store.turn))
    executor = RuntimeToolExecutor(runtime, store.turn, tool_schemas=schemas, offload_tool_images=offload)
    return executor, workspace


def _view(executor: RuntimeToolExecutor, call_id: str = "toolu_img1"):
    return executor.call_tool(
        ToolCallRequest(
            name="ViewFile",
            arguments={"path": "shot.png"},
            meta={TOOL_USE_ID_META_KEY: call_id, "message_id": "msg_1"},
        )
    )


def test_executor_saves_the_tool_image_and_keeps_the_inline_block(tmp_path: Path) -> None:
    executor, workspace = _executor(tmp_path, offload=True, cli_native_media=True)
    result = _view(executor)
    assert result.is_error is False
    assert len(result.images) == 1  # the inline block stays
    saved = sorted((workspace.root / TOOL_MEDIA_DIRECTORY).glob("toolu_img1-*.*"))
    assert len(saved) == 1
    assert f"Imagem salva em {saved[0].resolve().as_posix()}." in result.text
    assert "ferramenta nativa read_file" in result.text
    # view_file names the original file too, so a model that calls it out of habit recovers.
    assert (workspace.root / "shot.png").resolve().as_posix() in result.text


def test_executor_without_the_capability_adds_no_note_and_writes_no_file(tmp_path: Path) -> None:
    executor, workspace = _executor(tmp_path, offload=False)
    result = _view(executor)
    assert len(result.images) == 1
    assert "Imagem salva em" not in result.text
    assert "Arquivo original" not in result.text
    assert not (workspace.root / TOOL_MEDIA_DIRECTORY).exists()


def test_the_antigravity_adapter_turns_the_offload_on_and_the_others_do_not() -> None:
    from agentos.providers.claude_cli.adapter import ClaudeCliAdapter
    from agentos.providers.codex_cli.adapter import CodexCliAdapter

    assert AntigravityCliAdapter.capabilities.offload_tool_images is True
    assert ClaudeCliAdapter.capabilities.offload_tool_images is False
    assert CodexCliAdapter.capabilities.offload_tool_images is False


def test_public_text_keeps_the_native_read_file_name() -> None:
    assert public_text("use read_file para texto") == "use Read para texto"
    assert "nativa read_file" in public_text("sua ferramenta nativa read_file com o caminho")
    assert "native `read_file`" in public_text("call your native `read_file` tool")


# --- view_file on audio and video ---------------------------------------------------------------


class _FakeTranscriber:
    def __init__(self, text: str = "The blue train leaves at 9:15 from platform 3.") -> None:
        self.text = text
        self.calls: list[tuple[str, str, str]] = []

    def transcribe(self, path: Path, *, kind: str | None = None, question: str = "") -> str:
        self.calls.append((Path(path).name, str(kind), question))
        return self.text


class _FailingTranscriber:
    def transcribe(self, path: Path, *, kind: str | None = None, question: str = "") -> str:
        raise MediaTranscriptionError("o Gemini nao respondeu")


def _toolset_with_audio(tmp_path: Path, **kwargs: Any) -> tuple[AgentToolset, Path]:
    workspace = ConversationWorkspace(tmp_path, "c1")
    audio = workspace.root / "neutro.wav"
    audio.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    return AgentToolset(workspace, enable_terminal=False, **kwargs), audio


def test_view_file_on_audio_transcribes_for_a_model_that_does_not_hear(tmp_path: Path) -> None:
    transcriber = _FakeTranscriber()
    toolset, _ = _toolset_with_audio(tmp_path, media_transcriber_factory=lambda: transcriber)
    result = toolset.view_file("neutro.wav", "o que diz?")
    assert "The blue train leaves at 9:15 from platform 3." in result["content"]
    assert "não ouve" in result["content"]
    assert result["images"] == []
    assert transcriber.calls == [("neutro.wav", "audio", "o que diz?")]
    # read_file hands audio to the same reader instead of dumping binary as text
    assert "blue train" in toolset.read_file("neutro.wav")["content"]


def test_view_file_on_audio_points_gemini_to_its_native_read_file_with_the_absolute_path(tmp_path: Path) -> None:
    transcriber = _FakeTranscriber()
    toolset, audio = _toolset_with_audio(
        tmp_path, cli_native_media=True, native_media_hears=True, media_transcriber_factory=lambda: transcriber
    )
    content = toolset.view_file("neutro.wav")["content"]
    assert "ferramenta nativa read_file" in content
    assert audio.resolve().as_posix() in content
    assert transcriber.calls == []  # a model that hears is not transcribed for


def test_view_file_on_audio_says_clearly_when_there_is_no_transcriber_or_it_fails(tmp_path: Path) -> None:
    toolset, _ = _toolset_with_audio(tmp_path)
    content = toolset.view_file("neutro.wav")["content"]
    assert "não ouve" in content and "Gemini" in content
    failing, _ = _toolset_with_audio(tmp_path / "other", media_transcriber_factory=lambda: _FailingTranscriber())
    message = failing.view_file("neutro.wav")["content"]
    assert "o Gemini nao respondeu" in message and "Troque para um modelo Gemini" in message
    broken, _ = _toolset_with_audio(tmp_path / "third", media_transcriber_factory=lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert "Gemini" in broken.view_file("neutro.wav")["content"]


# --- subagents: the vision of its own model ----------------------------------------------------------


def test_a_gemini_subagent_under_a_text_only_main_agent_gets_images(tmp_path: Path, agents_store) -> None:  # noqa: F811
    routing = _Routing(_Scripted([_text_batch("ok")]))
    session = _session(
        tmp_path,
        agents_store,
        routing,
        [_definition("google-antigravity", ("gemini-3.8-flash-high",))],
        allowed={("gemini-3.8-flash-high", "google-antigravity")},
    )
    session.model_sees_images = False
    session._model_sees_images_for = lambda model_id, provider=None: str(model_id).startswith("gemini-")
    session._model_hears_audio_for = lambda model_id, provider=None: str(model_id).startswith("gemini-")
    captured: list[AgentToolset] = []
    original = session._toolset

    def spy(**kwargs: Any) -> AgentToolset:
        built = original(**kwargs)
        captured.append(built)
        return built

    session._toolset = spy  # type: ignore[method-assign]
    outcome = session._run_subagent("revisor", "revise")
    assert outcome.status == "succeeded", outcome.content
    assert captured and captured[-1].model_sees_images is True
    assert captured[-1].cli_native_media is True and captured[-1].native_media_hears is True


def test_a_text_only_subagent_under_a_vision_main_agent_does_not_get_images(tmp_path: Path, agents_store) -> None:  # noqa: F811
    routing = _Routing(_Scripted([_text_batch("ok")]))
    session = _session(
        tmp_path,
        agents_store,
        routing,
        [_definition("google-antigravity", ("gpt-oss-120b-medium",))],
        allowed={("gpt-oss-120b-medium", "google-antigravity")},
    )
    session.model_sees_images = True
    session._model_sees_images_for = lambda model_id, provider=None: str(model_id).startswith("gemini-")
    captured: list[AgentToolset] = []
    original = session._toolset

    def spy(**kwargs: Any) -> AgentToolset:
        built = original(**kwargs)
        captured.append(built)
        return built

    session._toolset = spy  # type: ignore[method-assign]
    session._run_subagent("revisor", "revise")
    assert captured and captured[-1].model_sees_images is False
    assert captured[-1].native_media_hears is False


def test_the_session_keeps_the_main_answer_when_it_cannot_tell_the_subagent_model() -> None:
    class _Bare:
        model_sees_images = True
        _model_sees_images_for = None

    assert TurnSession._sees_images_for(_Bare(), "x", "p") is True  # type: ignore[arg-type]

    class _Raising:
        model_sees_images = False

        @staticmethod
        def _model_sees_images_for(model_id: str, provider: str | None) -> bool:
            raise RuntimeError("catalog down")

    assert TurnSession._sees_images_for(_Raising(), "x", "p") is False  # type: ignore[arg-type]


# --- the attachment marker -----------------------------------------------------------------------------


def _record(path: str, kind: str, size: int = 2048) -> dict[str, object]:
    return {"path": path, "kind": kind, "bytes": size}


def test_marker_of_a_non_agy_provider_keeps_the_old_text_and_adds_the_transcription_hint(tmp_path: Path) -> None:
    marker = _attachment_marker([_record(".orin/uploads/a.png", "image")], provider="anthropic", model_id="claude-opus-5-5")
    assert "Use view_file(path=\"…\") para ler o conteúdo de um anexo visual, ou read_file para texto." in marker
    assert ".orin/uploads/a.png" in marker
    audio = _attachment_marker([_record(".orin/uploads/a.mp3", "audio")], provider="openai-codex", model_id="gpt-6")
    assert "(áudio, 2 KB)" in audio and "transcrição" in audio


def test_marker_gives_the_absolute_path_and_the_native_tool_on_agy_for_gemini(tmp_path: Path) -> None:
    records = [
        _record(".orin/uploads/a.png", "image"),
        _record(".orin/uploads/a.wav", "audio"),
        _record(".orin/uploads/a.mp4", "video"),
        _record(".orin/uploads/doc.pdf", "pdf"),
    ]
    marker = _attachment_marker(records, workspace_root=tmp_path, provider="google-antigravity", model_id="gemini-3.8-flash-high")
    absolute = (tmp_path / ".orin/uploads/a.png").as_posix()
    assert absolute in marker
    assert "ferramenta nativa read_file" in marker and "caminho ABSOLUTO" in marker
    assert "imagem/áudio/vídeo" in marker
    assert "PDF e documentos" in marker and "view_file" in marker


def test_marker_on_agy_for_claude_sends_images_native_and_audio_to_the_transcription(tmp_path: Path) -> None:
    records = [_record(".orin/uploads/a.png", "image"), _record(".orin/uploads/a.wav", "audio")]
    marker = _attachment_marker(records, workspace_root=tmp_path, provider="google-antigravity", model_id="claude-opus-5-5-high")
    assert "Para imagem, chame sua ferramenta nativa read_file" in marker
    assert "áudio/vídeo (o Orin devolve a transcrição" in marker
    text_only = _attachment_marker(
        [_record(".orin/uploads/a.png", "image")], workspace_root=tmp_path, provider="google-antigravity",
        model_id="gpt-oss-120b-medium",
    )
    assert "ferramenta nativa read_file" not in text_only and "view_file" in text_only

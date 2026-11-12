"""Transcribe an audio or video file for a model that cannot listen, through a short Antigravity CLI process.

Claude and GPT through agy (and the Claude CLI or Codex) take no audio. Orin starts a short `agy` process with a
Gemini Flash model on the user's subscription (no API key), whose only tool is the native `read_file` behind the
same media gate a chat turn uses, has it listen to a copy of the file, and hands the text back to the model that
asked. When that fails the caller says so and suggests a Gemini model.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from typing import Any, Callable, Mapping

from agentos.agentic.cli_media_client import CliImageGenerationClient
from agentos.agentic.media_client import MediaGenerationError
from agentos.providers.antigravity_cli.command import (
    build_isolated_antigravity_env,
    clean_antigravity_model_id,
    redact_antigravity_argv,
    resolve_antigravity_binary,
    wire_antigravity_media_reader,
)
from agentos.providers.antigravity_cli.media_gate import (
    ALLOWED_AUDIO_EXTENSIONS,
    ALLOWED_VIDEO_EXTENSIONS,
    DEFAULT_MAX_MEDIA_BYTES,
    MediaReadPolicy,
)
from agentos.providers.antigravity_cli.stream import (
    FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
    AntigravityStreamTranslator,
)
from agentos.providers.claude_cli.native_loop import find_forbidden_item_kinds
from agentos.providers.claude_cli.provider import kill_process_tree

_LOGGER = logging.getLogger("agentos.agentic.media_transcriber")

DEFAULT_TRANSCRIPTION_MODEL: str = "gemini-3.8-flash-medium"
DEFAULT_TRANSCRIPTION_TIMEOUT_SECONDS: float = 240.0
MAX_TRANSCRIPT_CHARS: int = 60_000

_AUDIO_VIDEO_EXTENSIONS = ALLOWED_AUDIO_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS
_QUESTION_MAX_CHARS = 500


class MediaTranscriptionError(RuntimeError):
    """The transcription could not be produced; the message is safe to show the model."""


def is_audio_or_video_path(path: Path | str) -> bool:
    return Path(str(path)).suffix.lower() in _AUDIO_VIDEO_EXTENSIONS


def media_kind(path: Path | str) -> str:
    """``"video"`` for a video container, ``"audio"`` otherwise (``.webm`` counts as video)."""
    suffix = Path(str(path)).suffix.lower()
    if suffix in ALLOWED_VIDEO_EXTENSIONS:
        return "video"
    return "audio"


def _one_line(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _build_prompt(media_path: Path, kind: str, question: str) -> str:
    target = media_path.as_posix()
    if kind == "video":
        task = (
            "Watch it and listen to it. First transcribe, verbatim and in the original language, every word "
            "that is spoken (write 'no speech' if there is none). Then describe in a few sentences what is "
            "shown on screen, including any visible text."
        )
    else:
        task = (
            "Listen to it and transcribe, verbatim and in the original language, every word that is spoken. "
            "If there is no speech, describe the sounds in one sentence."
        )
    focus = _one_line(question, _QUESTION_MAX_CHARS)
    extra = f" The person asked, which you may use as a hint about what matters: {focus}" if focus else ""
    return (
        f"Call your read_file tool once with the absolute path {target} . {task} "
        f"Reply with the result only, no preamble and no commentary.{extra}"
    )


class AgyMediaTranscriber(CliImageGenerationClient):
    """Runs the short `agy` process; reuses the image client's process runner (timeout, cancel, kill)."""

    _ACTIVITY_NOUN = "Transcrição de mídia"
    _ACTIVITY_VERB = "transcrever mídia"

    def __init__(
        self,
        *,
        cli_model: str | None = None,
        binary: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = DEFAULT_TRANSCRIPTION_TIMEOUT_SECONDS,
        cancel_check: Callable[[], bool] | None = None,
        popen_factory: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(
            "google-antigravity",
            cli_model=cli_model,
            binary=binary,
            env=env,
            timeout_seconds=timeout_seconds,
            cancel_check=cancel_check,
            popen_factory=popen_factory,
        )

    def _model(self) -> str:
        raw = self._cli_model or str(
            self._source_env().get("ORIN_ANTIGRAVITY_TRANSCRIBE_MODEL") or ""
        ).strip()
        if raw:
            try:
                cleaned = clean_antigravity_model_id(raw)
            except ValueError:
                cleaned = None
            if cleaned and cleaned.lower().startswith("gemini-"):
                return cleaned
        return DEFAULT_TRANSCRIPTION_MODEL

    def available(self) -> bool:
        return bool(self._binary or resolve_antigravity_binary(self._env))

    def transcribe(self, path: Path | str, *, kind: str | None = None, question: str = "") -> str:
        source = Path(str(path))
        if not source.is_file():
            raise MediaTranscriptionError("O arquivo de mídia não foi encontrado.")
        if not is_audio_or_video_path(source):
            raise MediaTranscriptionError("Tipo de arquivo sem suporte para transcrição.")
        try:
            size = source.stat().st_size
        except OSError as exc:
            raise MediaTranscriptionError("Não foi possível inspecionar o arquivo de mídia.") from exc
        if size <= 0:
            raise MediaTranscriptionError("O arquivo de mídia está vazio.")
        if size > DEFAULT_MAX_MEDIA_BYTES:
            raise MediaTranscriptionError("O arquivo de mídia excede o limite de 200 MB para transcrição.")
        binary = self._binary or resolve_antigravity_binary(self._env)
        if not binary:
            raise MediaTranscriptionError("O Google Antigravity CLI (agy) não está instalado.")
        resolved_kind = kind if kind in {"audio", "video"} else media_kind(source)
        model = self._model()

        workdir = Path(tempfile.mkdtemp(prefix="orin-agy-asr-"))
        try:
            media_dir = workdir / "media"
            media_dir.mkdir()
            target = media_dir / f"input{source.suffix.lower()}"
            shutil.copy2(source, target)
            try:
                wiring = wire_antigravity_media_reader(
                    workdir,
                    media_dir=media_dir,
                    model=model,
                    instructions=(
                        "You are a transcription engine. Your only tool is read_file; use it on the file you "
                        "are given and answer with the transcription, nothing else."
                    ),
                )
            except ValueError as exc:
                raise MediaTranscriptionError(f"Falha ao preparar a transcrição: {exc}") from exc
            return self._run(
                binary=str(binary),
                workdir=workdir,
                wiring_argv=wiring.argv,
                media_dir=media_dir,
                media_file=target.resolve(),
                model=model,
                kind=resolved_kind,
                question=question,
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _run(
        self,
        *,
        binary: str,
        workdir: Path,
        wiring_argv: tuple[str, ...],
        media_dir: Path,
        media_file: Path,
        model: str,
        kind: str,
        question: str,
    ) -> str:
        isolated_env = build_isolated_antigravity_env(self._env)
        if os.name == "nt":
            for sys_key in ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT"):
                if sys_key not in isolated_env and sys_key in os.environ:
                    isolated_env[sys_key] = os.environ[sys_key]
        cmd = [
            binary,
            "--output-format",
            "stream-json",
            "--disable-slash-commands",
            "--model",
            model,
            *wiring_argv,
            "-p",
            _build_prompt(media_file, kind, question),
        ]
        policy = MediaReadPolicy(
            roots=(str(media_dir.resolve()),),
            unrestricted=False,
            extensions=_AUDIO_VIDEO_EXTENSIONS,
        )
        translator = AntigravityStreamTranslator(model=model, media_policy=policy)
        breach: list[str] = []

        def _handle_line(raw_line: str, proc: Any) -> None:
            kinds = find_forbidden_item_kinds(
                FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS, raw_line=raw_line, media_policy=policy
            )
            events = translator.feed_line(raw_line)
            for event in events:
                kinds = kinds or find_forbidden_item_kinds(
                    FORBIDDEN_ANTIGRAVITY_BUILTIN_TOOLS,
                    raw=event.raw if isinstance(event.raw, Mapping) else None,
                    tool_name=event.tool_name,
                    media_policy=policy,
                )
            if kinds:
                breach.append(str(kinds[0]))
                kill_process_tree(proc)

        t0 = time.monotonic()
        _LOGGER.info(
            "Starting Antigravity CLI media transcription model=%s kind=%s argv=%s",
            model,
            kind,
            redact_antigravity_argv(cmd),
        )
        try:
            exit_code, stderr_text = self._run_streaming_process(
                cmd=cmd,
                cwd=workdir,
                env=isolated_env,
                stdin_bytes=None,
                on_stdout_line=_handle_line,
                provider_label="Antigravity CLI",
            )
        except MediaGenerationError as exc:
            raise MediaTranscriptionError(str(exc)) from exc
        if breach:
            raise MediaTranscriptionError(
                f"O Antigravity CLI tentou usar uma ferramenta não permitida ({breach[0]}); a transcrição foi interrompida."
            )
        result = translator.result
        if result is None:
            detail = f" ({stderr_text[:200]})" if stderr_text else ""
            raise MediaTranscriptionError(
                f"O Antigravity CLI terminou sem resultado (código {exit_code}){detail}."
            )
        if result.is_error:
            raise MediaTranscriptionError(
                f"O Antigravity CLI não conseguiu transcrever: {_one_line(result.result_text, 300)}"
            )
        text = str(result.result_text or "").strip()
        if not text:
            raise MediaTranscriptionError("O Antigravity CLI não devolveu texto para a mídia.")
        _LOGGER.info(
            "Antigravity CLI media transcription finished model=%s chars=%d duration=%.2fs",
            model,
            len(text),
            time.monotonic() - t0,
        )
        return text[:MAX_TRANSCRIPT_CHARS]


__all__ = [
    "AgyMediaTranscriber",
    "DEFAULT_TRANSCRIPTION_MODEL",
    "MediaTranscriptionError",
    "is_audio_or_video_path",
    "media_kind",
]

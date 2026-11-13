"""Subscription CLI image generation client (OpenAI Codex CLI & Google Antigravity CLI)."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Mapping

from agentos.agentic.media_client import (
    MAX_MEDIA_DOWNLOAD_BYTES,
    MediaGenerationError,
    normalize_reference_image,
)
from agentos.providers.antigravity_cli.command import (
    _is_symlink_or_junction,
    _write_owner_only_file,
    build_isolated_antigravity_env,
    clean_antigravity_model_id,
    redact_antigravity_argv,
    resolve_antigravity_binary,
    resolve_antigravity_home,
)
from agentos.providers.claude_cli.provider import kill_process_tree
from agentos.providers.codex_cli.catalog import (
    discover_codex_models,
    resolve_codex_home,
)
from agentos.providers.codex_cli.command import (
    build_isolated_codex_env,
    clean_codex_model_id,
    encode_codex_user_turn,
    redact_codex_argv,
    resolve_codex_binary,
)

_LOGGER = logging.getLogger("agentos.agentic.cli_media_client")

CODEX_IMAGE_MODEL_ID: str = "codex-image"
ANTIGRAVITY_IMAGE_MODEL_ID: str = "antigravity-image"
VIRTUAL_CLI_IMAGE_MODEL_IDS: frozenset[str] = frozenset(
    {CODEX_IMAGE_MODEL_ID, ANTIGRAVITY_IMAGE_MODEL_ID}
)

DEFAULT_CODEX_IMAGE_CLI_MODEL: str = "gpt-6-luna"
DEFAULT_ANTIGRAVITY_IMAGE_CLI_MODEL: str = "gemini-3.8-flash-medium"
DEFAULT_CLI_IMAGE_TIMEOUT_SECONDS: float = 240.0

_SAFE_SESSION_DIR_RE = re.compile(r"\A[A-Za-z0-9._\-]{1,128}\Z")
_SIZE_DIM_RE = re.compile(r"\A(\d{2,5})x(\d{2,5})\Z", re.IGNORECASE)
_IMAGE_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp"})

_AGY_IMAGE_AGENT_MD: str = (
    "---\n"
    "name: orin\n"
    "description: Orin image generator\n"
    "mainAgent: true\n"
    "excludeDefaultComponents: true\n"
    "inheritMcp: false\n"
    "tools: [generate_image]\n"
    "---\n"
    "# Instructions\n"
    "Call `generate_image` once to generate the requested image, then reply briefly. "
    "Do not call any other tool.\n"
)

_AGY_IMAGE_WITH_REF_AGENT_MD: str = (
    "---\n"
    "name: orin\n"
    "description: Orin image generator with visual reference\n"
    "mainAgent: true\n"
    "excludeDefaultComponents: true\n"
    "inheritMcp: false\n"
    "tools: [read_file, generate_image]\n"
    "---\n"
    "# Instructions\n"
    "First call `read_file` on the reference image path provided in the prompt to inspect the visual reference. "
    "Then call `generate_image` once to generate the requested image following the visual reference closely "
    "(preserving character identity, style, colors, and composition as requested), and reply briefly. "
    "Do not call any other tool.\n"
)


def detect_image_extension(data: bytes, *, fallback_suffix: str = "") -> str:
    """Detect real image extension (png, jpg, webp, gif) from magic bytes."""
    if len(data) >= 8 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 3 and data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if len(data) >= 6 and data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"

    norm_suffix = fallback_suffix.strip().lower().lstrip(".")
    if norm_suffix == "jpeg":
        norm_suffix = "jpg"
    if norm_suffix in {"png", "jpg", "webp", "gif"} and data:
        return norm_suffix
    raise MediaGenerationError(
        "Formato de imagem gerado inválido ou não reconhecido (esperado PNG, JPG ou WEBP)."
    )


def _has_image_magic(data: bytes) -> bool:
    if len(data) >= 8 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if len(data) >= 3 and data.startswith(b"\xff\xd8\xff"):
        return True
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    if len(data) >= 6 and data.startswith((b"GIF87a", b"GIF89a")):
        return True
    return False


def _write_owner_only_bytes(path: Path, data: bytes) -> Path:
    if _is_symlink_or_junction(path):
        raise MediaGenerationError(f"Recusando escrita em symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_TRUNC
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
    )
    fd = os.open(str(path), flags, 0o600)
    try:
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return path


def _stage_reference_image(workdir: Path, reference_image: Any | None) -> Path | None:
    ref = normalize_reference_image(reference_image)
    if ref is None:
        return None
    ext = detect_image_extension(ref.data)
    staged_path = workdir / f"reference.{ext}"
    return _write_owner_only_bytes(staged_path, ref.data)


def _format_size_aspect_hint(size: str) -> str:
    cleaned = str(size or "1024x1024").strip() or "1024x1024"
    match = _SIZE_DIM_RE.match(cleaned)
    if not match:
        return cleaned
    width = int(match.group(1))
    height = int(match.group(2))
    if width <= 0 or height <= 0:
        return cleaned
    if width == height:
        return f"{width}x{height} (proporção quadrada 1:1 / square aspect ratio)"
    if width > height:
        return f"{width}x{height} (proporção paisagem / landscape aspect ratio)"
    return f"{width}x{height} (proporção retrato / portrait aspect ratio)"


def _build_cli_image_prompt(
    prompt: str,
    size: str,
    *,
    has_reference_image: bool = False,
    reference_image_path: str | None = None,
) -> str:
    size_hint = _format_size_aspect_hint(size)
    sanitized_prompt = (
        str(prompt or "")
        .replace("\x00", "")
        .replace("\r\n", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )
    if reference_image_path:
        return (
            f"First call `read_file` on the reference image at `{reference_image_path}` to inspect it. "
            f"Use that reference image to preserve visual style, character identity, colors, and composition as requested, "
            f"and generate one image with your image generation tool "
            f"(size / aspect ratio: {size_hint}): {sanitized_prompt}"
        )
    if has_reference_image:
        return (
            f"Use the attached reference image to preserve visual style, character identity, colors, and composition "
            f"as requested, and generate one image with your image generation tool "
            f"(size / aspect ratio: {size_hint}): {sanitized_prompt}"
        )
    return (
        f"Generate one image with your image generation tool "
        f"(size / aspect ratio: {size_hint}): {sanitized_prompt}"
    )


def _validate_safe_session_id(raw_id: object, label: str) -> str:
    if not isinstance(raw_id, str):
        raise MediaGenerationError(f"Identificador de sessão inválido ({label}).")
    cleaned = raw_id.strip()
    if (
        not cleaned
        or cleaned in {".", ".."}
        or not _SAFE_SESSION_DIR_RE.fullmatch(cleaned)
    ):
        raise MediaGenerationError(f"Identificador de sessão inseguro ({label}).")
    return cleaned


def _collect_newest_image(directory: Path, *, provider_label: str) -> tuple[bytes, str]:
    if _is_symlink_or_junction(directory) or not directory.is_dir():
        raise MediaGenerationError(
            f"Nenhuma imagem foi gerada pelo {provider_label} (diretório de saída ausente)."
        )

    candidates: list[tuple[int, int, str, Path]] = []
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise MediaGenerationError(
            f"Falha ao ler o diretório de imagens geradas pelo {provider_label}."
        ) from exc

    for entry in entries:
        try:
            if _is_symlink_or_junction(entry) or not entry.is_file():
                continue
            stat = entry.stat()
        except OSError:
            continue
        if stat.st_size <= 0:
            continue

        suffix = entry.suffix.lower()
        is_known_suffix = suffix in _IMAGE_SUFFIXES
        has_magic = False
        try:
            with entry.open("rb") as fh:
                header = fh.read(16)
            has_magic = _has_image_magic(header)
        except OSError:
            continue

        if not has_known_image_type(has_magic=has_magic, is_known_suffix=is_known_suffix):
            continue

        priority = 1 if has_magic else 0
        candidates.append((priority, stat.st_mtime_ns, entry.name, entry))

    if not candidates:
        raise MediaGenerationError(
            f"Nenhuma imagem foi gerada pelo {provider_label}."
        )

    candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    chosen = candidates[0][3]

    try:
        size_bytes = chosen.stat().st_size
    except OSError as exc:
        raise MediaGenerationError(
            f"Não foi possível inspecionar a imagem gerada pelo {provider_label}."
        ) from exc

    if size_bytes > MAX_MEDIA_DOWNLOAD_BYTES:
        raise MediaGenerationError(
            f"A imagem gerada pelo {provider_label} excede o limite máximo de 100 MB."
        )

    try:
        data = chosen.read_bytes()
    except OSError as exc:
        raise MediaGenerationError(
            f"Não foi possível ler os bytes da imagem gerada pelo {provider_label}."
        ) from exc

    if not data:
        raise MediaGenerationError(
            f"O arquivo de imagem gerado pelo {provider_label} está vazio."
        )
    if len(data) > MAX_MEDIA_DOWNLOAD_BYTES:
        raise MediaGenerationError(
            f"A imagem gerada pelo {provider_label} excede o limite máximo de 100 MB."
        )

    ext = detect_image_extension(data, fallback_suffix=chosen.suffix)
    return data, ext


def has_known_image_type(*, has_magic: bool, is_known_suffix: bool) -> bool:
    return has_magic or is_known_suffix


class CliImageGenerationClient:
    """Executes headless image generation through subscription CLIs (Codex and Antigravity)."""

    # What the process is doing, for the messages of `_run_streaming_process` (a subclass changes them).
    _ACTIVITY_NOUN = "Geração de imagem"
    _ACTIVITY_VERB = "gerar imagem"

    def __init__(
        self,
        provider: str,
        *,
        cli_model: str | None = None,
        binary: str | None = None,
        env: Mapping[str, str] | None = None,
        codex_home: str | Path | None = None,
        antigravity_brain_dir: str | Path | None = None,
        timeout_seconds: float = DEFAULT_CLI_IMAGE_TIMEOUT_SECONDS,
        cancel_check: Callable[[], bool] | None = None,
        popen_factory: Callable[..., Any] | None = None,
    ) -> None:
        norm_provider = str(provider or "").strip().lower()
        if norm_provider not in {"openai-codex", "google-antigravity"}:
            raise MediaGenerationError(
                f"Provedor CLI de geração de imagem não suportado: {provider!r}"
            )
        self.provider = norm_provider
        self._cli_model = str(cli_model).strip() if cli_model and str(cli_model).strip() else None
        self._binary = str(binary).strip() if binary and str(binary).strip() else None
        self._env = dict(env) if env is not None else None
        self._codex_home = Path(codex_home) if codex_home is not None else None
        self._antigravity_brain_dir = (
            Path(antigravity_brain_dir) if antigravity_brain_dir is not None else None
        )
        self._timeout_seconds = max(1.0, float(timeout_seconds or DEFAULT_CLI_IMAGE_TIMEOUT_SECONDS))
        self._cancel_check = cancel_check
        self._popen_factory = popen_factory

    def _source_env(self) -> Mapping[str, str]:
        return os.environ if self._env is None else self._env

    def _resolve_codex_cli_model(self, codex_home: Path) -> str:
        raw_override = self._cli_model or str(
            self._source_env().get("ORIN_CODEX_IMAGE_MODEL") or ""
        ).strip()
        if raw_override:
            try:
                cleaned = clean_codex_model_id(raw_override)
            except ValueError as exc:
                raise MediaGenerationError(
                    "Modelo configurado para geração de imagem no Codex CLI é inválido."
                ) from exc
            if cleaned and cleaned not in VIRTUAL_CLI_IMAGE_MODEL_IDS:
                return cleaned

        catalog = discover_codex_models(codex_home)
        chat_ids = [
            m.id
            for m in catalog
            if m.id not in VIRTUAL_CLI_IMAGE_MODEL_IDS
            and "image" not in (getattr(m, "output_modalities", None) or ())
        ]
        if DEFAULT_CODEX_IMAGE_CLI_MODEL in chat_ids:
            return DEFAULT_CODEX_IMAGE_CLI_MODEL
        if chat_ids:
            return chat_ids[0]
        return DEFAULT_CODEX_IMAGE_CLI_MODEL

    def _resolve_antigravity_cli_model(self) -> str:
        raw_override = self._cli_model or str(
            self._source_env().get("ORIN_ANTIGRAVITY_IMAGE_MODEL") or ""
        ).strip()
        if raw_override:
            try:
                cleaned = clean_antigravity_model_id(raw_override)
            except ValueError as exc:
                raise MediaGenerationError(
                    "Modelo configurado para geração de imagem no Antigravity CLI é inválido."
                ) from exc
            if cleaned and cleaned not in VIRTUAL_CLI_IMAGE_MODEL_IDS:
                return cleaned
        return DEFAULT_ANTIGRAVITY_IMAGE_CLI_MODEL

    def _resolve_antigravity_brain_root(self) -> Path:
        if self._antigravity_brain_dir is not None:
            return self._antigravity_brain_dir.expanduser()
        env_override = str(
            self._source_env().get("ORIN_ANTIGRAVITY_BRAIN_DIR") or ""
        ).strip()
        if env_override:
            return Path(env_override).expanduser()
        return resolve_antigravity_home(self._env) / "brain"

    def generate_image(
        self,
        *,
        model: str = "",
        prompt: str,
        size: str = "1024x1024",
        reference_image: Any | None = None,
    ) -> tuple[bytes, str]:
        _ = model  # Real CLI model comes from configuration, not the virtual catalog id.
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise MediaGenerationError("O prompt é obrigatório para geração de imagem.")
        clean_size = str(size or "1024x1024").strip() or "1024x1024"

        prefix = "orin-codex-img-" if self.provider == "openai-codex" else "orin-agy-img-"
        temp_dir = Path(tempfile.mkdtemp(prefix=prefix))
        try:
            staged_ref_path = _stage_reference_image(temp_dir, reference_image)
            if self.provider == "openai-codex":
                return self._generate_with_codex(
                    workdir=temp_dir,
                    prompt=clean_prompt,
                    size=clean_size,
                    staged_ref_path=staged_ref_path,
                )
            return self._generate_with_antigravity(
                workdir=temp_dir,
                prompt=clean_prompt,
                size=clean_size,
                staged_ref_path=staged_ref_path,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def generate_audio(
        self,
        *,
        model: str = "",
        text: str = "",
        voice: str = "alloy",
        response_format: str = "mp3",
    ) -> tuple[bytes, str]:
        _ = (model, text, voice, response_format)
        raise MediaGenerationError(
            f"Geração de áudio não suportado pelo provedor CLI '{self.provider}'."
        )

    def generate_video(
        self,
        *,
        model: str = "",
        prompt: str = "",
        duration_seconds: int = 5,
    ) -> tuple[bytes, str]:
        _ = (model, prompt, duration_seconds)
        raise MediaGenerationError(
            f"Geração de vídeo não suportado pelo provedor CLI '{self.provider}'."
        )

    def _prepare_popen_cmd(self, cmd: list[str]) -> list[str]:
        if str(cmd[0]).lower().endswith(".py"):
            return [sys.executable, *cmd]
        return cmd

    def _run_streaming_process(
        self,
        *,
        cmd: list[str],
        cwd: Path,
        env: dict[str, str],
        stdin_bytes: bytes | None,
        on_stdout_line: Callable[[str, Any], None],
        provider_label: str,
    ) -> tuple[int | None, str]:
        popen_fn = self._popen_factory or subprocess.Popen
        popen_cmd = self._prepare_popen_cmd(cmd)
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        )

        try:
            proc = popen_fn(
                popen_cmd,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
                creationflags=creationflags,
            )
        except Exception as exc:
            raise MediaGenerationError(
                f"Falha ao iniciar o processo do {provider_label}."
            ) from exc

        stdout_queue: queue.Queue[str | None] = queue.Queue()
        stderr_chunks: list[str] = []

        def _read_stdout() -> None:
            try:
                if proc.stdout is not None:
                    for raw_line in iter(proc.stdout.readline, ""):
                        stdout_queue.put(raw_line)
            except Exception:
                pass
            finally:
                stdout_queue.put(None)

        def _read_stderr() -> None:
            try:
                if proc.stderr is not None:
                    data = proc.stderr.read()
                    if data:
                        stderr_chunks.append(data)
            except Exception:
                pass

        stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        if stdin_bytes is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_bytes.decode("utf-8"))
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass

        deadline = time.monotonic() + self._timeout_seconds
        eof = False
        try:
            while not eof:
                if callable(self._cancel_check) and self._cancel_check():
                    kill_process_tree(proc)
                    raise MediaGenerationError(
                        f"{self._ACTIVITY_NOUN} pelo {provider_label} cancelada."
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    kill_process_tree(proc)
                    raise MediaGenerationError(
                        f"Tempo limite excedido ({int(self._timeout_seconds)}s) ao {self._ACTIVITY_VERB} pelo {provider_label}."
                    )
                try:
                    line = stdout_queue.get(timeout=min(0.05, remaining))
                except queue.Empty:
                    continue
                if line is None:
                    eof = True
                    break
                on_stdout_line(line, proc)

            remaining = max(0.1, deadline - time.monotonic())
            try:
                proc.wait(timeout=min(5.0, remaining))
            except subprocess.TimeoutExpired:
                kill_process_tree(proc)
                raise MediaGenerationError(
                    f"Tempo limite excedido ({int(self._timeout_seconds)}s) ao finalizar o {provider_label}."
                )
        except Exception:
            kill_process_tree(proc)
            raise
        finally:
            stdout_thread.join(timeout=1.0)
            stderr_thread.join(timeout=1.0)

        stderr_text = "".join(stderr_chunks).strip()
        return getattr(proc, "returncode", None), stderr_text

    def _generate_with_codex(
        self,
        *,
        workdir: Path,
        prompt: str,
        size: str,
        staged_ref_path: Path | None = None,
    ) -> tuple[bytes, str]:
        binary = self._binary or resolve_codex_binary(self._env)
        if not binary:
            raise MediaGenerationError(
                "Executável do OpenAI Codex CLI não encontrado."
            )

        codex_home = (
            self._codex_home
            if self._codex_home is not None
            else resolve_codex_home(self._env)
        )
        cli_model = self._resolve_codex_cli_model(codex_home)
        isolated_env = build_isolated_codex_env(self._env)
        if self._codex_home is not None:
            isolated_env["CODEX_HOME"] = str(self._codex_home)
        if os.name == "nt":
            for sys_key in ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT"):
                if sys_key not in isolated_env and sys_key in os.environ:
                    isolated_env[sys_key] = os.environ[sys_key]

        cmd: list[str] = [
            str(binary),
            "exec",
            "--json",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--ephemeral",
            "-m",
            cli_model,
            "-c",
            "features.shell_tool=false",
            "-c",
            'web_search="disabled"',
            "-c",
            "features.view_image=false",
            "-c",
            "features.multi_agent=false",
            "-c",
            'sandbox_mode="read-only"',
            "-c",
            'approval_policy="never"',
        ]
        if staged_ref_path is not None:
            cmd.extend(["-i", str(staged_ref_path)])
        cmd.append("-")

        full_prompt = _build_cli_image_prompt(
            prompt,
            size,
            has_reference_image=staged_ref_path is not None,
        )
        stdin_bytes = encode_codex_user_turn(full_prompt)

        thread_id: str | None = None
        stream_error: str | None = None

        def _handle_line(raw_line: str, _proc: Any) -> None:
            nonlocal thread_id, stream_error
            stripped = raw_line.strip()
            if not stripped:
                return
            try:
                packet = json.loads(stripped)
            except json.JSONDecodeError:
                return
            if not isinstance(packet, Mapping):
                return
            event_type = str(packet.get("type") or "").strip()
            if event_type == "thread.started" and thread_id is None:
                raw_tid = packet.get("thread_id")
                if raw_tid is not None:
                    thread_id = _validate_safe_session_id(raw_tid, "thread_id")
            elif event_type in {"error", "turn.failed"}:
                err_obj = packet.get("error")
                if isinstance(err_obj, Mapping):
                    msg = str(err_obj.get("message") or "").strip()
                    if msg:
                        stream_error = msg
                elif isinstance(err_obj, str) and err_obj.strip():
                    stream_error = err_obj.strip()
                elif isinstance(packet.get("message"), str) and str(packet["message"]).strip():
                    stream_error = str(packet["message"]).strip()

        t0 = time.monotonic()
        _LOGGER.info(
            "Starting Codex CLI image generation model=%s argv=%s",
            cli_model,
            redact_codex_argv(cmd),
        )
        exit_code, _stderr = self._run_streaming_process(
            cmd=cmd,
            cwd=workdir,
            env=isolated_env,
            stdin_bytes=stdin_bytes,
            on_stdout_line=_handle_line,
            provider_label="Codex CLI",
        )
        elapsed = time.monotonic() - t0

        if not thread_id:
            detail = f" ({stream_error})" if stream_error else ""
            raise MediaGenerationError(
                f"O Codex CLI não iniciou uma sessão válida para geração de imagem{detail}."
            )

        images_dir = Path(codex_home) / "generated_images" / thread_id
        try:
            data, ext = _collect_newest_image(images_dir, provider_label="Codex CLI")
        except MediaGenerationError as exc:
            if stream_error:
                raise MediaGenerationError(
                    f"{exc} Detalhe: {stream_error}"
                ) from exc
            if exit_code not in (0, None):
                raise MediaGenerationError(
                    f"{exc} (código de saída {exit_code})."
                ) from exc
            raise

        _LOGGER.info(
            "Codex CLI image generation finished model=%s ext=%s bytes=%d duration=%.2fs",
            cli_model,
            ext,
            len(data),
            elapsed,
        )
        return data, ext

    def _generate_with_antigravity(
        self,
        *,
        workdir: Path,
        prompt: str,
        size: str,
        staged_ref_path: Path | None = None,
    ) -> tuple[bytes, str]:
        binary = self._binary or resolve_antigravity_binary(self._env)
        if not binary:
            raise MediaGenerationError(
                "Executável do Google Antigravity CLI (agy) não encontrado."
            )

        if _is_symlink_or_junction(workdir):
            raise MediaGenerationError("Diretório temporário inválido (symlink).")

        git_dir = workdir / ".git"
        if _is_symlink_or_junction(git_dir):
            raise MediaGenerationError("Diretório .git inválido (symlink).")
        git_dir.mkdir(parents=True, exist_ok=True)

        agents_dir = workdir / ".agents"
        agents_subdir = agents_dir / "agents"
        agent_md_path = agents_subdir / "orin.md"
        settings_file = agents_dir / "orin_settings.json"

        _write_owner_only_file(
            agent_md_path,
            _AGY_IMAGE_WITH_REF_AGENT_MD if staged_ref_path is not None else _AGY_IMAGE_AGENT_MD,
        )

        try:
            resolved_workdir = str(workdir.resolve())
        except OSError:
            resolved_workdir = str(workdir.absolute())

        if staged_ref_path is not None:
            allow_rules = ["read_file(*)"]
            deny_rules = [
                "command(*)",
                "write_file(*)",
                "read_url(*)",
                "execute_url(*)",
                "unsandboxed(*)",
            ]
        else:
            allow_rules = []
            deny_rules = [
                "command(*)",
                "read_file(*)",
                "write_file(*)",
                "read_url(*)",
                "execute_url(*)",
                "unsandboxed(*)",
            ]

        settings_payload = {
            "trustedWorkspaces": [resolved_workdir],
            "allowNonWorkspaceAccess": False,
            "enableTerminalSandbox": True,
            "disableSlashCommands": True,
            "permissions": {
                "allow": allow_rules,
                "deny": deny_rules,
            },
        }
        _write_owner_only_file(
            settings_file,
            json.dumps(settings_payload, ensure_ascii=True, indent=2) + "\n",
        )

        cli_model = self._resolve_antigravity_cli_model()
        isolated_env = build_isolated_antigravity_env(self._env)
        if os.name == "nt":
            for sys_key in ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT"):
                if sys_key not in isolated_env and sys_key in os.environ:
                    isolated_env[sys_key] = os.environ[sys_key]

        ref_posix: str | None = None
        if staged_ref_path is not None:
            try:
                ref_posix = staged_ref_path.resolve().as_posix()
            except OSError:
                ref_posix = staged_ref_path.absolute().as_posix()

        full_prompt = _build_cli_image_prompt(
            prompt,
            size,
            has_reference_image=staged_ref_path is not None,
            reference_image_path=ref_posix,
        )
        cmd: list[str] = [
            str(binary),
            "--agent",
            "orin",
            "--settings",
            str(settings_file),
            "--model",
            cli_model,
            "--output-format",
            "stream-json",
            "-p",
            full_prompt,
        ]

        conversation_id: str | None = None
        stream_error: str | None = None
        allowed_tools = (
            {"generate_image", "read_file"}
            if staged_ref_path is not None
            else {"generate_image"}
        )

        def _handle_line(raw_line: str, proc: Any) -> None:
            nonlocal conversation_id, stream_error
            stripped = raw_line.strip()
            if not stripped:
                return
            try:
                packet = json.loads(stripped)
            except json.JSONDecodeError:
                return
            if not isinstance(packet, Mapping):
                return

            raw_cid = packet.get("conversation_id")
            if raw_cid is not None and conversation_id is None:
                conversation_id = _validate_safe_session_id(raw_cid, "conversation_id")

            event_name = str(packet.get("event") or "").strip()
            if event_name == "init" and conversation_id is None:
                init_obj = packet.get("init")
                if isinstance(init_obj, Mapping) and init_obj.get("conversation_id") is not None:
                    conversation_id = _validate_safe_session_id(
                        init_obj.get("conversation_id"), "conversation_id"
                    )
            elif event_name == "step_update":
                step_obj = packet.get("step_update")
                if isinstance(step_obj, Mapping):
                    step_type = str(step_obj.get("step_type") or "").strip().lower()
                    has_tool_key = "tool_name" in step_obj and step_obj.get("tool_name") is not None
                    raw_tool = str(step_obj.get("tool_name") or "").strip()
                    if has_tool_key or step_type == "tool":
                        if raw_tool not in allowed_tools:
                            kill_process_tree(proc)
                            raise MediaGenerationError(
                                f"Execução abortada no Antigravity CLI: ferramenta não permitida ({raw_tool or 'desconhecida'})."
                            )
                    state = str(step_obj.get("state") or "").strip().upper()
                    if state == "ERROR":
                        err_msg = str(step_obj.get("error") or step_obj.get("message") or "").strip()
                        if err_msg:
                            stream_error = err_msg
            elif event_name == "result":
                res_obj = packet.get("result")
                if isinstance(res_obj, Mapping):
                    status = str(res_obj.get("status") or "").strip().upper()
                    if status and status != "SUCCESS":
                        err_msg = str(res_obj.get("error") or "").strip()
                        stream_error = err_msg or f"status={status}"

        t0 = time.monotonic()
        _LOGGER.info(
            "Starting Antigravity CLI image generation model=%s argv=%s",
            cli_model,
            redact_antigravity_argv(cmd),
        )
        exit_code, _stderr = self._run_streaming_process(
            cmd=cmd,
            cwd=workdir,
            env=isolated_env,
            stdin_bytes=None,
            on_stdout_line=_handle_line,
            provider_label="Antigravity CLI",
        )
        elapsed = time.monotonic() - t0

        if not conversation_id:
            detail = f" ({stream_error})" if stream_error else ""
            raise MediaGenerationError(
                f"O Antigravity CLI não informou o conversation_id da sessão{detail}."
            )

        brain_root = self._resolve_antigravity_brain_root()
        conv_dir = brain_root / conversation_id
        try:
            data, ext = _collect_newest_image(conv_dir, provider_label="Antigravity CLI")
        except MediaGenerationError as exc:
            if stream_error:
                raise MediaGenerationError(
                    f"{exc} Detalhe: {stream_error}"
                ) from exc
            if exit_code not in (0, None):
                raise MediaGenerationError(
                    f"{exc} (código de saída {exit_code})."
                ) from exc
            raise

        _LOGGER.info(
            "Antigravity CLI image generation finished model=%s ext=%s bytes=%d duration=%.2fs",
            cli_model,
            ext,
            len(data),
            elapsed,
        )
        return data, ext


__all__ = [
    "ANTIGRAVITY_IMAGE_MODEL_ID",
    "CODEX_IMAGE_MODEL_ID",
    "CliImageGenerationClient",
    "DEFAULT_ANTIGRAVITY_IMAGE_CLI_MODEL",
    "DEFAULT_CLI_IMAGE_TIMEOUT_SECONDS",
    "DEFAULT_CODEX_IMAGE_CLI_MODEL",
    "VIRTUAL_CLI_IMAGE_MODEL_IDS",
    "detect_image_extension",
]

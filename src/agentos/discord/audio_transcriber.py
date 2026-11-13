"""Audio and Discord voice-message transcription pipeline for Orin Friend.

Transcribes incoming Discord voice messages (`IS_VOICE_MESSAGE` / `.ogg` Opus) and
audio attachments (`.mp3`, `.m4a`, `.wav`, `.flac`, `.aac`, `.opus`, `.webm`) using
Orin's configured speech-to-text stack in priority order:
1. Local `faster-whisper` (`LocalWhisperEngine.transcribe_file`) when installed.
2. Configured cloud STT providers (`OpenAI`, `Groq`, `OpenRouter`).
3. Subscription `Antigravity CLI` (`AgyMediaTranscriber`) when enabled/available.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
import os
from pathlib import Path
import tempfile
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.engine import Engine

from agentos.agentic.media_transcriber import AgyMediaTranscriber, MediaTranscriptionError
from agentos.live_voice.credentials import (
    GROQ_BASE_URL,
    OPENAI_DEFAULT_BASE_URL,
    OPENROUTER_DEFAULT_BASE_URL,
)
from agentos.live_voice.engines import (
    CloudCredential,
    LocalWhisperEngine,
    VoiceEngineError,
    transcribe_audio_file_openai_compatible,
    transcribe_audio_file_openrouter,
)
from agentos.persistence.postgres.provider_api_keys import PostgresProviderApiKeyAdapter
from agentos.persistence.postgres.schema import provider_configurations
from agentos.persistence.provider_secrets import ProviderSecretCipher

_LOGGER = logging.getLogger("agentos.discord.audio_transcriber")

DISCORD_VOICE_MESSAGE_FLAG = 1 << 13  # 8192 (IS_VOICE_MESSAGE)

AUDIO_EXTENSIONS = frozenset({
    ".ogg",
    ".oga",
    ".opus",
    ".mp3",
    ".mpga",
    ".mpeg",
    ".m4a",
    ".wav",
    ".flac",
    ".aac",
})

_CONTENT_TYPE_TO_EXT: dict[str, str] = {
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mpga": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/aac": ".aac",
    "audio/webm": ".webm",
}

_NORMALIZED_EXT_REMAP: dict[str, str] = {
    ".oga": ".ogg",
    ".mpga": ".mp3",
    ".mpeg": ".mp3",
}


class DiscordAudioTranscriptionError(RuntimeError):
    """Raised when an incoming Discord audio attachment could not be transcribed."""


def clean_mime_type(content_type: str | None) -> str:
    raw = str(content_type or "").strip().lower()
    if ";" in raw:
        raw = raw.split(";", 1)[0].strip()
    return raw


def is_discord_voice_message(
    attachment: Mapping[str, Any],
    *,
    message_flags: int = 0,
) -> bool:
    if (int(message_flags or 0) & DISCORD_VOICE_MESSAGE_FLAG) != 0:
        return True
    if attachment.get("waveform") is not None or attachment.get("duration_secs") is not None:
        mime = clean_mime_type(str(attachment.get("content_type") or ""))
        ext = Path(str(attachment.get("filename") or "")).suffix.lower()
        if mime.startswith("audio/") or ext in AUDIO_EXTENSIONS or ext == ".webm" or not ext:
            return True
    return False


def is_discord_audio_attachment(
    attachment: Mapping[str, Any],
    *,
    message_flags: int = 0,
    classified_kind: str | None = None,
) -> bool:
    """Return True when a Discord attachment represents a voice message or audio file."""
    if classified_kind == "audio":
        return True
    mime = clean_mime_type(str(attachment.get("content_type") or ""))
    if mime.startswith("audio/") or mime == "application/ogg":
        return True
    ext = Path(str(attachment.get("filename") or "")).suffix.lower()
    if ext in AUDIO_EXTENSIONS:
        return True
    if is_discord_voice_message(attachment, message_flags=message_flags):
        return ext in AUDIO_EXTENSIONS or ext in {"", ".webm"}
    return False


def is_discord_media_attachment_hint(
    attachment: Mapping[str, Any],
    *,
    message_flags: int = 0,
) -> bool:
    """True when metadata indicates an audio or video container eligible for `MAX_MEDIA_UPLOAD_BYTES`."""
    if is_discord_audio_attachment(attachment, message_flags=message_flags):
        return True
    mime = clean_mime_type(str(attachment.get("content_type") or ""))
    if mime.startswith("video/"):
        return True
    ext = Path(str(attachment.get("filename") or "")).suffix.lower()
    return ext in {".mp4", ".mov", ".webm", ".mkv"}


def normalize_discord_attachment_filename(
    filename: str,
    content_type: str | None = None,
    *,
    is_voice_message: bool = False,
) -> str:
    """Ensure Discord audio/voice attachments carry a canonical file extension."""
    clean_name = str(filename or "").strip() or ("voice-message.ogg" if is_voice_message else "anexo")
    suffix = Path(clean_name).suffix.lower()
    if suffix in _NORMALIZED_EXT_REMAP:
        stem = clean_name[: -len(suffix)] or "audio"
        return f"{stem}{_NORMALIZED_EXT_REMAP[suffix]}"
    if suffix:
        return clean_name

    mime = clean_mime_type(content_type)
    inferred = _CONTENT_TYPE_TO_EXT.get(mime)
    if inferred:
        return f"{clean_name}{inferred}"
    if is_voice_message:
        return f"{clean_name}.ogg"
    return clean_name


class DiscordAudioTranscriber:
    """Multi-backend speech-to-text transcriber for Discord voice messages and audio files."""

    def __init__(
        self,
        *,
        engine: Engine | None = None,
        secret_cipher: ProviderSecretCipher | None = None,
        whisper_engine: LocalWhisperEngine | None = None,
        agy_transcriber_factory: Callable[[], AgyMediaTranscriber] | None = None,
        http_transport: httpx.BaseTransport | None = None,
        default_language: str | None = None,
    ) -> None:
        self._engine = engine
        self._secret_cipher = secret_cipher
        self._whisper_engine = whisper_engine
        self._agy_factory = agy_transcriber_factory or AgyMediaTranscriber
        self._http_transport = http_transport
        self._default_language = default_language

    def _whisper(self) -> LocalWhisperEngine:
        if self._whisper_engine is None:
            self._whisper_engine = LocalWhisperEngine()
        return self._whisper_engine

    def _cipher(self) -> ProviderSecretCipher:
        if self._secret_cipher is not None:
            return self._secret_cipher
        self._secret_cipher = ProviderSecretCipher.from_environment(required=False)
        return self._secret_cipher

    def _resolve_cloud_credentials(self, user_id: str) -> list[CloudCredential]:
        creds: list[CloudCredential] = []
        seen_providers: set[str] = set()

        if self._engine is not None and user_id:
            try:
                pool = PostgresProviderApiKeyAdapter(self._engine, cipher=self._cipher())
                with self._engine.connect() as conn:
                    rows = (
                        conn.execute(
                            select(
                                provider_configurations.c.provider,
                                provider_configurations.c.base_url,
                                provider_configurations.c.enabled,
                                provider_configurations.c.secret_ref,
                            ).where(
                                provider_configurations.c.user_id == user_id,
                                provider_configurations.c.provider.in_(("openai", "groq", "openrouter")),
                            )
                        )
                        .mappings()
                        .all()
                    )
                by_provider = {str(r["provider"]).strip().lower(): dict(r) for r in rows}
                defaults = {
                    "openai": OPENAI_DEFAULT_BASE_URL,
                    "groq": GROQ_BASE_URL,
                    "openrouter": OPENROUTER_DEFAULT_BASE_URL,
                }
                for prov in ("openai", "groq", "openrouter"):
                    cfg = by_provider.get(prov)
                    if cfg is None or not cfg.get("enabled"):
                        continue
                    plaintext = ""
                    chosen = pool.next_available_key(user_id, prov)
                    if chosen is not None and chosen.plaintext:
                        plaintext = chosen.plaintext.strip()
                    elif cfg.get("secret_ref"):
                        try:
                            plaintext = self._cipher().decrypt(str(cfg["secret_ref"])).strip()
                        except Exception:
                            plaintext = ""
                    if plaintext:
                        base_url = str(cfg.get("base_url") or "").strip() or defaults[prov]
                        creds.append(CloudCredential(prov, base_url, plaintext))
                        seen_providers.add(prov)
            except Exception:
                _LOGGER.debug("Could not query cloud STT credentials for user %s", user_id, exc_info=True)

        env_fallbacks = (
            ("openai", "OPENAI_API_KEY", OPENAI_DEFAULT_BASE_URL),
            ("groq", "GROQ_API_KEY", GROQ_BASE_URL),
            ("openrouter", "OPENROUTER_API_KEY", OPENROUTER_DEFAULT_BASE_URL),
        )
        for prov, env_name, default_url in env_fallbacks:
            if prov in seen_providers:
                continue
            env_val = os.getenv(env_name, "").strip()
            if env_val:
                creds.append(CloudCredential(prov, default_url, env_val))
                seen_providers.add(prov)

        return creds

    def _is_antigravity_allowed(self, user_id: str) -> bool:
        if self._engine is None:
            return True
        try:
            with self._engine.connect() as conn:
                row = (
                    conn.execute(
                        select(
                            provider_configurations.c.enabled,
                            provider_configurations.c.risk_acknowledged_at,
                        ).where(
                            provider_configurations.c.user_id == user_id,
                            provider_configurations.c.provider == "google-antigravity",
                        )
                    )
                    .mappings()
                    .first()
                )
            if row is not None:
                return bool(row.get("enabled")) and row.get("risk_acknowledged_at") is not None
        except Exception:
            pass
        return False

    def transcribe_audio(
        self,
        *,
        user_id: str,
        audio_bytes: bytes,
        filename: str = "voice-message.ogg",
        content_type: str | None = None,
        language: str | None = None,
        audio_path: Path | str | None = None,
    ) -> str:
        """Transcribe an audio payload and return the non-empty spoken text."""
        if not audio_bytes and (audio_path is None or not Path(audio_path).is_file()):
            raise DiscordAudioTranscriptionError("O arquivo de áudio está vazio.")

        norm_name = normalize_discord_attachment_filename(
            filename,
            content_type,
            is_voice_message=True,
        )
        suffix = Path(norm_name).suffix.lower() or ".ogg"
        eff_mime = clean_mime_type(content_type) or "audio/ogg"
        eff_lang = language if language is not None else self._default_language

        errors: list[str] = []
        temp_file_path: Path | None = None

        def _ensure_local_path() -> Path:
            nonlocal temp_file_path
            if audio_path is not None:
                candidate = Path(audio_path)
                if candidate.is_file() and candidate.suffix.lower() == suffix:
                    return candidate
            if temp_file_path is not None and temp_file_path.is_file():
                return temp_file_path
            fd, raw_tmp = tempfile.mkstemp(prefix="orin-discord-audio-", suffix=suffix)
            os.close(fd)
            temp_file_path = Path(raw_tmp)
            payload = audio_bytes if audio_bytes else Path(str(audio_path)).read_bytes()
            temp_file_path.write_bytes(payload)
            return temp_file_path

        try:
            # 1. Local faster-whisper engine
            whisper = self._whisper()
            if whisper.is_installed():
                try:
                    local_path = _ensure_local_path()
                    text = whisper.transcribe_file(local_path, language=eff_lang)
                    if text and text.strip():
                        return text.strip()
                    errors.append("Whisper local não detectou fala audível")
                except VoiceEngineError as exc:
                    errors.append(str(exc))
                except Exception as exc:
                    _LOGGER.warning("Local Whisper failed on Discord audio %r: %s", norm_name, exc)
                    errors.append(f"Whisper local falhou ({exc})")

            # 2. Configured Cloud STT providers (OpenAI, Groq, OpenRouter)
            payload_bytes = audio_bytes if audio_bytes else _ensure_local_path().read_bytes()
            for cred in self._resolve_cloud_credentials(user_id):
                try:
                    if cred.provider == "openai":
                        text = transcribe_audio_file_openai_compatible(
                            cred,
                            payload_bytes,
                            filename=norm_name,
                            content_type=eff_mime,
                            language=eff_lang,
                            model_id="gpt-4o-mini-transcribe",
                            transport=self._http_transport,
                        )
                    elif cred.provider == "groq":
                        text = transcribe_audio_file_openai_compatible(
                            cred,
                            payload_bytes,
                            filename=norm_name,
                            content_type=eff_mime,
                            language=eff_lang,
                            model_id="whisper-large-v3-turbo",
                            transport=self._http_transport,
                        )
                    else:
                        text = transcribe_audio_file_openrouter(
                            cred,
                            payload_bytes,
                            audio_format=suffix.lstrip("."),
                            language=eff_lang,
                            model_id="openai/whisper-large-v3-turbo",
                            transport=self._http_transport,
                        )
                    if text and text.strip():
                        return text.strip()
                    errors.append(f"{cred.provider} não detectou fala audível")
                except VoiceEngineError as exc:
                    errors.append(str(exc))
                except Exception as exc:
                    _LOGGER.warning("Cloud STT (%s) failed on Discord audio %r: %s", cred.provider, norm_name, exc)
                    errors.append(f"{cred.provider} falhou ({exc})")

            # 3. Antigravity CLI subscription fallback
            if self._is_antigravity_allowed(user_id):
                try:
                    agy = self._agy_factory()
                    if agy.available():
                        local_path = _ensure_local_path()
                        text = agy.transcribe(local_path, kind="audio")
                        if text and text.strip():
                            return text.strip()
                except MediaTranscriptionError as exc:
                    errors.append(str(exc))
                except Exception as exc:
                    _LOGGER.warning("Antigravity STT failed on Discord audio %r: %s", norm_name, exc)
                    errors.append(f"Antigravity CLI falhou ({exc})")

            if errors:
                raise DiscordAudioTranscriptionError("; ".join(errors[:3]))
            raise DiscordAudioTranscriptionError(
                "Nenhum motor de transcrição (Whisper local, OpenAI, Groq, OpenRouter ou Antigravity CLI) "
                "está instalado ou configurado para ouvir áudios."
            )
        finally:
            if temp_file_path is not None:
                try:
                    temp_file_path.unlink(missing_ok=True)
                except OSError:
                    pass


__all__ = [
    "AUDIO_EXTENSIONS",
    "DISCORD_VOICE_MESSAGE_FLAG",
    "DiscordAudioTranscriber",
    "DiscordAudioTranscriptionError",
    "clean_mime_type",
    "is_discord_audio_attachment",
    "is_discord_media_attachment_hint",
    "is_discord_voice_message",
    "normalize_discord_attachment_filename",
]

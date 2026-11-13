"""Media tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

from agentos.agentic.media_client import (
    MAX_REFERENCE_IMAGE_BYTES,
    MediaGenerationError,
    ReferenceImageInput,
    detect_reference_image_mime,
)
from agentos.agentic.tools._shared import AgentToolError, ToolOutcome, WorkspaceError, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec
from agentos.pathsafety import absolute_inside, is_drive_path


class MediaTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def _resolve_media_client(self, target_provider: str | None = None):
        raw = self._media_client
        if callable(raw) and not hasattr(raw, "generate_image"):
            try:
                client = raw(target_provider) if target_provider is not None else raw()
            except TypeError:
                client = raw()
        else:
            client = raw
        if self._media_policy is None or client is None:
            raise AgentToolError("Ferramenta de geração de mídia indisponível para o provedor selecionado.")
        return client

    def _resolve_media_target(self, modality: str, requested_model: str | None) -> tuple[str, str]:
        if hasattr(self._media_policy, "resolve_target"):
            target = self._media_policy.resolve_target(modality, requested_model)
            return target.provider, target.model_id
        resolved_model = self._media_policy.resolve_model(modality, requested_model)
        return str(getattr(self._media_policy, "turn_provider", "") or ""), resolved_model

    def _resolve_media_output_path(self, output_path: str | None, *, default_prefix: str, ext: str) -> str:
        clean_ext = (ext or "bin").lstrip(".").lower() or "bin"
        if output_path is not None and str(output_path).strip():
            raw_rel = str(output_path).strip().replace("\\", "/").lstrip("/")
            if "." not in Path(raw_rel).name:
                raw_rel = f"{raw_rel}.{clean_ext}"
        else:
            raw_rel = f"generated/{default_prefix}-{uuid4().hex[:8]}.{clean_ext}"
        target = self.workspace.resolve(raw_rel)
        return self.workspace.relative(target)

    def _clean_and_resolve_reference_target(self, raw_value: str) -> Path:
        cleaned = str(raw_value or "").strip()
        had_workspace_scheme = cleaned.lower().startswith("workspace://")
        if had_workspace_scheme:
            cleaned = cleaned[len("workspace://") :]
        cleaned = unquote(cleaned).strip().replace("\\", "/")
        if not cleaned:
            raise AgentToolError(
                "Caminho de imagem de referência inválido.",
                error_code="INVALID_ARGUMENTS",
            )
        if cleaned.startswith("/") and not is_drive_path(cleaned):
            in_root = absolute_inside(cleaned, self.workspace.root) is not None
            in_extra = any(
                absolute_inside(cleaned, extra) is not None
                for extra in getattr(self.workspace, "additional_roots", ())
            )
            if had_workspace_scheme or (not in_root and not in_extra):
                cleaned = cleaned.lstrip("/")

        segments = [seg for seg in cleaned.split("/") if seg not in ("", ".")]
        if not segments or any(seg == ".." for seg in segments):
            raise AgentToolError(
                "Caminho de imagem de referência inválido (travessia de diretório não permitida).",
                error_code="INVALID_ARGUMENTS",
            )

        is_rel_input = had_workspace_scheme or (
            not is_drive_path(cleaned) and not cleaned.startswith("/")
        )
        if is_rel_input:
            raw_candidate = self.workspace.root.joinpath(*segments)
            if raw_candidate.is_symlink():
                try:
                    raw_candidate.resolve().relative_to(self.workspace.root.resolve())
                except (OSError, ValueError) as error:
                    raise AgentToolError(
                        "Caminho de imagem de referência aponta para fora do workspace.",
                        error_code="INVALID_ARGUMENTS",
                    ) from error

        try:
            resolved = self.workspace.resolve(cleaned)
        except WorkspaceError as error:
            raise AgentToolError(str(error), error_code="INVALID_ARGUMENTS") from error

        if is_rel_input:
            try:
                resolved.relative_to(self.workspace.root.resolve())
            except ValueError as error:
                raise AgentToolError(
                    "Caminho de imagem de referência deve permanecer dentro do workspace.",
                    error_code="INVALID_ARGUMENTS",
                ) from error

        return resolved

    def _resolve_reference_image(
        self,
        reference_image_path: str | None = None,
        reference_image_uri: str | None = None,
    ) -> tuple[ReferenceImageInput, str] | None:
        raw_path = str(reference_image_path or "").strip()
        raw_uri = str(reference_image_uri or "").strip()
        if not raw_path and not raw_uri:
            return None

        resolved_path = self._clean_and_resolve_reference_target(raw_path) if raw_path else None
        resolved_uri = self._clean_and_resolve_reference_target(raw_uri) if raw_uri else None
        if resolved_path is not None and resolved_uri is not None and resolved_path != resolved_uri:
            raise AgentToolError(
                "Os argumentos 'reference_image_path' e 'reference_image_uri' apontam para arquivos diferentes.",
                error_code="INVALID_ARGUMENTS",
            )
        resolved = resolved_path if resolved_path is not None else resolved_uri
        assert resolved is not None

        try:
            exists_and_file = resolved.exists() and resolved.is_file()
        except OSError as error:
            raise AgentToolError(
                f"Não foi possível acessar a imagem de referência: {error}",
                error_code="INVALID_ARGUMENTS",
            ) from error
        if not exists_and_file:
            raise AgentToolError(
                f"Imagem de referência não encontrada no workspace: {raw_path or raw_uri}",
                error_code="INVALID_ARGUMENTS",
            )

        try:
            stat = resolved.stat()
        except OSError as error:
            raise AgentToolError(
                f"Não foi possível inspecionar a imagem de referência: {error}",
                error_code="INVALID_ARGUMENTS",
            ) from error

        if stat.st_size <= 0:
            raise AgentToolError(
                "O arquivo de imagem de referência está vazio.",
                error_code="INVALID_ARGUMENTS",
            )
        if stat.st_size > MAX_REFERENCE_IMAGE_BYTES:
            raise AgentToolError(
                "A imagem de referência excede o limite máximo de 25 MB.",
                error_code="INVALID_ARGUMENTS",
            )

        try:
            raw_bytes = resolved.read_bytes()
        except OSError as error:
            raise AgentToolError(
                f"Não foi possível ler a imagem de referência: {error}",
                error_code="INVALID_ARGUMENTS",
            ) from error

        if not raw_bytes:
            raise AgentToolError(
                "O arquivo de imagem de referência está vazio.",
                error_code="INVALID_ARGUMENTS",
            )
        if len(raw_bytes) > MAX_REFERENCE_IMAGE_BYTES:
            raise AgentToolError(
                "A imagem de referência excede o limite máximo de 25 MB.",
                error_code="INVALID_ARGUMENTS",
            )

        try:
            mime_type, _ext = detect_reference_image_mime(raw_bytes)
        except MediaGenerationError as error:
            raise AgentToolError(
                "O arquivo de referência deve ser uma imagem válida (PNG, JPG, WEBP ou GIF).",
                error_code="INVALID_ARGUMENTS",
            ) from error

        try:
            ref_key = self.workspace.key(resolved)
        except WorkspaceError as error:
            raise AgentToolError(str(error), error_code="INVALID_ARGUMENTS") from error

        return (
            ReferenceImageInput(
                data=raw_bytes,
                mime_type=mime_type,
                filename=resolved.name,
                path=str(resolved),
            ),
            ref_key,
        )

    def generate_image(
        self,
        prompt: str,
        model: str | None = None,
        size: str = "1024x1024",
        output_path: str | None = None,
        reference_image_path: str | None = None,
        reference_image_uri: str | None = None,
    ) -> ToolOutcome:
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise AgentToolError("O argumento 'prompt' não pode ser vazio.", error_code="INVALID_ARGUMENTS")
        if self._media_policy is None:
            raise AgentToolError("Geração de imagem não habilitada para este provedor.", error_code="MEDIA_NOT_AVAILABLE")
        try:
            resolved_provider, resolved_model = self._resolve_media_target("image", model)
        except PermissionError as error:
            raise AgentToolError(str(error), error_code="MODEL_NOT_ALLOWED") from error
        if not resolved_model:
            allowed = ", ".join(self._media_policy.models_for("image")) or "nenhum"
            raise AgentToolError(
                f"Modelo de imagem inválido ou não favoritado ({self._media_policy.turn_provider}). Permitidos: {allowed}",
                error_code="MODEL_NOT_ALLOWED",
            )
        if output_path is not None and str(output_path).strip():
            self.workspace.resolve(str(output_path))
        resolved_ref = self._resolve_reference_image(
            reference_image_path=reference_image_path,
            reference_image_uri=reference_image_uri,
        )
        client = self._resolve_media_client(resolved_provider)
        try:
            if resolved_ref is None:
                data, ext = client.generate_image(
                    model=resolved_model,
                    prompt=clean_prompt,
                    size=str(size or "1024x1024"),
                )
            else:
                ref_image, _ref_key = resolved_ref
                try:
                    sig = inspect.signature(client.generate_image)
                    accepts_ref = "reference_image" in sig.parameters or any(
                        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                    )
                except (TypeError, ValueError):
                    accepts_ref = True
                if not accepts_ref:
                    raise AgentToolError(
                        "O cliente de geração de imagem do provedor ativo não suporta imagem de referência.",
                        error_code="MEDIA_GENERATION_FAILED",
                    )
                data, ext = client.generate_image(
                    model=resolved_model,
                    prompt=clean_prompt,
                    size=str(size or "1024x1024"),
                    reference_image=ref_image,
                )
        except MediaGenerationError as error:
            raise AgentToolError(str(error), error_code="MEDIA_GENERATION_FAILED") from error
        rel_path = self._resolve_media_output_path(output_path, default_prefix="image", ext=ext)
        written = self.workspace.write_bytes(rel_path, data, maximum_bytes=100 * 1024 * 1024)
        workspace_uri = f"workspace://{rel_path}"
        markdown_ref = f"![{Path(rel_path).name}]({workspace_uri})"
        summary = f"Imagem gerada em {rel_path} ({resolved_model})"
        payload = {
            "tool_kind": "media",
            "modality": "image",
            "path": rel_path,
            "uri": workspace_uri,
            "markdown": markdown_ref,
            "model": resolved_model,
            "provider": resolved_provider or self._media_policy.turn_provider,
            "bytes": written,
        }
        if resolved_ref is not None:
            payload["reference_image"] = resolved_ref[1]
        return ToolOutcome(
            "succeeded",
            summary[:240],
            json.dumps(payload, ensure_ascii=False, indent=2),
            payload,
        )

    def generate_audio(
        self,
        text: str,
        model: str | None = None,
        voice: str = "alloy",
        response_format: str = "mp3",
        output_path: str | None = None,
    ) -> ToolOutcome:
        from agentos.agentic.media_client import MediaGenerationError

        clean_text = str(text or "").strip()
        if not clean_text:
            raise AgentToolError("O argumento 'text' não pode ser vazio.", error_code="INVALID_ARGUMENTS")
        if self._media_policy is None:
            raise AgentToolError("Geração de áudio não habilitada para este provedor.", error_code="MEDIA_NOT_AVAILABLE")
        try:
            resolved_provider, resolved_model = self._resolve_media_target("audio", model)
        except PermissionError as error:
            raise AgentToolError(str(error), error_code="MODEL_NOT_ALLOWED") from error
        if not resolved_model:
            allowed = ", ".join(self._media_policy.models_for("audio")) or "nenhum"
            raise AgentToolError(
                f"Modelo de áudio inválido ou não favoritado ({self._media_policy.turn_provider}). Permitidos: {allowed}",
                error_code="MODEL_NOT_ALLOWED",
            )
        if output_path is not None and str(output_path).strip():
            self.workspace.resolve(str(output_path))
        client = self._resolve_media_client(resolved_provider)
        try:
            data, ext = client.generate_audio(
                model=resolved_model,
                text=clean_text,
                voice=str(voice or "alloy"),
                response_format=str(response_format or "mp3"),
            )
        except MediaGenerationError as error:
            raise AgentToolError(str(error), error_code="MEDIA_GENERATION_FAILED") from error
        rel_path = self._resolve_media_output_path(output_path, default_prefix="audio", ext=ext)
        written = self.workspace.write_bytes(rel_path, data, maximum_bytes=100 * 1024 * 1024)
        workspace_uri = f"workspace://{rel_path}"
        markdown_ref = f"[{Path(rel_path).name}]({workspace_uri})"
        summary = f"Áudio gerado em {rel_path} ({resolved_model})"
        payload = {
            "tool_kind": "media",
            "modality": "audio",
            "path": rel_path,
            "uri": workspace_uri,
            "markdown": markdown_ref,
            "model": resolved_model,
            "provider": resolved_provider or self._media_policy.turn_provider,
            "bytes": written,
        }
        return ToolOutcome(
            "succeeded",
            summary[:240],
            json.dumps(payload, ensure_ascii=False, indent=2),
            payload,
        )

    def generate_video(
        self,
        prompt: str,
        model: str | None = None,
        output_path: str | None = None,
    ) -> ToolOutcome:
        from agentos.agentic.media_client import MediaGenerationError

        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise AgentToolError("O argumento 'prompt' não pode ser vazio.", error_code="INVALID_ARGUMENTS")
        if self._media_policy is None:
            raise AgentToolError("Geração de vídeo não habilitada para este provedor.", error_code="MEDIA_NOT_AVAILABLE")
        try:
            resolved_provider, resolved_model = self._resolve_media_target("video", model)
        except PermissionError as error:
            raise AgentToolError(str(error), error_code="MODEL_NOT_ALLOWED") from error
        if not resolved_model:
            allowed = ", ".join(self._media_policy.models_for("video")) or "nenhum"
            raise AgentToolError(
                f"Modelo de vídeo inválido ou não favoritado ({self._media_policy.turn_provider}). Permitidos: {allowed}",
                error_code="MODEL_NOT_ALLOWED",
            )
        if output_path is not None and str(output_path).strip():
            self.workspace.resolve(str(output_path))
        client = self._resolve_media_client(resolved_provider)
        try:
            data, ext = client.generate_video(
                model=resolved_model,
                prompt=clean_prompt,
            )
        except MediaGenerationError as error:
            raise AgentToolError(str(error), error_code="MEDIA_GENERATION_FAILED") from error
        rel_path = self._resolve_media_output_path(output_path, default_prefix="video", ext=ext)
        written = self.workspace.write_bytes(rel_path, data, maximum_bytes=100 * 1024 * 1024)
        workspace_uri = f"workspace://{rel_path}"
        markdown_ref = f"[{Path(rel_path).name}]({workspace_uri})"
        summary = f"Vídeo gerado em {rel_path} ({resolved_model})"
        payload = {
            "tool_kind": "media",
            "modality": "video",
            "path": rel_path,
            "uri": workspace_uri,
            "markdown": markdown_ref,
            "model": resolved_model,
            "provider": resolved_provider or self._media_policy.turn_provider,
            "bytes": written,
        }
        return ToolOutcome(
            "succeeded",
            summary[:240],
            json.dumps(payload, ensure_ascii=False, indent=2),
            payload,
        )


# Tool specs, moved from AgentToolset._build_definitions (W4).
SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        key="generate_image",
        description="Generate an image using a favorited image model from the active provider and save it into the conversation workspace. Supports optional visual reference images (e.g. from '.orin/uploads/...' or 'workspace://...') via reference_image_path or reference_image_uri to preserve character identity, style, or composition. Link the returned workspace:// URI in your final response so the user can view it inline.",
        legacy_schema=lambda self: (
            _schema(
                {
                    "prompt": {**_TEXT, "description": "Detailed prompt describing the image to generate."},
                    "model": {
                        **_TEXT,
                        "description": f"Optional model ID from active provider favorites ({', '.join((self._media_policy.models_for("image")))}). Defaults to '{(self._media_policy.default_model_for("image") or "")}'.",
                    },
                    "size": {
                        **_TEXT,
                        "description": "Optional image dimensions (e.g. '1024x1024', '1536x1024', '1024x1536'). Defaults to '1024x1024'.",
                    },
                    "output_path": {
                        **_TEXT,
                        "description": "Optional workspace-relative path to save the generated image (e.g. 'generated/banner.png').",
                    },
                    "reference_image_path": {
                        **_TEXT,
                        "description": "Optional workspace path or workspace:// URI of a reference image (e.g. '.orin/uploads/ref.png' or 'workspace://generated/char.png') to guide style, character, or composition.",
                    },
                    "reference_image_uri": {
                        **_TEXT,
                        "description": "Optional workspace path or workspace:// URI of a reference image (e.g. 'workspace://.orin/uploads/ref.png' or 'workspace://generated/char.png') to guide style, character, or composition.",
                    },
                },
                ("prompt",),
            )
        ),
        handler="generate_image",
        kind="media",
        policy_tags=("mutates",),
        available=lambda self: (self._media_policy is not None and not self._read_only_mode) and (self._media_policy.has_modality("image")),
        order=600,
    ),
    ToolSpec(
        key="generate_audio",
        description="Generate audio or speech using a favorited audio model from the active provider and save it into the conversation workspace. Link the returned workspace:// URI in your final response so the user can listen inline.",
        legacy_schema=lambda self: (
            _schema(
                {
                    "text": {**_TEXT, "description": "Text to synthesize into speech or audio prompt to generate."},
                    "model": {
                        **_TEXT,
                        "description": f"Optional model ID from active provider favorites ({', '.join((self._media_policy.models_for("audio")))}). Defaults to '{(self._media_policy.default_model_for("audio") or "")}'.",
                    },
                    "voice": {
                        **_TEXT,
                        "description": "Optional voice identifier (e.g. 'alloy', 'nova', 'echo', 'onyx', 'fable', 'shimmer'). Defaults to 'alloy'.",
                    },
                    "response_format": {
                        "type": "string",
                        "enum": ["mp3", "wav", "opus", "aac", "flac"],
                        "description": "Optional output audio format (default 'mp3').",
                    },
                    "output_path": {
                        **_TEXT,
                        "description": "Optional workspace-relative path to save the generated audio (e.g. 'generated/audio.mp3').",
                    },
                },
                ("text",),
            )
        ),
        handler="generate_audio",
        kind="media",
        policy_tags=("mutates",),
        available=lambda self: (self._media_policy is not None and not self._read_only_mode) and (self._media_policy.has_modality("audio")),
        order=610,
    ),
    ToolSpec(
        key="generate_video",
        description="Generate a video clip using a favorited video model from the active provider and save it into the conversation workspace. Link the returned workspace:// URI in your final response so the user can watch inline.",
        legacy_schema=lambda self: (
            _schema(
                {
                    "prompt": {**_TEXT, "description": "Detailed prompt describing the video to generate."},
                    "model": {
                        **_TEXT,
                        "description": f"Optional model ID from active provider favorites ({', '.join((self._media_policy.models_for("video")))}). Defaults to '{(self._media_policy.default_model_for("video") or "")}'.",
                    },
                    "output_path": {
                        **_TEXT,
                        "description": "Optional workspace-relative path to save the generated video (e.g. 'generated/clip.mp4').",
                    },
                },
                ("prompt",),
            )
        ),
        handler="generate_video",
        kind="media",
        policy_tags=("mutates",),
        available=lambda self: (self._media_policy is not None and not self._read_only_mode) and (self._media_policy.has_modality("video")),
        order=620,
    ),
)

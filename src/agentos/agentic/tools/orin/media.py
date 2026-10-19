"""Media tools: methods of AgentToolset that live in their own module (W4 split)."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from agentos.agentic.tools._shared import AgentToolError, ToolOutcome, _TEXT, _schema
from agentos.agentic.tools.spec import ToolSpec


class MediaTools:
    """Mixed into ``AgentToolset``; ``self`` is the toolset."""

    def _resolve_media_client(self):
        client = self._media_client
        if callable(client) and not hasattr(client, "generate_image"):
            client = client()
            self._media_client = client
        if self._media_policy is None or client is None:
            raise AgentToolError("Ferramenta de geração de mídia indisponível para o provedor atual.")
        return client

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

    def generate_image(
        self,
        prompt: str,
        model: str | None = None,
        size: str = "1024x1024",
        output_path: str | None = None,
    ) -> ToolOutcome:
        from agentos.agentic.media_client import MediaGenerationError

        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise AgentToolError("O argumento 'prompt' não pode ser vazio.", error_code="INVALID_ARGUMENTS")
        if self._media_policy is None:
            raise AgentToolError("Geração de imagem não habilitada para este provedor.", error_code="MEDIA_NOT_AVAILABLE")
        try:
            resolved_model = self._media_policy.resolve_model("image", model)
        except PermissionError as error:
            raise AgentToolError(str(error), error_code="MODEL_NOT_ALLOWED") from error
        if resolved_model is None:
            allowed = ", ".join(self._media_policy.models_for("image")) or "nenhum"
            raise AgentToolError(
                f"Modelo de imagem inválido ou não favoritado no provedor ativo ({self._media_policy.turn_provider}). Permitidos: {allowed}",
                error_code="MODEL_NOT_ALLOWED",
            )
        if output_path is not None and str(output_path).strip():
            self.workspace.resolve(str(output_path))
        client = self._resolve_media_client()
        try:
            data, ext = client.generate_image(
                model=resolved_model,
                prompt=clean_prompt,
                size=str(size or "1024x1024"),
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
            "provider": self._media_policy.turn_provider,
            "bytes": written,
        }
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
            resolved_model = self._media_policy.resolve_model("audio", model)
        except PermissionError as error:
            raise AgentToolError(str(error), error_code="MODEL_NOT_ALLOWED") from error
        if resolved_model is None:
            allowed = ", ".join(self._media_policy.models_for("audio")) or "nenhum"
            raise AgentToolError(
                f"Modelo de áudio inválido ou não favoritado no provedor ativo ({self._media_policy.turn_provider}). Permitidos: {allowed}",
                error_code="MODEL_NOT_ALLOWED",
            )
        if output_path is not None and str(output_path).strip():
            self.workspace.resolve(str(output_path))
        client = self._resolve_media_client()
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
            "provider": self._media_policy.turn_provider,
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
            resolved_model = self._media_policy.resolve_model("video", model)
        except PermissionError as error:
            raise AgentToolError(str(error), error_code="MODEL_NOT_ALLOWED") from error
        if resolved_model is None:
            allowed = ", ".join(self._media_policy.models_for("video")) or "nenhum"
            raise AgentToolError(
                f"Modelo de vídeo inválido ou não favoritado no provedor ativo ({self._media_policy.turn_provider}). Permitidos: {allowed}",
                error_code="MODEL_NOT_ALLOWED",
            )
        if output_path is not None and str(output_path).strip():
            self.workspace.resolve(str(output_path))
        client = self._resolve_media_client()
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
            "provider": self._media_policy.turn_provider,
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
        description="Generate an image using a favorited image model from the active provider and save it into the conversation workspace. Link the returned workspace:// URI in your final response so the user can view it inline.",
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

"""Read a picture with a model and return its text.

One non-streaming-shaped call over the streaming transport the worker already
builds: reusing it means the provider payload, the error normalization and the
credential handling are the ones the chat loop is already tested on.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from agentos.agentic.provider_content import image_block
from agentos.agentic.provider_stream import StreamKind

from .selection import VisionModel

MAX_TRANSCRIPTION_CHARS = 20_000
DEFAULT_INSTRUCTION = (
    "Transcreva todo o texto visível desta imagem, preservando a ordem de leitura, "
    "títulos, tabelas e valores. Descreva brevemente elementos não textuais "
    "relevantes (gráficos, diagramas, fotos). Não invente conteúdo ausente."
)


class VisionUnavailable(RuntimeError):
    """No model could read the image, or the read produced nothing."""


class VisionReader:
    def __init__(self, transport_factory: Callable[[VisionModel], object], *, model: VisionModel | None) -> None:
        self._transport_factory = transport_factory
        self.model = model

    def transcribe(self, images: Sequence[tuple[str, str]], *, instruction: str = "") -> str:
        """Transcribe ``(base64, media_type)`` images into one text."""
        if self.model is None:
            raise VisionUnavailable("no visual reading model is configured")
        if not images:
            raise VisionUnavailable("no image to read")
        transport = self._transport_factory(self.model)
        if transport is None:
            raise VisionUnavailable("the visual reading model is unavailable")
        content: list[dict[str, str]] = [{"type": "text", "text": f"{instruction.strip()}\n\n{DEFAULT_INSTRUCTION}".strip()}]
        content.extend(image_block(media_type, data) for data, media_type in images)
        parts: list[str] = []
        try:
            for item in transport.stream({"messages": [{"role": "user", "content": content}], "tools": []}):
                if item.kind is StreamKind.ERROR:
                    raise VisionUnavailable("the visual reading model failed")
                if item.kind is StreamKind.TEXT and item.text:
                    parts.append(item.text)
        except VisionUnavailable:
            raise
        except Exception as error:
            raise VisionUnavailable("the visual reading model failed") from error
        finally:
            closer = getattr(transport, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
        text = "".join(parts).strip()
        if not text:
            raise VisionUnavailable("the visual reading model returned nothing")
        return text[:MAX_TRANSCRIPTION_CHARS]


__all__ = ["DEFAULT_INSTRUCTION", "VisionReader", "VisionUnavailable"]

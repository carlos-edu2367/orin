"""Turn a page or a picture into a bounded JPEG a model can be shown."""
from __future__ import annotations

import base64
import io
from pathlib import Path

MAX_IMAGE_EDGE = 1568
MAX_IMAGE_PIXELS = 50_000_000
MAX_RENDERED_PAGES = 20
JPEG_QUALITY = 85


class ImageTooLarge(ValueError):
    """The image would cost more to decode than it is worth."""


def _encode(image) -> str:
    from PIL import Image

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    if max(image.size) > MAX_IMAGE_EDGE:
        scale = MAX_IMAGE_EDGE / max(image.size)
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def normalize_image(path: Path, *, max_pixels: int = MAX_IMAGE_PIXELS) -> tuple[str, str]:
    """Return ``(base64_jpeg, media_type)`` for one picture on disk."""
    from PIL import Image

    with Image.open(path) as image:
        if image.width * image.height > max_pixels:
            raise ImageTooLarge(f"{path.name} is too large to decode")
        image.load()
        return _encode(image), "image/jpeg"


def render_pdf_pages(path: Path, pages: tuple[int, ...], *, max_pages: int = MAX_RENDERED_PAGES) -> list[tuple[str, str]]:
    """Rasterize 1-based ``pages`` of a PDF into bounded JPEGs."""
    import pypdfium2

    document = pypdfium2.PdfDocument(str(path))
    try:
        total = len(document)
        wanted = [number for number in pages if 1 <= number <= total][:max_pages]
        rendered: list[tuple[str, str]] = []
        for number in wanted:
            page = document[number - 1]
            bitmap = page.render(scale=2)
            rendered.append((_encode(bitmap.to_pil()), "image/jpeg"))
        return rendered
    finally:
        document.close()


__all__ = ["ImageTooLarge", "MAX_IMAGE_EDGE", "MAX_IMAGE_PIXELS", "MAX_RENDERED_PAGES", "normalize_image", "render_pdf_pages"]

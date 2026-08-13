from pathlib import Path

import pytest

from agentos.reading.render import MAX_IMAGE_PIXELS, ImageTooLarge, normalize_image, render_pdf_pages


def test_normalize_returns_base64_and_a_media_type(tmp_path: Path):
    Image = pytest.importorskip("PIL.Image")
    target = tmp_path / "foto.png"
    Image.new("RGB", (40, 40), "white").save(target)
    data, media_type = normalize_image(target)
    assert media_type == "image/jpeg"
    assert isinstance(data, str) and len(data) > 32


def test_normalize_shrinks_a_large_image(tmp_path: Path):
    Image = pytest.importorskip("PIL.Image")
    import base64
    import io

    target = tmp_path / "grande.png"
    Image.new("RGB", (4000, 1000), "white").save(target)
    data, _ = normalize_image(target)
    restored = Image.open(io.BytesIO(base64.b64decode(data)))
    assert max(restored.size) == 1568


def test_normalize_refuses_a_pixel_bomb(tmp_path: Path):
    Image = pytest.importorskip("PIL.Image")
    target = tmp_path / "bomba.png"
    Image.new("RGB", (10, 10), "white").save(target)
    with pytest.raises(ImageTooLarge):
        normalize_image(target, max_pixels=16)


def test_render_pdf_pages_returns_one_image_per_requested_page(tmp_path: Path):
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("pypdfium2")
    target = tmp_path / "doc.pdf"
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    with target.open("wb") as handle:
        writer.write(handle)
    images = render_pdf_pages(target, (1, 2))
    assert len(images) == 2
    assert all(media_type == "image/jpeg" for _, media_type in images)


def test_render_pdf_pages_is_bounded(tmp_path: Path):
    pypdf = pytest.importorskip("pypdf")
    pytest.importorskip("pypdfium2")
    target = tmp_path / "doc.pdf"
    writer = pypdf.PdfWriter()
    for _ in range(30):
        writer.add_blank_page(width=100, height=100)
    with target.open("wb") as handle:
        writer.write(handle)
    assert len(render_pdf_pages(target, tuple(range(1, 31)), max_pages=4)) == 4


def test_max_image_pixels_is_declared():
    assert MAX_IMAGE_PIXELS > 0

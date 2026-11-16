from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from agentos.providers.codex_cli.attachments import validated_codex_images


def _write_png(path: Path, size: int = 64) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * max(0, size - 8)
    path.write_bytes(payload[:size] if size < 8 else payload)
    return path


def test_accepts_workspace_images_with_spaces_parentheses_and_accents(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()

    img1 = _write_png(ws / ".orin" / "uploads" / "image (5).png")
    img2 = _write_png(ws / "portfólio" / "Imagem do ChatGPT (2).png")
    img3 = _write_png(ws / "Meus Assets" / "FOTO.JPG")
    img4 = _write_png(ws / "anim.gif")
    img5 = _write_png(ws / "hero.webp")
    img6 = _write_png(ws / "photo.jpeg")

    result = validated_codex_images(
        [
            ".orin/uploads/image (5).png",
            "portfólio/Imagem do ChatGPT (2).png",
            img3,
            "anim.gif",
            "hero.webp",
            "photo.jpeg",
        ],
        ws,
    )

    assert result == (
        img1.resolve(),
        img2.resolve(),
        img3.resolve(),
        img4.resolve(),
        img5.resolve(),
        img6.resolve(),
    )


def test_rejects_outside_workspace_dotdot_comma_dash_svg_size_and_missing(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()

    valid = _write_png(ws / "sub" / "ok.png")
    outside_img = _write_png(outside_dir / "secret.png")
    _write_png(ws / "with,comma.png")
    _write_png(ws / "dir,comma" / "clean.png")
    _write_png(ws / "-leading-dash.png")
    _write_png(ws / "sub" / "-dash-in-subdir.png")
    _write_png(ws / "-dashdir" / "inside.png")
    svg_file = ws / "vector.svg"
    svg_file.write_text("<svg></svg>", encoding="utf-8")
    empty_png = _write_png(ws / "empty.png", size=0)
    oversized_png = ws / "huge.png"
    with oversized_png.open("wb") as fh:
        fh.seek(20 * 1024 * 1024)
        fh.write(b"\x00")
    sub_dir = ws / "folder.png"
    sub_dir.mkdir()

    # Create a junction or symlink inside workspace pointing outside
    escaped_link_dir = ws / "escaped_link"
    link_created = False
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(escaped_link_dir), str(outside_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        link_created = completed.returncode == 0 and escaped_link_dir.exists()
    else:
        try:
            escaped_link_dir.symlink_to(outside_dir, target_is_directory=True)
            link_created = True
        except OSError:
            link_created = False

    candidates: list[str | Path] = [
        outside_img,
        "../outside/secret.png",
        "sub/../sub/ok.png",
        "with,comma.png",
        "dir,comma/clean.png",
        "-leading-dash.png",
        "sub/-dash-in-subdir.png",
        "-dashdir/inside.png",
        "vector.svg",
        empty_png,
        oversized_png,
        "nonexistent.png",
        sub_dir,
        "sub/ok.png\n--evil",
        "sub/ok.png\x00.png",
        "sub/ok.png",
    ]
    if link_created:
        candidates.insert(0, escaped_link_dir / "secret.png")

    result = validated_codex_images(candidates, ws)
    assert result == (valid.resolve(),)


def test_deduplicates_preserving_order_and_enforces_max_images(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "workspace"
    ws.mkdir()

    imgs = [_write_png(ws / f"img_{i}.png") for i in range(10)]

    # Duplicate via relative string, Path, and resolved Path
    paths: list[str | Path] = [
        "img_0.png",
        imgs[0],
        imgs[0].resolve(),
    ]
    if os.name == "nt":
        paths.append("IMG_0.PNG")
    for i in range(1, 10):
        paths.append(f"img_{i}.png")

    result = validated_codex_images(paths, ws)
    assert len(result) == 8
    assert result == tuple(img.resolve() for img in imgs[:8])

    assert validated_codex_images(paths, ws, max_images=3) == tuple(
        img.resolve() for img in imgs[:3]
    )
    assert validated_codex_images(paths, ws, max_images=0) == ()

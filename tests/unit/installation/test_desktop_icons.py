from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_orin_favicon_svg_uses_rounded_square_and_canonical_mascot() -> None:
    svg_path = REPO_ROOT / "frontend" / "src" / "assets" / "orin-favicon.svg"
    content = svg_path.read_text(encoding="utf-8")

    # Must be strictly well-formed XML
    root = ET.fromstring(content)
    assert root.tag.endswith("svg")

    assert 'viewBox="0 0 160 160"' in content
    assert "<rect" in content
    assert 'rx="38"' in content
    assert "M 55 46" in content
    assert "M 76 105 Q 81 112 87 104" in content
    assert 'cx="61"' in content and 'cx="101"' in content
    # Must not contain legacy spike/ring paths
    assert "M32 23L35.2 49.5" not in content
    assert "M32 24 27.5 52h9L32 24Z" not in content
    assert "M18 43a22 22 0 1 1 28 0" not in content


def test_orin_logo_pngs_are_identical_1024_rgba_with_squircle_and_mascot() -> None:
    desktop_png = REPO_ROOT / "desktop" / "electron" / "assets" / "orin-logo.png"
    frontend_src_png = REPO_ROOT / "frontend" / "src" / "assets" / "orin-logo.png"
    frontend_public_png = REPO_ROOT / "frontend" / "public" / "orin-logo.png"

    assert desktop_png.is_file()
    assert frontend_src_png.is_file()
    assert frontend_public_png.is_file()

    desktop_bytes = desktop_png.read_bytes()
    assert desktop_bytes == frontend_src_png.read_bytes()
    assert desktop_bytes == frontend_public_png.read_bytes()

    with Image.open(desktop_png) as img:
        assert img.format == "PNG"
        assert img.size == (1024, 1024)
        assert img.mode == "RGBA"

        # Outer corner (0, 0) and (1023, 1023) must be transparent due to rounded-square shape
        assert img.getpixel((0, 0))[3] == 0
        assert img.getpixel((1023, 0))[3] == 0
        assert img.getpixel((0, 1023))[3] == 0
        assert img.getpixel((1023, 1023))[3] == 0

        # Inside the rounded-square margin (140, 140) must be dark violet-indigo background
        bg_r, bg_g, bg_b, bg_a = img.getpixel((140, 140))
        assert bg_a == 255
        assert 10 <= bg_r <= 60
        assert 8 <= bg_g <= 50
        assert 20 <= bg_b <= 90
        assert bg_b > bg_g

        # Center (512, 512) must be the pastel lavender Orin mascot body
        c_r, c_g, c_b, c_a = img.getpixel((512, 512))
        assert c_a == 255
        assert c_r > 150
        assert c_g > 130
        assert c_b > 210

        # Left eye (~412, 554) and right eye (~623, 544) must render dark charcoal features (#282333)
        for eye_pt in ((412, 554), (623, 544)):
            e_r, e_g, e_b, e_a = img.getpixel(eye_pt)
            assert e_a == 255
            assert e_r < 65 and e_g < 60 and e_b < 80


def test_orin_logo_ico_has_multi_resolution_frames_derived_from_new_mascot() -> None:
    ico_path = REPO_ROOT / "desktop" / "electron" / "assets" / "orin-logo.ico"
    assert ico_path.is_file()

    required_sizes = {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)}
    with Image.open(ico_path) as ico:
        assert ico.format == "ICO"
        sizes = set(ico.info.get("sizes", set()))
        assert required_sizes.issubset(sizes)

        rgba = ico.convert("RGBA")
        w, h = rgba.size
        # Outer corner of the ICO must be transparent (rounded square)
        assert rgba.getpixel((0, 0))[3] == 0
        # Center of the ICO must be the pastel lavender Orin mascot body
        c_r, c_g, c_b, c_a = rgba.getpixel((w // 2, h // 2))
        assert c_a == 255
        assert c_r > 150
        assert c_g > 130
        assert c_b > 210


def test_desktop_splash_and_main_cjs_use_mascot_and_windows_app_id() -> None:
    splash_html = (REPO_ROOT / "desktop" / "electron" / "splash.html").read_text(encoding="utf-8")
    main_cjs = (REPO_ROOT / "desktop" / "electron" / "main.cjs").read_text(encoding="utf-8")
    desktop_pkg = json.loads((REPO_ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))

    assert '<span class="orb-mark">O</span>' not in splash_html
    assert "orb-mark" in splash_html
    assert "M 55 46" in splash_html

    # Extract inline SVG inside .orb-mark and verify it is well-formed XML
    match = re.search(r'<span class="orb-mark">\s*(<svg[\s\S]*?</svg>)\s*</span>', splash_html)
    assert match is not None
    ET.fromstring(match.group(1))

    app_id = desktop_pkg["build"]["appId"]
    assert app_id == "dev.orin.desktop"
    assert f"setAppUserModelId('{app_id}')" in main_cjs
    assert (REPO_ROOT / "desktop" / desktop_pkg["build"]["icon"]).is_file()
    assert (REPO_ROOT / "desktop" / desktop_pkg["build"]["linux"]["icon"]).is_file()

"""Builds the Cena art assets from the two source images.

    python -I frontend/scripts/scene-art/build_scene_art.py [--preview out.png]

Inputs  (frontend/scripts/scene-art/source/):
  office.png   empty office background (any size close to 5:3)
  sprites.png  sprite sheet, transparent background: row 0 = Orin, rows 1..4 = people,
               first cell of each row is a portrait (skipped)

Outputs (frontend/src/features/conversations/scene/assets/):
  office.webp          background resized to 1600x960 (5 px per logical scene pixel)
  characters.webp      packed atlas of every frame, kept at the sheet's own resolution
  sceneArtManifest.ts  frame rectangles and foot anchors (generated, do not edit)

Requires Pillow, numpy and scipy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "source"
OUT = HERE.parents[1] / "src" / "features" / "conversations" / "scene" / "assets"

SCENE_W, SCENE_H = 320, 192
BG_W, BG_H = SCENE_W * 5, SCENE_H * 5

# Logical scene pixels per sheet pixel (a standing person is ~150 sheet px -> ~45 scene px).
ART_SCALE = 0.3
# The Orin mascot is drawn smaller than the people (~100 sheet px -> ~22 scene px).
ORIN_SCALE = 0.22

# Server racks of the office source image, where the blinking LEDs are searched.
RACK_BOX = (1340, 60, 1600, 340)

ORIN_POSES = ["idle", "happy", "think", "ask", "scared", "sleep", "sparkle", "move", "dash"]
PERSON_POSES = ["idle", "walk1", "walk2", "desk", "laptop", "think", "ask", "wait", "scared", "relax"]

# Head-and-shoulders cut of the "working at the desk" frame (back view), in sheet pixels.
# The polygon stops at the top of the sheet's own chair: the office chair is drawn over it.
SEATED_CUTS: dict[int, list[tuple[int, int]]] = {
    1: [(570, 308), (570, 262), (580, 251), (598, 246), (616, 248), (633, 255), (643, 266),
        (643, 285), (637, 292), (637, 308)],
    2: [(567, 505), (567, 470), (575, 452), (585, 440), (600, 436), (612, 443), (625, 452),
        (636, 460), (638, 475), (637, 494), (631, 500), (628, 505)],
    3: [(562, 697), (562, 668), (568, 660), (575, 640), (590, 630), (605, 630), (620, 640),
        (626, 652), (627, 672), (626, 690), (627, 697)],
    4: [(560, 892), (560, 860), (568, 845), (580, 833), (595, 829), (610, 832), (622, 840),
        (630, 852), (636, 862), (636, 882), (628, 892)],
}
# x of the sheet chair's centre in each "desk" frame: the cut is centred on the office chair.
SEATED_CHAIR_CX = {1: 601, 2: 603, 3: 594, 4: 596}


def find_bands(solid: np.ndarray, axis: int, min_count: int, min_len: int) -> list[tuple[int, int]]:
    counts = solid.sum(axis=axis)
    bands: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(counts):
        if v > min_count and start is None:
            start = i
        elif v <= min_count and start is not None:
            if i - start >= min_len:
                bands.append((start, i))
            start = None
    if start is not None and len(counts) - start >= min_len:
        bands.append((start, len(counts)))
    return bands


def merge_close(cells: list[tuple[int, int]], gap: int) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for c in cells:
        if merged and c[0] - merged[-1][1] < gap:
            merged[-1] = (merged[-1][0], c[1])
        else:
            merged.append(c)
    return merged


def tight_box(alpha: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(alpha > 8)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def foot_anchor(alpha: np.ndarray) -> tuple[float, float]:
    """Bottom-centre of the opaque body (ignores soft shadows and side effects)."""
    solid = alpha > 200
    ys, xs = np.nonzero(solid)
    bottom = int(ys.max()) + 1
    top = int(ys.min())
    band = solid[max(top, bottom - max(8, (bottom - top) // 4)):bottom]
    bx = np.nonzero(band)[1]
    return float((bx.min() + bx.max() + 1) / 2), float(bottom)


def slice_sheet(sheet: Image.Image) -> list[dict]:
    rgba = np.asarray(sheet)
    solid = rgba[..., 3] > 160
    rows = find_bands(solid, axis=1, min_count=20, min_len=30)
    if len(rows) != 5:
        raise SystemExit(f"expected 5 sprite rows, found {len(rows)}: {rows}")
    frames: list[dict] = []
    for r, (y0, y1) in enumerate(rows):
        cells = merge_close(find_bands(solid[y0:y1], axis=0, min_count=0, min_len=1), gap=10)
        cells = cells[1:]  # portrait
        poses = ORIN_POSES if r == 0 else PERSON_POSES
        if len(cells) != len(poses):
            raise SystemExit(f"row {r}: expected {len(poses)} frames, found {len(cells)}")
        who = "orin" if r == 0 else f"p{r}"
        scale = ORIN_SCALE if r == 0 else ART_SCALE
        for (x0, x1), pose in zip(cells, poses):
            # Grow the band a little so soft glows are not clipped, then trim to content.
            gx0, gx1 = max(0, x0 - 6), min(sheet.width, x1 + 6)
            gy0, gy1 = max(0, y0 - 6), min(sheet.height, y1 + 6)
            crop = sheet.crop((gx0, gy0, gx1, gy1))
            a = np.asarray(crop)[..., 3]
            # Drop the faint row separators that run across the whole sheet.
            a_clean = a.copy()
            a_clean[a_clean < 40] = 0
            bx0, by0, bx1, by1 = tight_box(a_clean)
            img = crop.crop((bx0, by0, bx1, by1))
            arr = np.array(img)
            arr[..., 3][arr[..., 3] < 40] = 0
            img = Image.fromarray(arr)
            ax, ay = foot_anchor(arr[..., 3])
            frames.append({"name": f"{who}.{pose}", "img": img, "ax": ax, "ay": ay, "s": scale})
        if r > 0:
            frames.append(seated_cut(sheet, r))
    return frames


def seated_cut(sheet: Image.Image, row: int) -> dict:
    poly = SEATED_CUTS[row]
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    box = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)
    mask = Image.new("L", sheet.size, 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    cut = Image.new("RGBA", sheet.size, (0, 0, 0, 0))
    cut.paste(sheet, (0, 0), mask)
    img = cut.crop(box)
    # Anchor = chair centre at the bottom edge of the cut (where the office backrest starts).
    return {
        "name": f"p{row}.seated",
        "img": img,
        "ax": float(SEATED_CHAIR_CX[row] - box[0]),
        "ay": float(img.height),
        "s": ART_SCALE,
    }


def find_rack_leds(office_src: Image.Image) -> list[list[float]]:
    """Small saturated blobs on the server racks, as logical [x, y, w, h] rects."""
    from scipy import ndimage

    x0, y0, x1, y1 = RACK_BOX
    sub = np.asarray(office_src.convert("RGB")).astype(int)[y0:y1, x0:x1]
    hi, lo = sub.max(-1), sub.min(-1)
    labels, _ = ndimage.label((hi > 150) & ((hi - lo) > 70))
    kx = SCENE_W / office_src.width
    ky = SCENE_H / office_src.height
    leds: list[list[float]] = []
    for sl in ndimage.find_objects(labels):
        h = sl[0].stop - sl[0].start
        w = sl[1].stop - sl[1].start
        if h * w < 6 or h > 14 or w > 14:
            continue
        leds.append([
            round((sl[1].start + x0) * kx, 2), round((sl[0].start + y0) * ky, 2),
            round(w * kx, 2), round(h * ky, 2),
        ])
    return leds


def pack(frames: list[dict], max_w: int = 1400, pad: int = 2) -> Image.Image:
    order = sorted(frames, key=lambda f: -f["img"].height)
    x = y = row_h = 0
    for f in order:
        w, h = f["img"].size
        if x + w > max_w:
            x, y, row_h = 0, y + row_h + pad, 0
        f["x"], f["y"] = x, y
        x += w + pad
        row_h = max(row_h, h)
    atlas = Image.new("RGBA", (max_w, y + row_h), (0, 0, 0, 0))
    for f in order:
        atlas.paste(f["img"], (f["x"], f["y"]))
    return atlas


def write_manifest(frames: list[dict], atlas: Image.Image, leds: list[list[float]], path: Path) -> None:
    entries = {
        f["name"]: {
            "x": f["x"], "y": f["y"], "w": f["img"].width, "h": f["img"].height,
            "ax": round(f["ax"], 1), "ay": round(f["ay"], 1), "s": f["s"],
        }
        for f in sorted(frames, key=lambda f: f["name"])
    }
    body = json.dumps(entries, indent=2)
    led_rows = ",\n".join(f"  [{', '.join(str(v) for v in led)}]" for led in leds)
    path.write_text(
        "// Generated by frontend/scripts/scene-art/build_scene_art.py. Do not edit by hand.\n\n"
        "export interface SceneArtFrame {\n"
        "  readonly x: number\n  readonly y: number\n  readonly w: number\n  readonly h: number\n"
        "  /** Foot anchor inside the frame, in atlas pixels. */\n"
        "  readonly ax: number\n  readonly ay: number\n"
        "  /** Logical scene pixels per atlas pixel. */\n  readonly s: number\n}\n\n"
        f"export const SCENE_ART_ATLAS_SIZE = {{ w: {atlas.width}, h: {atlas.height} }} as const\n\n"
        f"export const SCENE_ART_FRAMES: Readonly<Record<string, SceneArtFrame>> = {body}\n\n"
        "/** Rack LEDs of the office art as logical [x, y, width, height]. */\n"
        "export const SCENE_ART_RACK_LEDS: ReadonlyArray<readonly [number, number, number, number]> = [\n"
        f"{led_rows},\n]\n",
        encoding="utf-8",
    )


def build_preview(bg: Image.Image, frames: dict[str, dict], out: Path) -> None:
    """Rough composite used to check scale and positions (not shipped)."""
    k = BG_W / SCENE_W
    canvas = bg.convert("RGBA").copy()

    def put(name: str, fx: float, fy: float) -> None:
        f = frames[name]
        s = f["s"] * k
        img = f["img"].resize((round(f["img"].width * s), round(f["img"].height * s)), Image.LANCZOS)
        canvas.alpha_composite(img, (round(fx * k - f["ax"] * s), round(fy * k - f["ay"] * s)))

    for name, x, y in [
        ("p1.idle", 77, 76), ("p2.think", 138, 78), ("p3.ask", 292, 79),
        ("orin.idle", 168, 137), ("p4.wait", 137, 137), ("p2.walk1", 34, 184),
        ("orin.sleep", 214.3, 163.7), ("p3.relax", 232, 186), ("p1.relax", 192, 186),
    ]:
        put(name, x, y)
    for name, cx, top in [("p1.seated", 63.6, 106.7), ("p4.seated", 210.7, 51.0),
                          ("p2.seated", 272.8, 106.3), ("p3.seated", 119.8, 163.1)]:
        put(name, cx, top + 2)
    canvas.convert("RGB").save(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", type=Path, help="also write a composite preview PNG")
    args = parser.parse_args()

    office_src = Image.open(SOURCE / "office.png").convert("RGB")
    office = office_src.resize((BG_W, BG_H), Image.LANCZOS)
    leds = find_rack_leds(office_src)
    sheet = Image.open(SOURCE / "sprites.png").convert("RGBA")

    frames = slice_sheet(sheet)
    atlas = pack(frames)

    OUT.mkdir(parents=True, exist_ok=True)
    office.save(OUT / "office.webp", "WEBP", quality=88, method=6)
    atlas.save(OUT / "characters.webp", "WEBP", quality=90, method=6, exact=False)
    write_manifest(frames, atlas, leds, OUT / "sceneArtManifest.ts")
    for name in ("office.webp", "characters.webp"):
        print(name, (OUT / name).stat().st_size // 1024, "KB")
    print("atlas", atlas.size, "frames", len(frames), "leds", len(leds))

    if args.preview:
        build_preview(office, {f["name"]: f for f in frames}, args.preview)


if __name__ == "__main__":
    main()

"""Camera pod previews: face and back depth maps + shaded views -> out/preview-camera-pod-*.png, out/hero-camera-pod-*.png.

Run:  uv run --no-project --with trimesh --with rtree --with numpy --with pillow python camera_pin_views.py
Needs out/camera-pod-*-assembled.stl from camera_pin.py.
"""
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw, ImageFont

import hero

OUT = Path(__file__).parent / "out"


def depth_map(stl, name, from_front=True, step=0.06):
    """Light = outer surface, grey = engraving, darker = deeper, black = through (seen from the front or from behind)."""
    m = trimesh.load(OUT / stl)
    lo, hi = m.bounds
    xs, ys = np.arange(lo[0] - 1, hi[0] + 1, step), np.arange(lo[1] - 1, hi[1] + 1, step)
    X, Y = np.meshgrid(xs, ys)
    z0, d, ref = (hi[2] + 5, -1.0, hi[2]) if from_front else (lo[2] - 5, 1.0, lo[2])
    origins = np.column_stack([X.ravel(), Y.ravel(), np.full(X.size, z0)])
    loc, idx, _ = m.ray.intersects_location(origins, np.tile([0, 0, d], (X.size, 1)), multiple_hits=False)
    depth = np.full(X.size, np.nan)
    depth[idx] = np.abs(loc[:, 2] - ref)
    depth = depth.reshape(X.shape)
    img = np.where(np.isnan(depth), 0.96, np.where(depth < 0.05, 0.86, np.where(depth < 0.7, 0.55,
                   np.where(depth < 1.3, 0.38, 0.08))))
    img = img[::-1] if from_front else img[::-1, ::-1]
    Image.fromarray((img * 255).astype(np.uint8)).save(OUT / name)
    print(OUT / name)


def sheet():
    """One overview image: face, back, and two shaded views, labelled."""
    tiles = [("Face (front)", "preview-camera-pod-face.png"), ("Back", "preview-camera-pod-back.png"),
             ("Three-quarter", "hero-camera-pod-front.png"), ("Side", "hero-camera-pod-side.png")]
    H = 760
    imgs = []
    for label, f in tiles:
        im = Image.open(OUT / f).convert("RGB")
        im.thumbnail((H, H))
        imgs.append((label, im))
    W = sum(im.width for _, im in imgs) + 40 * (len(imgs) + 1)
    out = Image.new("RGB", (W, H + 170), (238, 240, 244))
    d = ImageDraw.Draw(out)
    try:
        big = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 40)
        small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 26)
    except OSError:
        big = small = ImageFont.load_default()
    d.text((40, 30), "EXO ARGO camera pod v0.1 (draft)", fill=(20, 22, 28), font=big)
    d.text((40, 82), "Pi Camera Rev 1.3 in a hand-held, pinnable pod: 34 x 60.5 x 14.3 mm, grip 30 mm, one M2 screw. "
                     "Renders only: no print files until the camera board is measured.", fill=(80, 84, 94), font=small)
    x = 40
    for label, im in imgs:
        out.paste(im, (x, 140 + (H - im.height) // 2))
        d.text((x, H + 130), label, fill=(80, 84, 94), font=small)
        x += im.width + 40
    out.save(OUT / "camera-pod-sheet.png")
    print(OUT / "camera-pod-sheet.png")


if __name__ == "__main__":
    depth_map("camera-pod-shell-assembled.stl", "preview-camera-pod-face.png")
    depth_map("camera-pod-back-assembled.stl", "preview-camera-pod-back.png", from_front=False)
    parts = [("camera-pod-shell-assembled", (0, 0, 0), (0.17, 0.19, 0.22)),
             ("camera-pod-back-assembled", (0, 0, 0), (0.42, 0.44, 0.48))]
    hero.render("camera-pod-front", parts, azim=-115, elev=38, px=900)
    hero.render("camera-pod-side", parts, azim=-10, elev=15, px=900)
    hero.render("camera-pod-back", parts, azim=70, elev=-40, px=900)
    sheet()

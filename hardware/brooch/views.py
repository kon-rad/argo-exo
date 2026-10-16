"""Brooch previews: face depth map + shaded views (painter's algorithm, no GPU) -> out/preview-*.png, out/hero-*.png.

Run:  uv run --no-project --with trimesh --with rtree --with numpy --with pillow --with matplotlib python views.py
Needs the STLs from brooch_case.py in out/.
"""
import sys
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent / "case"))
import hero  # noqa: E402  (the deck case's rasterizer)

OUT = Path(__file__).parent / "out"
hero.OUT = OUT                                  # read parts from and write to this folder
render = hero.render


def depth_map(stl, name, from_front=True, step=0.08):
    """Seen from the front (+z) or from behind (-z): light = outer surface, grey = engraving, black = through."""
    m = trimesh.load(OUT / stl)
    lo, hi = m.bounds
    xs, ys = np.arange(lo[0], hi[0], step), np.arange(lo[1], hi[1], step)
    X, Y = np.meshgrid(xs, ys)
    z0, d = (hi[2] + 5, -1.0) if from_front else (lo[2] - 5, 1.0)
    origins = np.column_stack([X.ravel(), Y.ravel(), np.full(X.size, z0)])
    loc, idx, _ = m.ray.intersects_location(origins, np.tile([0, 0, d], (X.size, 1)), multiple_hits=False)
    depth = np.full(X.size, np.nan)
    depth[idx] = np.abs(loc[:, 2] - (hi[2] if from_front else lo[2]))
    depth = depth.reshape(X.shape)
    img = np.where(np.isnan(depth), 0.0, np.where(depth < 0.05, 0.88, np.where(depth < 1.0, 0.55, 0.3)))
    img = img[::-1]
    if not from_front:
        img = img[:, ::-1]                          # as seen from behind
    Image.fromarray((img * 255).astype(np.uint8)).save(OUT / name)
    print(OUT / name)


depth_map("brooch-shell-assembled.stl", "preview-face.png")
depth_map("brooch-back-assembled.stl", "preview-back.png", from_front=False)
PARTS = [("brooch-shell-assembled", (0, 0, 0), (0.17, 0.19, 0.22)), ("brooch-back-assembled", (0, 0, 0), (0.42, 0.44, 0.48))]
render("brooch-front", PARTS, azim=-110, elev=35, px=900)
render("brooch-side", PARTS, azim=-20, elev=20, px=900)
render("brooch-back", PARTS, azim=70, elev=-35, px=900)

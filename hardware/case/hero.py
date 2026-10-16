"""Shaded views of the assembled case (orthographic, painter's algorithm, no GPU) -> out/hero-*.png.

Run:  uv run --no-project --with trimesh --with numpy --with pillow python hero.py
Needs the STLs from pi4_deck_case.py in out/.
"""
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "out"
TOP = 27.5
PARTS = [("shell", (0, 0, 0), (0.17, 0.19, 0.22)), ("base", (0, 0, 0), (0.42, 0.44, 0.48)),
         ("hatch-ribbon", (0, 0, TOP), (0.78, 0.62, 0.18)), ("camera-bracket", (72, 7, TOP), (0.20, 0.55, 0.36))]


def scene(parts):
    meshes, colors = [], []
    for name, off, col in parts:
        m = trimesh.load(OUT / f"{name}.stl")
        m.apply_translation(off)
        meshes.append(m)
        colors.append(np.tile(col, (len(m.faces), 1)))
    return trimesh.util.concatenate(meshes), np.vstack(colors)


def render(name, parts, azim, elev, px=1000, ss=2):
    """Painter's algorithm: triangles sorted far to near and filled with PIL, drawn at ss x size then downscaled."""
    mesh, fcol = scene(parts)
    a, e = np.radians(azim), np.radians(elev)
    d = -np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])      # view direction
    right = np.cross(d, [0, 0, 1.0]) if abs(d[2]) < 0.99 else np.array([1.0, 0, 0])
    right /= np.linalg.norm(right)
    up = np.cross(right, d)
    n = mesh.face_normals
    keep = n @ d < 0                                                               # back faces off
    tris, n, fcol = mesh.triangles[keep], n[keep], fcol[keep]
    key = np.array([-0.4, -0.6, 0.7]); key /= np.linalg.norm(key)
    rim = np.array([0.6, 0.5, 0.3]); rim /= np.linalg.norm(rim)
    h = (key - d) / np.linalg.norm(key - d)
    shade = 0.30 + 0.60 * np.clip(n @ key, 0, 1) + 0.22 * np.clip(n @ rim, 0, 1)
    col = np.clip(fcol * shade[:, None] + (np.clip(n @ h, 0, 1) ** 40 * 0.35)[:, None], 0, 1)
    c = mesh.bounds.mean(0)
    span = np.linalg.norm(mesh.extents) * 0.55
    size = px * ss
    sx = ((tris - c) @ right / span * 0.5 + 0.5) * size
    sy = (0.5 - (tris - c) @ up / span * 0.5) * size
    depth = ((tris - c) @ d).mean(1)
    img = Image.new("RGB", (size, size), (238, 240, 244))
    draw = ImageDraw.Draw(img)
    for i in np.argsort(-depth):
        rgb = tuple(int(v * 255) for v in col[i])
        draw.polygon(list(zip(sx[i], sy[i])), fill=rgb, outline=rgb)
    img.resize((px, px), Image.LANCZOS).save(OUT / f"hero-{name}.png")
    print(OUT / f"hero-{name}.png")


render("front", PARTS, azim=-125, elev=28)
render("back", PARTS, azim=55, elev=22)
render("lid", PARTS[:1], azim=-75, elev=52)

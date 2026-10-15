"""Preview PNGs of the case from the STLs in out/ (matplotlib, no GPU)."""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import trimesh
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

OUT = Path(__file__).parent / "out"


def draw(ax, mesh, offset, color):
    tris = mesh.vertices[mesh.faces] + np.array(offset)
    normals = mesh.face_normals
    light = np.clip(normals @ np.array([0.3, -0.5, 0.8]) * 0.5 + 0.55, 0.2, 1.0)
    cols = np.array(matplotlib.colors.to_rgb(color))[None, :] * light[:, None]
    ax.add_collection3d(Poly3DCollection(tris, facecolors=cols, edgecolor="none"))
    return tris.reshape(-1, 3)


def scene(name, items, elev, azim):
    fig = plt.figure(figsize=(9, 7), dpi=110)
    ax = fig.add_subplot(projection="3d")
    pts = [draw(ax, trimesh.load(OUT / f"{part}.stl"), off, col) for part, off, col in items]
    pts = np.vstack(pts)
    lo, hi = pts.min(0), pts.max(0)
    mid, span = (lo + hi) / 2, (hi - lo).max() / 2
    ax.set_xlim(mid[0] - span, mid[0] + span); ax.set_ylim(mid[1] - span, mid[1] + span); ax.set_zlim(mid[2] - span, mid[2] + span)
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()
    fig.savefig(OUT / f"preview-{name}.png", bbox_inches="tight")
    plt.close(fig)
    print(OUT / f"preview-{name}.png")


TOP = 27.5
CAM = (72, 12, TOP)          # camera bracket on the lid holes (62, 8) and (82, 8)
scene("assembled", [("shell", (0, 0, 0), "#3a3f47"), ("hatch-ribbon", (0, 0, TOP), "#c9a227"),
                    ("camera-bracket", CAM, "#2f8f5b"), ("strap-plate", (0, 0, -3), "#6b4f2a")], 30, -60)
scene("ports", [("shell", (0, 0, 0), "#3a3f47"), ("base", (0, 0, 0), "#8a8f99")], 12, -110)
scene("exploded", [("strap-plate", (0, 0, -20), "#6b4f2a"), ("base", (0, 0, 0), "#8a8f99"), ("shell", (0, 0, 25), "#3a3f47"),
                   ("hatch-open", (0, 0, 25 + TOP + 12), "#c9a227"), ("camera-bracket", (72, 12, 25 + TOP + 12), "#2f8f5b")], 22, 50)
scene("top", [("shell", (0, 0, 0), "#3a3f47")], 90, -90)
scene("strap", [("strap-plate", (0, 0, 0), "#6b4f2a")], 90, -90)

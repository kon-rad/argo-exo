"""Top-down depth map of the shell's lid (shows the engraving, holes and slots) -> out/preview-lid.png."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import trimesh

OUT = Path(__file__).parent / "out"
m = trimesh.load(OUT / "shell.stl")
lo, hi = m.bounds
step = 0.15
xs = np.arange(lo[0], hi[0], step)
ys = np.arange(lo[1], hi[1], step)
X, Y = np.meshgrid(xs, ys)
origins = np.column_stack([X.ravel(), Y.ravel(), np.full(X.size, hi[2] + 5)])
dirs = np.tile([0, 0, -1.0], (X.size, 1))
loc, idx, _ = m.ray.intersects_location(origins, dirs, multiple_hits=False)
Z = np.full(X.size, np.nan)
Z[idx] = loc[:, 2]
Z = Z.reshape(X.shape)
top = hi[2]
img = np.where(np.isnan(Z), 0.0, np.where(Z > top - 0.05, 0.85, np.where(Z > top - 0.9, 0.55, np.where(Z > top - 1.5, 0.35, 0.05))))
plt.figure(figsize=(10, 7), dpi=120)
plt.imshow(img, origin="lower", cmap="gray", vmin=0, vmax=1, extent=[lo[0], hi[0], lo[1], hi[1]])
plt.title("Lid seen from above: light = lid surface, grey = 0.6 mm engraving, darker = 1.2 mm, black = through")
plt.savefig(OUT / "preview-lid.png", bbox_inches="tight")
print(OUT / "preview-lid.png")

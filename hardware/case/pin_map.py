"""Face-on depth map of the camera pin (what people see) -> out/preview-camera-pin.png."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import trimesh

OUT = Path(__file__).parent / "out"
m = trimesh.load(OUT / "camera-pin-face.stl")
lo, hi = m.bounds
step = 0.1
X, Y = np.meshgrid(np.arange(lo[0], hi[0], step), np.arange(lo[1], hi[1], step))
origins = np.column_stack([X.ravel(), Y.ravel(), np.full(X.size, lo[2] - 5)])     # look at the face from below
loc, idx, _ = m.ray.intersects_location(origins, np.tile([0, 0, 1.0], (X.size, 1)), multiple_hits=False)
Z = np.full(X.size, np.nan)
Z[idx] = loc[:, 2]
Z = Z.reshape(X.shape)
img = np.where(np.isnan(Z), 0.0, np.where(Z < 0.05, 0.85, np.where(Z < 0.6, 0.55, np.where(Z < 1.4, 0.35, 0.15))))
plt.figure(figsize=(7, 7), dpi=120)
plt.imshow(img[:, ::-1], origin="lower", cmap="gray", vmin=0, vmax=1, extent=[-hi[0], -lo[0], lo[1], hi[1]])
plt.title("Camera pin, face-on (mirrored to the wearer's view); ribbon exits at the bottom")
plt.savefig(OUT / "preview-camera-pin.png", bbox_inches="tight")
print(OUT / "preview-camera-pin.png")

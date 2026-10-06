"""The camera, memo and video library: what's on the SD card, what's in the cloud, safe serving."""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Callable

log = logging.getLogger("exo-media")

FOLDERS = {"photos": "outbox/photos", "audio": "outbox/audio", "video": "media/video"}
KIND = {"photos": "photo", "audio": "audio", "video": "video"}
EXT = {"photos": {".jpg", ".jpeg", ".png", ".webp"}, "audio": {".wav", ".mp3", ".m4a", ".ogg", ".opus"},
       "video": {".mp4", ".mov", ".mkv", ".webm", ".m4v"}}
PAGE = 9


def safe_path(deck_root, rel: str) -> Path | None:
    """Resolve rel inside one of the three media folders, or None. Symlinks are followed before the check,
    so a link inside a folder that points outside it is refused too."""
    if not rel or "\x00" in rel or rel.startswith("/") or "\\" in rel or "%" in rel:
        return None
    top, _, rest = rel.partition("/")
    parts = rest.split("/")
    if top not in FOLDERS or not rest or any(p in ("", ".", "..") for p in parts):
        return None
    base = (Path(deck_root) / FOLDERS[top]).resolve()
    try:
        p = (base / rest).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return p if p != base and p.is_relative_to(base) else None


def _cloud(manifest) -> tuple[set, int | None]:
    try:
        m = json.loads(Path(manifest).read_text())
        files = {f for f in m.get("files", []) if isinstance(f, str)}
        at = m.get("synced_at")
        return files, at if isinstance(at, int) else None
    except (OSError, ValueError, AttributeError):
        return set(), None


def index(deck_root, manifest) -> list[dict]:
    root = Path(deck_root)
    cloud, _ = _cloud(manifest)
    items, seen = [], set()
    for top, folder in FOLDERS.items():
        d = root / folder
        for p in sorted(d.rglob("*")) if d.exists() else []:
            if p.name.startswith(".") or p.suffix.lower() not in EXT[top]:   # skips .part uploads in flight
                continue
            rel = f"{top}/{p.relative_to(d).as_posix()}"
            real = safe_path(root, rel)
            if real is None or not real.is_file():                           # dangling or escaping symlink
                continue
            st = real.stat()
            items.append({"rel": rel, "kind": KIND[top], "size": st.st_size, "mtime": int(st.st_mtime),
                          "where": "both" if rel in cloud else "sd"})
            seen.add(rel)
    for rel in sorted(cloud - seen):
        top = rel.partition("/")[0]
        if top in KIND:
            items.append({"rel": rel, "kind": KIND[top], "size": None, "mtime": 0, "where": "cloud"})
    return sorted(items, key=lambda i: i["mtime"], reverse=True)


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=False, timeout=20, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def thumb(deck_root, rel: str, cache_dir, runner: Callable[[list[str]], None] = _run) -> Path | None:
    src = safe_path(deck_root, rel)
    if src is None or not src.is_file():
        return None
    out = Path(cache_dir) / (hashlib.sha1(rel.encode()).hexdigest()[:16] + ".jpg")
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        if rel.startswith("photos/"):
            from PIL import Image
            with Image.open(src) as im:
                im.thumbnail((480, 480))
                im.convert("RGB").save(out, "JPEG", quality=80)
        elif rel.startswith("video/"):
            runner(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", str(src), "-frames:v", "1",
                    "-vf", "scale=480:-2", str(out)])
    except Exception as exc:              # corrupt image, ffmpeg missing or timed out: no thumbnail, not a 500
        log.warning("thumb %s failed: %s", rel, exc)
        out.unlink(missing_ok=True)
        return None
    return out if out.exists() else None


def panel(deck_root, state, page: int) -> dict:
    root, state = Path(deck_root), Path(state)
    items = index(root, state / "media-cloud.json")
    _, synced_at = _cloud(state / "media-cloud.json")
    page = min(max(page, 0), max(0, (len(items) - 1) // PAGE))   # "more" past the end shows the last page
    shown = [dict(i, n=k + 1) for k, i in enumerate(items[page * PAGE:(page + 1) * PAGE])]
    try:
        n = int((state / "media-open").read_text().strip())
        open_item = next((i for i in shown if i["n"] == n), None)
    except (OSError, ValueError):
        open_item = None
    return {"items": shown, "page": page, "total": len(items), "sd_only": sum(i["where"] == "sd" for i in items),
            "cloud_only": sum(i["where"] == "cloud" for i in items), "synced_at": synced_at, "open": open_item}

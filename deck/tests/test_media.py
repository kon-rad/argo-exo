import json
import os
import subprocess

from exo_deck import media


def setup(tmp_path):
    for rel, t in (("outbox/photos/a.jpg", 100), ("outbox/photos/b.jpg", 300), ("outbox/audio/m.wav", 200), ("media/video/v.mp4", 400)):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 10)
        os.utime(p, (t, t))
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "media-cloud.json").write_text(json.dumps(
        {"synced_at": 500, "files": ["photos/a.jpg", "video/v.mp4", "photos/old.jpg"]}))
    return tmp_path


def test_index_newest_first_with_sync_state(tmp_path):
    items = media.index(setup(tmp_path), tmp_path / "state" / "media-cloud.json")
    by = {i["rel"]: i for i in items}
    assert [i["rel"] for i in items[:4]] == ["video/v.mp4", "photos/b.jpg", "audio/m.wav", "photos/a.jpg"]
    assert by["photos/a.jpg"]["where"] == "both" and by["photos/b.jpg"]["where"] == "sd"
    assert by["photos/old.jpg"]["where"] == "cloud" and by["video/v.mp4"]["kind"] == "video"


def test_safe_path_blocks_traversal(tmp_path):
    root = setup(tmp_path)
    assert media.safe_path(root, "photos/a.jpg") == (root / "outbox/photos/a.jpg").resolve()
    assert media.safe_path(root, "video/v.mp4") == (root / "media/video/v.mp4").resolve()
    for bad in ("../.env", "photos/../../.env", "/etc/passwd", "state/media-cloud.json", "photos/%2e%2e/x", "",
                "photos", "photos/", "photos/..", "photos/./a.jpg", "photos//a.jpg", "photos\\..\\x", "photos/a.jpg\x00.png",
                "outbox/photos/a.jpg", "photos_evil/a.jpg"):
        assert media.safe_path(root, bad) is None, bad


def test_safe_path_blocks_symlinks_and_sibling_folders(tmp_path):
    root = setup(tmp_path)
    secret = tmp_path / ".env"
    secret.write_text("SECRET=1")
    (root / "outbox/photos/link.jpg").symlink_to(secret)
    (root / "outbox/photos/dirlink").symlink_to(root / "state")
    evil = root / "outbox/photos_evil"
    evil.mkdir()
    (evil / "x.jpg").write_bytes(b"x")
    (root / "outbox/audio/via.wav").symlink_to(evil / "x.jpg")
    assert media.safe_path(root, "photos/link.jpg") is None
    assert media.safe_path(root, "photos/dirlink/media-cloud.json") is None
    assert media.safe_path(root, "audio/via.wav") is None
    assert media.safe_path(root, "photos/../photos_evil/x.jpg") is None
    rels = [i["rel"] for i in media.index(root, root / "state" / "media-cloud.json")]
    assert "photos/link.jpg" not in rels and "audio/via.wav" not in rels


def test_index_skips_partial_uploads_and_other_files(tmp_path):
    root = setup(tmp_path)
    (root / "outbox/photos/c.jpg.part").write_bytes(b"x")
    (root / "outbox/photos/notes.txt").write_bytes(b"x")
    rels = [i["rel"] for i in media.index(root, root / "state" / "media-cloud.json")]
    assert "photos/c.jpg.part" not in rels and "photos/notes.txt" not in rels


def test_panel_numbers_and_open(tmp_path):
    root = setup(tmp_path)
    (root / "state" / "media-open").write_text("2")
    p = media.panel(root, root / "state", 0)
    assert [i["n"] for i in p["items"]] == [1, 2, 3, 4, 5]
    assert p["open"]["rel"] == "photos/b.jpg" and p["sd_only"] == 2 and p["cloud_only"] == 1 and p["synced_at"] == 500


def test_panel_pages_and_clamps(tmp_path):
    root = setup(tmp_path)
    for k in range(12):
        (root / "outbox/photos" / f"p{k:02}.jpg").write_bytes(b"x")
    s = root / "state"
    assert len(media.panel(root, s, 0)["items"]) == 9
    p1 = media.panel(root, s, 1)
    assert p1["page"] == 1 and [i["n"] for i in p1["items"]] == list(range(1, 9))
    p9 = media.panel(root, s, 9)               # "more" past the end: last page, not empty
    assert p9["page"] == 1 and p9["items"]


def test_panel_open_ignores_garbage(tmp_path):
    root = setup(tmp_path)
    (root / "state" / "media-open").write_text("banana")
    assert media.panel(root, root / "state", 0)["open"] is None


def test_thumb_photo_with_pillow(tmp_path):
    from PIL import Image
    root = setup(tmp_path)
    Image.new("RGB", (2000, 1000), (200, 50, 50)).save(root / "outbox/photos/big.jpg")
    out = media.thumb(root, "photos/big.jpg", tmp_path / "thumbs")
    with Image.open(out) as im:
        assert max(im.size) == 480
    assert media.thumb(root, "photos/a.jpg", tmp_path / "thumbs") is None   # not an image: no thumb, no crash
    assert media.thumb(root, "../.env", tmp_path / "thumbs") is None


def test_thumb_video_uses_injected_runner(tmp_path):
    root = setup(tmp_path)
    calls = []

    def runner(cmd):
        calls.append(cmd)
        open(cmd[-1], "wb").write(b"jpg")
    out = media.thumb(root, "video/v.mp4", tmp_path / "thumbs", runner)
    assert out.read_bytes() == b"jpg" and calls[0][0] == "ffmpeg" and isinstance(calls[0], list)
    assert media.thumb(root, "audio/m.wav", tmp_path / "thumbs", runner) is None


def test_thumb_survives_ffmpeg_failure(tmp_path):
    root = setup(tmp_path)

    def boom(cmd):
        raise subprocess.TimeoutExpired(cmd, 20)
    assert media.thumb(root, "video/v.mp4", tmp_path / "thumbs", boom) is None


def test_sync_script_exits_quietly_without_rclone_config(tmp_path):
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / "bin" / "deck-media-sync"
    r = subprocess.run(["bash", str(script)], env={"DECK_ROOT": str(tmp_path), "PATH": os.environ["PATH"]},
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0 and r.stderr == "" and len(r.stdout.strip().splitlines()) == 1
    assert not (tmp_path / "state" / "media-cloud.json").exists()


def test_install_and_units_for_media_sync():
    from pathlib import Path
    deck = Path(__file__).resolve().parents[1]
    assert "rclone ffmpeg" in (deck / "install.sh").read_text()
    assert (deck / "systemd" / "deck-media-sync.service").is_file() and (deck / "systemd" / "deck-media-sync.timer").is_file()
    assert os.access(deck / "bin" / "deck-media-sync", os.X_OK)


def test_failed_thumb_leaves_no_partial_file(tmp_path):
    root = setup(tmp_path)
    cache = tmp_path / "thumbs"

    def half(cmd):
        open(cmd[-1], "wb").write(b"half")
        raise subprocess.TimeoutExpired(cmd, 20)
    assert media.thumb(root, "video/v.mp4", cache, half) is None
    assert list(cache.iterdir()) == []                      # no final file, no temp leftovers
    assert media.thumb(root, "photos/a.jpg", cache) is None  # corrupt jpeg
    assert list(cache.iterdir()) == []


def test_thumb_only_runs_ffmpeg_on_video_extensions(tmp_path):
    root = setup(tmp_path)
    (root / "media/video/notes.txt").write_bytes(b"x")
    calls = []
    assert media.thumb(root, "video/notes.txt", tmp_path / "thumbs", lambda c: calls.append(c)) is None
    assert calls == []

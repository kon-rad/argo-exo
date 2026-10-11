import importlib.util, json, os, time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "recorder" / "deck-recorder.py"


def load(tmp_path, **env):
    os.environ.update({"DECK_ROOT": str(tmp_path), "DECK_UPLOAD_TOKEN": "tok", **env})
    spec = importlib.util.spec_from_file_location("deck_recorder", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_video_goes_where_media_and_sync_look(tmp_path):
    mod = load(tmp_path)
    from exo_deck import media
    assert mod.VIDEO == tmp_path / media.FOLDERS["video"]


def test_stream_url_needs_a_fresh_heartbeat(tmp_path):
    mod = load(tmp_path, DECK_STREAM_URL="")
    assert mod.stream_url() is None
    (tmp_path / "clip-status.json").write_text(json.dumps({"seen": time.time(), "ip": "clip-host"}))
    assert mod.stream_url() == "http://clip-host:81/stream?t=tok"
    (tmp_path / "clip-status.json").write_text(json.dumps({"seen": time.time() - 60, "ip": "clip-host"}))
    assert mod.stream_url() is None


def test_video_args_add_the_mic_only_when_present(tmp_path):
    mod = load(tmp_path)
    without = mod.Recorder().video_args("http://clip/stream", with_mic=False)
    with_mic = mod.Recorder().video_args("http://clip/stream", with_mic=True)
    assert without.count("-i") == 1 and "-map" not in without
    assert with_mic.count("-i") == 2 and with_mic[with_mic.index("-i", with_mic.index("-i") + 1) + 1] == mod.MIC
    for args in (without, with_mic):
        assert args[args.index("-c:v") + 1] == "copy"
        assert "matroska" in args      # survives a crash mid-recording


def test_capture_video_toggle(tmp_path):
    import importlib.machinery
    os.environ["DECK_ROOT"] = str(tmp_path)
    loader = importlib.machinery.SourceFileLoader("deck_capture", str(SRC.parents[1] / "bin" / "deck-capture"))
    spec = importlib.util.spec_from_loader("deck_capture", loader)
    cap = importlib.util.module_from_spec(spec)
    loader.exec_module(cap)
    assert cap.main(["video", "toggle"]) == 0
    assert json.loads((tmp_path / "capture.json").read_text())["video"] is True
    assert cap.main(["off"]) == 0
    assert json.loads((tmp_path / "capture.json").read_text())["video"] is False

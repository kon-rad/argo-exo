import os
import signal
from pathlib import Path
from exo_deck import recorder


ARECORD = lambda pid: "arecord -q -d 30 rec.wav"
OTHER = lambda pid: "python deck-buttons.py"


class FakeProc:
    def __init__(self, pid=4242):
        self.pid = pid


def test_start_writes_pid_and_listening(tmp_path):
    launched = []
    assert recorder.start(tmp_path, ["arecord", "-q"], popen=lambda cmd, **kw: launched.append((cmd, kw)) or FakeProc())
    assert (tmp_path / "rec.pid").read_text() == "4242"
    assert (tmp_path / "listening").exists()
    cmd, kw = launched[0]
    assert cmd[-1] == str(tmp_path / "rec.wav") and "-d" in cmd and kw["start_new_session"]


def test_start_twice_keeps_one_recorder(tmp_path):
    (tmp_path / "rec.pid").write_text(str(os.getpid()))   # a live pid
    assert recorder.start(tmp_path, ["arecord"], popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned")),
                          cmdline=ARECORD) is False


def test_stop_signals_and_returns_wav(tmp_path):
    (tmp_path / "rec.pid").write_text("4242")
    (tmp_path / "listening").touch()
    (tmp_path / "rec.wav").write_bytes(b"x" * 20000)
    sent = []

    def kill(pid, sig):
        sent.append((pid, sig))
        if sig == 0 and signal.SIGINT in [s for _, s in sent]:
            raise ProcessLookupError   # exited after SIGINT

    assert recorder.stop(tmp_path, 9600, kill=kill, wait_s=0.1, cmdline=ARECORD) == tmp_path / "rec.wav"
    assert (4242, signal.SIGINT) in sent
    assert not (tmp_path / "listening").exists() and not (tmp_path / "rec.pid").exists()


def test_stop_short_recording_is_ignored(tmp_path):
    (tmp_path / "rec.pid").write_text("4242")
    (tmp_path / "rec.wav").write_bytes(b"x" * 100)

    def kill(pid, sig):
        if sig == 0:
            raise ProcessLookupError

    assert recorder.stop(tmp_path, 9600, kill=kill, wait_s=0.1, cmdline=ARECORD) is None


def test_stop_without_recording_returns_none(tmp_path):
    assert recorder.stop(tmp_path, 9600, kill=lambda *a: None, wait_s=0.1, cmdline=ARECORD) is None


def test_cancel_removes_audio(tmp_path):
    (tmp_path / "rec.pid").write_text("4242")
    (tmp_path / "rec.wav").write_bytes(b"x" * 20000)
    recorder.cancel(tmp_path, kill=lambda *a: (_ for _ in ()).throw(ProcessLookupError()), cmdline=ARECORD)
    assert not (tmp_path / "rec.wav").exists() and not (tmp_path / "rec.pid").exists()


def test_stale_non_arecord_pid_does_not_wedge_start(tmp_path):
    (tmp_path / "rec.pid").write_text("4242")
    assert recorder.start(tmp_path, ["arecord"], popen=lambda *a, **k: FakeProc(7), kill=lambda *a: None,
                          cmdline=OTHER) is True
    assert (tmp_path / "rec.pid").read_text() == "7"


def test_stop_never_signals_non_arecord_pid(tmp_path):
    (tmp_path / "rec.pid").write_text("4242")
    (tmp_path / "listening").touch()
    sent = []
    assert recorder.stop(tmp_path, 9600, kill=lambda p, s: sent.append((p, s)), wait_s=0.1, cmdline=OTHER) is None
    assert [x for x in sent if x[1] != 0] == []
    assert not (tmp_path / "rec.pid").exists() and not (tmp_path / "listening").exists()


def test_permission_error_cleans_up_without_raising(tmp_path):
    def kill(pid, sig):
        raise PermissionError

    for fn in (lambda: recorder.stop(tmp_path, 9600, kill=kill, wait_s=0.1, cmdline=ARECORD),
               lambda: recorder.cancel(tmp_path, kill=kill, cmdline=ARECORD)):
        (tmp_path / "rec.pid").write_text("4242")
        (tmp_path / "listening").touch()
        fn()
        assert not (tmp_path / "rec.pid").exists() and not (tmp_path / "listening").exists()


def test_stop_sigkills_a_stubborn_recorder(tmp_path):
    (tmp_path / "rec.pid").write_text("4242")
    sent = []
    recorder.stop(tmp_path, 9600, kill=lambda p, s: sent.append(s), wait_s=0.1, cmdline=ARECORD)
    assert signal.SIGKILL in sent

#!/usr/bin/env python3
"""Exo recorder: video from the XIAO clip + audio from the Pi's I2S mic, at the same time.

Follows /srv/deck/capture.json (written by deck-capture / deck-buttons):
  video=true   ffmpeg pulls the clip's MJPEG stream (http://<clip>:81/stream?t=<token>) and, if the Pi mic
               is present, records it into the same file -> media/video/vid_<time>.mkv while recording,
               then media/video/vid_<time>.mp4 (H.264 + AAC) once stopped
  mic=true     with the Pi mic present: outbox/audio/aud_<time>.wav, independent of video (both can run)
               without it: the clip's own mic records, as before (the collector tells it to)

Status for the red LED (deck-buttons): state/rec-video and state/rec-audio exist while each recording runs.
state/pi-mic exists while the Pi mic is usable; the collector then keeps the clip's mic off.

The mic is an ALSA device (DECK_MIC, default "exomic": mono, shared through a dsnoop in /etc/asound.conf,
see deck/alsa/asound-exomic.conf) so video, memos and push-to-talk (EXO_MIC_DEVICE=exomic) read it at once.
"""
import json, logging, os, shutil, signal, subprocess, threading, time
from pathlib import Path

DECK = Path(os.environ.get("DECK_ROOT", "/srv/deck"))
STATE, CAPTURE, CLIP = DECK / "state", DECK / "capture.json", DECK / "clip-status.json"
VIDEO, AUDIO = DECK / "media" / "video", DECK / "outbox" / "audio"   # where exo_deck.media and deck-media-sync look
TOKEN = os.environ.get("DECK_UPLOAD_TOKEN", "")
MIC = os.environ.get("DECK_MIC", "exomic")
MIC_ARGS = os.environ.get("DECK_MIC_ARGS", "-f alsa -channels 1 -sample_rate 48000").split()
MIC_FILTER = os.environ.get("DECK_MIC_FILTER", "")   # e.g. "pan=mono|c0=c0" if the device is stereo
STREAM = os.environ.get("DECK_STREAM_URL", "")                     # override for tests; else from the clip's IP
MAX_VIDEO_S = int(os.environ.get("DECK_MAX_VIDEO_S", "900"))      # safety stop
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
CAPTURE_BIN = os.environ.get("DECK_CAPTURE", "/usr/local/bin/deck-capture")

log = logging.getLogger("deck-recorder")


def stamp():
    return time.strftime("%Y%m%d_%H%M%S")


def read_json(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def mic_present():
    if os.environ.get("DECK_MIC_PRESENT") is not None:
        return os.environ["DECK_MIC_PRESENT"] == "1"
    try:
        return "adau7002" in Path("/proc/asound/cards").read_text().lower()
    except OSError:
        return False


def mark(name, on):
    path = STATE / name
    if on:
        path.touch()
    else:
        path.unlink(missing_ok=True)


def filt():
    return ["-af", MIC_FILTER] if MIC_FILTER else []


def stream_url():
    if STREAM:
        return STREAM
    clip = read_json(CLIP)
    if not clip.get("ip") or time.time() - clip.get("seen", 0) > 10:
        return None
    return f"http://{clip['ip']}:81/stream?t={TOKEN}"


class Job:
    """One ffmpeg process writing one file. stop() asks ffmpeg to finish the file cleanly."""

    def __init__(self, kind, args, out):
        self.kind, self.out, self.started = kind, out, time.time()
        log.info("%s start -> %s", kind, out.name)
        self.proc = subprocess.Popen([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", *args, str(out)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def alive(self):
        return self.proc.poll() is None

    def stop(self):
        # ffmpeg finishes the file on SIGINT, but a single one doesn't break a blocked network read;
        # a second one does (still a clean finish; ffmpeg only hard-exits after more than 3)
        for sig, wait in ((signal.SIGINT, 1.5), (signal.SIGINT, 8), (signal.SIGKILL, 5)):
            if not self.alive():
                break
            self.proc.send_signal(sig)
            try:
                self.proc.wait(wait)
            except subprocess.TimeoutExpired:
                pass
        err = self.proc.stderr.read().decode(errors="replace").strip() if self.proc.stderr else ""
        if err:
            log.info("%s ffmpeg: %s", self.kind, err[-500:])
        log.info("%s stop after %.0fs", self.kind, time.time() - self.started)


def finish_video(mkv):
    """mkv (MJPEG, raw audio) -> mp4 (H.264 + AAC), small enough to upload. Keeps the mkv if it fails."""
    if not mkv.exists() or mkv.stat().st_size < 1024:
        mkv.unlink(missing_ok=True)
        return
    mp4 = mkv.with_suffix(".mp4")
    part = mkv.with_suffix(".mp4.part")
    r = subprocess.run(["nice", "-n", "15", FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                        "-i", str(mkv), "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                        "-pix_fmt", "yuv420p", "-fps_mode", "vfr", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
                        "-f", "mp4", str(part)], capture_output=True)
    if r.returncode == 0:
        part.replace(mp4)
        mkv.unlink()
        log.info("video ready %s (%.1f MB)", mp4.name, mp4.stat().st_size / 1e6)
    else:
        part.unlink(missing_ok=True)
        log.warning("video encode failed, kept %s: %s", mkv.name, r.stderr.decode(errors="replace")[-300:])


class Recorder:
    def __init__(self):
        self.video = self.audio = None
        self.video_name = None

    def video_args(self, url, with_mic):
        args = ["-use_wallclock_as_timestamps", "1", "-thread_queue_size", "512",
                "-rw_timeout", "5000000", "-i", url]
        if with_mic:
            args += ["-use_wallclock_as_timestamps", "1", "-thread_queue_size", "4096", *MIC_ARGS, "-i", MIC,
                     "-map", "0:v", "-map", "1:a", *filt(), "-c:a", "pcm_s16le"]
        return args + ["-c:v", "copy", "-fps_mode", "passthrough", "-f", "matroska"]

    def tick(self):
        want = read_json(CAPTURE)
        pi_mic = mic_present()
        mark("pi-mic", pi_mic)

        # video (+ mic)
        want_video = bool(want.get("video"))
        if self.video and (not want_video or not self.video.alive()
                           or time.time() - self.video.started > MAX_VIDEO_S):
            if want_video and not self.video.alive():
                log.warning("video stream dropped; reconnecting into a new file")
            job, self.video = self.video, None
            job.stop()
            threading.Thread(target=finish_video, args=(job.out,), daemon=True).start()
            if want_video and time.time() - job.started > MAX_VIDEO_S:
                log.warning("video hit the %ds limit; turning video off", MAX_VIDEO_S)
                try:
                    subprocess.run([CAPTURE_BIN, "video", "off"], capture_output=True, timeout=10)
                except (OSError, subprocess.SubprocessError) as e:
                    log.warning("could not turn video off: %s", e)
                want_video = False
        if want_video and not self.video:
            url = stream_url()
            if url:
                VIDEO.mkdir(parents=True, exist_ok=True)
                self.video = Job("video", self.video_args(url, pi_mic), VIDEO / f"vid_{stamp()}.mkv")
        mark("rec-video", bool(self.video and self.video.alive()))

        # audio memo from the Pi mic (the clip's mic covers it when there's no Pi mic)
        want_audio = bool(want.get("mic")) and pi_mic
        if self.audio and (not want_audio or not self.audio.alive()):
            job, self.audio = self.audio, None
            job.stop()
        if want_audio and not self.audio:
            AUDIO.mkdir(parents=True, exist_ok=True)
            self.audio = Job("audio", [*MIC_ARGS, "-i", MIC, *filt(), "-ar", "16000",
                                       "-c:a", "pcm_s16le", "-f", "wav"], AUDIO / f"aud_{stamp()}.wav")
        mark("rec-audio", bool(self.audio and self.audio.alive()))

    def shutdown(self, *_):
        for job in (self.video, self.audio):
            if job:
                job.stop()
        if self.video:
            finish_video(self.video.out)
        for name in ("rec-video", "rec-audio"):
            mark(name, False)
        raise SystemExit(0)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if not shutil.which(FFMPEG):
        raise SystemExit("ffmpeg not found")
    STATE.mkdir(parents=True, exist_ok=True)
    for leftover in VIDEO.glob("*.mkv") if VIDEO.exists() else []:   # a crash mid-recording
        finish_video(leftover)
    rec = Recorder()
    signal.signal(signal.SIGTERM, rec.shutdown)
    signal.signal(signal.SIGINT, rec.shutdown)
    log.info("deck-recorder ready (pi mic: %s)", mic_present())
    while True:
        rec.tick()
        time.sleep(0.3)


if __name__ == "__main__":
    main()

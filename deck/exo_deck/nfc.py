"""Exo NFC reader (PN532 on the Pi's UART, HSU mode): reads and writes tags and logs every tap to the lifelog.
No transaction role: approvals stay on the buttons.

  deck-nfc                              reader state, last tap, any armed write
  deck-nfc read [--timeout 30]          wait for the next tap and print it
  deck-nfc write url <url> [--label <name>] [--timeout 30]
  deck-nfc write text "<text>" [--label <name>] [--timeout 30]
  deck-nfc label <uid> "<name>"         name a tag ("" removes the name)
  deck-nfc labels                       all named tags
  deck-nfc log [-n 20]                  recent taps
  deck-nfc run                          the service (deck-nfc.service)

read and write talk to the running service through files, so they need deck-nfc.service up.
Every tap goes to /srv/deck/outbox/nfc/<day>.jsonl (the kiosk shows the last one) and, as an argo-lifeos
event, to /srv/deck/outbox/lifeos/<day>.ndjson for lifeosd to ingest.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

log = logging.getLogger("deck-nfc")

DECK = Path(os.environ.get("DECK_ROOT", "/srv/deck"))
READER = os.environ.get("EXO_NFC_DEVICE", "tty:serial0:pn532")
DEBOUNCE_S, WRITE_TTL_S, FLASH_S = 5.0, 30.0, 3.0
MAX_VALUE = 500
UID = re.compile(r"^[0-9A-F]{4,32}$")
SOURCE = "exo-edge"


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _tz() -> str | None:
    tz = os.environ.get("TZ", "")
    if tz and "/" in tz:
        return tz.lstrip(":")
    try:
        link = os.readlink("/etc/localtime")
        return link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else None
    except OSError:
        return None


def _append(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.replace(path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def norm_uid(uid: str) -> str | None:
    u = re.sub(r"[\s:-]", "", str(uid)).upper()
    return u if UID.match(u) else None


# ---------- labels ----------

def labels_path(root: Path) -> Path:
    return root / "nfc-tags.json"


def labels(root: Path = DECK) -> dict:
    d = _read_json(labels_path(root))
    return {k: v for k, v in d.items() if isinstance(v, str)} if isinstance(d, dict) else {}


def set_label(root: Path, uid: str, name: str) -> None:
    d = labels(root)
    if name:
        d[uid] = name[:40]
    else:
        d.pop(uid, None)
    _write_json(labels_path(root), d)


# ---------- the log: one tap line + one lifeos event ----------

def lifeos_event(entry: dict, kind: str) -> dict:
    ev = {"type": "event", "source": SOURCE, "kind": kind, "start": entry["at"],
          "dedupe_key": f"nfc:{entry['uid']}:{int(entry['ts'] * 1000)}", "sensitivity": 1,
          "payload": {k: entry[k] for k in ("uid", "tag_type", "label", "ndef", "wrote") if k in entry}}
    tz = _tz()
    if tz:
        ev.update(tz=tz, tz_origin="device")
    if entry.get("label"):
        ev["title"] = f"NFC {entry['label']}"
    return ev


def record(root: Path, info: dict, now: float, kind: str = "nfc_tap", extra: dict | None = None,
           oled_flash: Callable | None = None) -> dict:
    """Append a tap (or a write) to the tap log and the lifeos outbox; flash it on the OLED."""
    uid = info["uid"]
    label = labels(root).get(uid, "")
    entry = {"ts": round(now, 3), "at": _iso(now), "kind": kind, "uid": uid,
             "tag_type": info.get("tag_type", ""), "label": label, "ndef": info.get("ndef", []), **(extra or {})}
    _append(root / "outbox" / "nfc" / f"{_day(now)}.jsonl", entry)
    _append(root / "outbox" / "lifeos" / f"{_day(now)}.ndjson", lifeos_event(entry, kind))
    if oled_flash is None:
        from . import oled
        oled_flash = oled.flash
    try:
        what = "NFC wrote" if kind == "nfc_write" else "NFC"
        oled_flash([f"{what} {label or uid[-8:]}", datetime.fromtimestamp(now).strftime("%H:%M:%S")], FLASH_S,
                   root, now=now)
    except OSError:
        pass
    return entry


def recent(root: Path, n: int = 20, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    out: list[dict] = []
    folder = root / "outbox" / "nfc"
    for f in sorted(folder.glob("*.jsonl"), reverse=True) if folder.is_dir() else []:
        try:
            lines = f.read_text().splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                out.append(e)
            if len(out) >= n:
                return list(reversed(out))
    return list(reversed(out))


class Debounce:
    """A tag held on the reader, or tapped twice within a few seconds, logs once."""

    def __init__(self, window: float = DEBOUNCE_S):
        self.window, self.last = window, {}

    def fresh(self, uid: str, now: float) -> bool:
        prev = self.last.get(uid)
        self.last[uid] = now
        return prev is None or now - prev >= self.window


# ---------- one-shot writes: the CLI arms, the service writes on the next tap ----------

def write_path(root: Path) -> Path:
    return root / "state" / "nfc-write.json"


def result_path(root: Path) -> Path:
    return root / "state" / "nfc-write-result.json"


def arm_write(root: Path, kind: str, value: str, label: str = "", now: float | None = None,
              ttl: float = WRITE_TTL_S) -> dict:
    if kind not in ("url", "text"):
        raise ValueError("kind must be url or text")
    if not value or len(value) > MAX_VALUE:
        raise ValueError(f"value must be 1-{MAX_VALUE} characters")
    if kind == "url" and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
        raise ValueError("a url needs a scheme, e.g. https://")
    now = time.time() if now is None else now
    req = {"id": secrets.token_hex(4), "kind": kind, "value": value, "label": label[:40], "armed": now,
           "expires": now + ttl}
    _write_json(write_path(root), req)
    return req


def take_write(root: Path, now: float) -> dict | None:
    p = write_path(root)
    req = _read_json(p)
    if req is None:
        return None
    try:
        p.unlink()
    except OSError:
        pass
    if not isinstance(req, dict) or req.get("expires", 0) < now or req.get("kind") not in ("url", "text"):
        return None
    return req


# ---------- the service's per-tag step ----------

class TagError(Exception):
    pass


def handle(root: Path, tag, now: float, debounce: Debounce, oled_flash: Callable | None = None) -> dict | None:
    """One tag on the reader. `tag` has .info() -> {uid, tag_type, ndef} and .write(kind, value)."""
    info = tag.info()
    uid = norm_uid(info.get("uid", ""))
    if not uid:
        return None
    info["uid"] = uid
    req = take_write(root, now)
    if req:
        result = {"id": req["id"], "uid": uid, "at": now, "ok": False, "error": ""}
        try:
            tag.write(req["kind"], req["value"])
            after = tag.info()
            result["ok"] = any(r.get("value") == req["value"] for r in after.get("ndef", []))
            if not result["ok"]:
                result["error"] = "read back did not match"
            info = {**after, "uid": uid}
        except Exception as exc:  # noqa: BLE001  (not NDEF, locked, too small, lifted mid-write)
            result["error"] = f"{type(exc).__name__}: {exc}"[:200]
        if result["ok"] and req.get("label"):
            set_label(root, uid, req["label"])
        entry = record(root, info, now, "nfc_write",
                       {"wrote": {"kind": req["kind"], "value": req["value"], "ok": result["ok"]}}, oled_flash)
        debounce.fresh(uid, now)
        _write_json(result_path(root), result)
        return entry
    if not debounce.fresh(uid, now):
        return None
    return record(root, info, now, "nfc_tap", None, oled_flash)


class NfcpyTag:
    """Adapter over an nfcpy tag object."""

    def __init__(self, tag, ndef_mod):
        self.tag, self.ndef = tag, ndef_mod

    def info(self) -> dict:
        t = self.tag
        recs = []
        nd = getattr(t, "ndef", None)
        for r in (nd.records if nd is not None else []):
            if hasattr(r, "iri"):
                recs.append({"type": "url", "value": r.iri})
            elif hasattr(r, "text"):
                recs.append({"type": "text", "value": r.text})
            else:
                recs.append({"type": str(getattr(r, "type", "?")), "value": ""})
        return {"uid": t.identifier.hex().upper(), "tag_type": str(getattr(t, "product", "") or t.type), "ndef": recs}

    def write(self, kind: str, value: str) -> None:
        nd = self.tag.ndef
        if nd is None:
            raise TagError("tag is not NDEF formatted")
        if not nd.is_writeable:
            raise TagError("tag is read only")
        nd.records = [self.ndef.UriRecord(value) if kind == "url" else self.ndef.TextRecord(value)]


def run(root: Path = DECK, device: str = READER, clock: Callable[[], float] = time.time) -> int:
    import nfc      # nfcpy: on the Pi only (requirements-sensors.txt)
    import ndef
    status = root / "state" / "nfc-status.json"
    try:
        clf = nfc.ContactlessFrontend(device)
    except Exception as exc:  # noqa: BLE001
        _write_json(status, {"reader": "error", "device": device, "error": str(exc)[:200], "at": clock()})
        log.error("no reader on %s: %s", device, exc)
        return 1
    _write_json(status, {"reader": "ok", "device": device, "found": str(clf), "since": clock()})
    log.info("deck-nfc listening on %s (%s)", device, clf)
    db = Debounce()

    def on_connect(tag):
        try:
            handle(root, NfcpyTag(tag, ndef), clock(), db)
        except Exception as exc:  # noqa: BLE001  (one bad tag must not stop the reader)
            log.warning("tag failed: %s", exc)
        return True                      # wait for the tag to leave before polling again

    try:
        while True:
            if not clf.connect(rdwr={"on-connect": on_connect}):
                time.sleep(0.2)
    finally:
        clf.close()


# ---------- CLI ----------

def _opt(args: list[str], name: str, default):
    if name in args:
        i = args.index(name)
        if i + 1 >= len(args):
            raise SystemExit(f"{name} needs a value")
        val = args[i + 1]
        return args[:i] + args[i + 2:], (type(default)(val) if default is not None else val)
    return args, default


def _fmt(e: dict) -> str:
    ndef = "; ".join(f"{r.get('type')}={r.get('value')}" for r in e.get("ndef") or []) or "no NDEF"
    wrote = e.get("wrote")
    w = f"  WROTE {wrote['kind']}={wrote['value']} {'ok' if wrote.get('ok') else 'FAILED'}" if wrote else ""
    return f"{e.get('at', '')}  {e.get('uid', '')}  {e.get('label') or '-'}  {e.get('tag_type', '')}  {ndef}{w}"


def main(argv: list[str], root: Path = DECK, clock: Callable[[], float] = time.time,
         sleep: Callable[[float], None] = time.sleep) -> int:
    args = list(argv)
    if args == ["run"]:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s deck-nfc %(message)s")
        return run(root)
    if not args or args == ["status"]:
        st = _read_json(root / "state" / "nfc-status.json") or {}
        print(f"reader   {st.get('reader', 'unknown (is deck-nfc.service running?)')} {st.get('error', '')}".rstrip())
        last = recent(root, 1)
        print(f"last tap {_fmt(last[-1]) if last else '-'}")
        req = _read_json(write_path(root))
        if isinstance(req, dict) and req.get("expires", 0) > clock():
            print(f"armed    write {req.get('kind')}={req.get('value')}")
        return 0
    cmd = args[0]
    if cmd == "log":
        rest, n = _opt(args[1:], "-n", 20)
        for e in recent(root, n):
            print(_fmt(e))
        return 0
    if args == ["labels"]:
        for uid, name in sorted(labels(root).items(), key=lambda kv: kv[1]):
            print(f"{uid}  {name}")
        return 0
    if cmd == "label" and len(args) == 3:
        uid = norm_uid(args[1])
        if not uid:
            print("not a tag UID (hex, e.g. 04A1B2C3D4E5F6)", file=sys.stderr)
            return 1
        set_label(root, uid, args[2])
        print(f"{uid}  {args[2] or '(label removed)'}")
        return 0
    if cmd == "read":
        rest, timeout = _opt(args[1:], "--timeout", 30.0)
        start = clock()
        seen = len(recent(root, 1000))
        print("tap a tag...", file=sys.stderr)
        while clock() - start < timeout:
            now_all = recent(root, 1000)
            if len(now_all) > seen and now_all[-1].get("ts", 0) >= start - 1:
                print(_fmt(now_all[-1]))
                return 0
            sleep(0.25)
        print("no tap seen (is deck-nfc.service running?)", file=sys.stderr)
        return 2
    if cmd == "write" and len(args) >= 3 and args[1] in ("url", "text"):
        rest, label = _opt(args[2:], "--label", "")
        rest, timeout = _opt(rest, "--timeout", WRITE_TTL_S)
        try:
            req = arm_write(root, args[1], " ".join(rest), label, clock(), timeout)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"tap the tag to write {args[1]}: {req['value']}", file=sys.stderr)
        while clock() < req["expires"] + 2:
            res = _read_json(result_path(root))
            if isinstance(res, dict) and res.get("id") == req["id"]:
                if res.get("ok"):
                    print(f"wrote and verified on {res['uid']}" + (f", labelled {label}" if label else ""))
                    return 0
                print(f"write failed on {res.get('uid')}: {res.get('error')}", file=sys.stderr)
                return 1
            sleep(0.25)
        try:
            write_path(root).unlink()
        except OSError:
            pass
        print("no tag tapped in time (is deck-nfc.service running?)", file=sys.stderr)
        return 2
    print(__doc__.strip(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

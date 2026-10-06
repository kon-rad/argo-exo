from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    deck_root: Path
    state: Path
    bridge_url: str
    bridge_token: str
    arecord_cmd: list
    min_wav_bytes: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        root = Path(env.get("DECK_ROOT", "/srv/deck"))
        args = shlex.split(env.get("EXO_ARECORD_ARGS", "-f S16_LE -r 16000 -c 1"))
        return cls(
            deck_root=root, state=root / "state",
            bridge_url=env.get("EXO_BRIDGE_URL", ""), bridge_token=env.get("EXO_BRIDGE_TOKEN", ""),
            arecord_cmd=["arecord", "-q", "-D", env.get("EXO_MIC_DEVICE", "default"), *args],
            min_wav_bytes=int(env.get("EXO_MIN_WAV_BYTES", "9600")),
        )

"""The Approve key + Mic chord, as a pure state machine that deck-buttons drives.

  - Approve + Mic held together for 2 s = panic freeze (fires once per hold).
  - A press of Approve approves on RELEASE, and only if Mic was never down during that press. Approving on
    press would send the oldest queued transaction the instant someone starts the panic chord with Approve.
"""
from __future__ import annotations

FREEZE_HOLD_S = 2.0


class ChordState:
    def __init__(self, hold_s: float = FREEZE_HOLD_S):
        self.hold_s = hold_s
        self.key_is_down = False
        self.tainted = False            # Mic was down at some point during this key press
        self.both_since: float | None = None
        self.fired = False

    def key_down(self, now: float, mic: bool) -> None:
        self.key_is_down, self.tainted = True, bool(mic)
        self.poll(now, key=True, mic=mic)

    def key_up(self, now: float, mic: bool) -> bool:
        """True if this release should approve the oldest queued item."""
        if not self.key_is_down:
            return False
        approve = not (self.tainted or mic)
        self.key_is_down, self.tainted = False, False
        self.both_since, self.fired = None, False
        return approve

    def mic_down(self, now: float, key: bool) -> bool:
        """Mic's press callback: a tap shorter than one tick during an Approve press still cancels the approve."""
        if self.key_is_down:
            self.tainted = True
        return self.poll(now, key=key, mic=True)

    def poll(self, now: float, key: bool, mic: bool) -> bool:
        """Call on every tick with both buttons' live state. True exactly once per 2 s two-button hold."""
        if key and mic:
            self.tainted = self.tainted or self.key_is_down
            if self.both_since is None:
                self.both_since, self.fired = now, False
            if not self.fired and now - self.both_since >= self.hold_s:
                self.fired = True
                return True
            return False
        self.both_since, self.fired = None, False
        return False

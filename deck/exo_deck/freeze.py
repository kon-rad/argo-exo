"""hooks/freeze: the panic button (Approve + Mic held 2 s). ExoModule.freeze() straight from the deck's hot key,
then the CRE freeze record through exo-bridge. A cloud outage never blocks the local freeze, and a failed local
freeze still asks the bridge. state/frozen is set as soon as the freeze tx is out (deck-confirm keeps it in
step with the chain afterwards)."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Callable

from eth_utils import keccak

from .exo_module import FROZEN, _bool
from .signer import build_tx, freeze_calldata, key_lock
from .state import _write

log = logging.getLogger("hooks/freeze")
LOCK_WAIT_S = 3.0       # a slow approve hook must not hold up the panic button for long


def _onchain_frozen(rpc, module: str) -> bool | None:
    try:
        return _bool(rpc.call("eth_call", [{"to": module, "data": "0x" + FROZEN.hex()}, "latest"]))
    except Exception:  # noqa: BLE001  unknown: freeze anyway
        return None


def _send_freeze(state: Path, signer, rpc, module: str) -> str:
    with key_lock(state, wait_s=LOCK_WAIT_S):
        raw = bytes(signer.sign(build_tx(rpc, signer.address, module, freeze_calldata())))
        txh = "0x" + keccak(raw).hex()
        rpc.call("eth_sendRawTransaction", ["0x" + raw.hex()])
    return txh


def run_freeze(state: Path, signer, rpc, bridge, module: str, speak: Callable[[str], None]) -> bool:
    """True if ExoModule is (or is now being) frozen from the deck."""
    state = Path(state)
    ok = False
    if _onchain_frozen(rpc, module) is True:
        ok = True
        speak("Already frozen. No transaction can run until the cold key unfreezes.")
    elif signer is None:
        speak("The hot key is locked, so I couldn't freeze from the deck. Use the cold key to freeze.")
    else:
        try:
            txh = _send_freeze(state, signer, rpc, module)
        except Exception as exc:  # noqa: BLE001  RPC text, if any, stays in this log
            log.error("freeze tx failed (%s)", type(exc).__name__)
            speak("The freeze didn't go out from the deck. Use the cold key to freeze now.")
        else:
            ok = True
            _write(state / "frozen", "1\n")
            _write(state / "tx-sent", txh)
            log.info("freeze sent %s", txh)
            speak("Frozen. No transaction can run until the cold key unfreezes.")
    try:
        bridge.freeze("panic")                                  # the CRE record (and a second path to frozen)
    except Exception as exc:  # noqa: BLE001
        log.warning("bridge freeze failed (%s)", type(exc).__name__)
    return ok


def main(argv: list[str]) -> int:
    from exo_nownodes.rpc import Rpc

    from . import tts
    from .bridge_client import Bridge
    from .config import Settings
    from .signer import HotSigner, SignerLocked

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    module = os.environ.get("EXO_MODULE_ADDRESS", "")
    s = Settings.from_env()
    if not module:
        log.error("EXO_MODULE_ADDRESS is not set")
        tts.speak("The freeze isn't configured on this deck. Use the cold key to freeze.")
        return 2
    try:
        signer = HotSigner(Path(os.environ.get("EXO_HOT_KEYSTORE", "/srv/deck/keys/hot.json")),
                           Path(os.environ.get("EXO_HOT_PASSFILE", "/run/exo/hot.pass")))
    except SignerLocked:
        signer = None
    ok = run_freeze(s.state, signer, Rpc("ethereum"), Bridge(s.bridge_url, s.bridge_token), module, tts.speak)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

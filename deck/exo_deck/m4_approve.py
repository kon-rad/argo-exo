"""Interim fund approver on the M4 (the Exo deck's role until the deck is online).

`m4-fund-approve` runs this: show the top open proposal, prepare a fresh quote through the bridge, recompute the Safe
hash and check chain, Safe and destination with the deck code (fund_sign.check_prepared), ask Konrad to type
`yes`, then sign with the keystore and send the signature back. Also `--refuse`. Nothing is signed without the typed yes.
Env: EXO_BRIDGE_URL, EXO_BRIDGE_TOKEN, EXO_FUND_SAFE, EXO_FUND_SAVINGS, EXO_HOT_KEYSTORE, EXO_HOT_PASSFILE."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .bridge_client import Bridge, BridgeError
from .fund_sign import ADDRESS, FundError, check_prepared
from .signer import HotSigner, SignerLocked


def main(argv: list[str], ask=input, out=print) -> int:
    safe = os.environ.get("EXO_FUND_SAFE", "")
    if not ADDRESS.match(safe):
        out("EXO_FUND_SAFE is not set; nothing signed.")
        return 2
    savings = os.environ.get("EXO_FUND_SAVINGS", "")
    savings = savings if ADDRESS.match(savings) else None
    bridge = Bridge(os.environ.get("EXO_BRIDGE_URL", ""), os.environ.get("EXO_BRIDGE_TOKEN", ""))
    try:
        props = [p for p in bridge.fund_proposals() if p.get("status") in ("proposed", "prepared")]
        if not props:
            out("No fund proposals waiting.")
            return 0
        p = props[0]
        out(f"Proposal {p['id']} ({p.get('kind', 'swap')}), committee {p.get('tally', '?')}\n  Motion: {p.get('motion', '')}")
        if "--refuse" in argv:
            if ask("Type 'refuse' to refuse it: ").strip().lower() != "refuse":
                out("Left as it is.")
                return 1
            bridge.fund_refuse(p["id"])
            out("Refused.")
            return 0
        prep = bridge.fund_prepare(p["id"])
        h = check_prepared(prep, safe, savings)
        out(f"  Trade: {prep.get('summary', '?')}\n  Safe hash (checked here): 0x{h.hex()}")
        if ask("Type 'yes' to sign: ").strip().lower() != "yes":
            out("Not signed.")
            return 1
        signer = HotSigner(Path(os.environ["EXO_HOT_KEYSTORE"]), Path(os.environ["EXO_HOT_PASSFILE"]))
        res = bridge.fund_approve(p["id"], "0x" + signer.sign_hash(h).hex())
        out(f"Done. {res.get('headline', 'Trade sent.') if isinstance(res, dict) else 'Trade sent.'}")
        return 0
    except FundError as exc:
        out(f"Refused to sign: {exc}")
    except SignerLocked:
        out("The deck key is locked (passphrase or keystore wrong).")
    except BridgeError as exc:
        out(f"Bridge: {exc}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

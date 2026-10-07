import logging
import os
import sys
import time

import requests
from waitress import serve

from .. import agents_view, approvals, chain_view, media, ring_view, state as st, system_view
from ..bridge_client import Bridge
from ..config import Settings
from .app import create_app


def build_providers(s: Settings) -> dict:
    providers = {"talk": lambda page: {"turns": st.recent_turns(s.state, 7)}}
    providers["approvals"] = lambda page: approvals.panel(s.state, time.time(), page)
    providers["body"] = lambda page: ring_view.panel(s.deck_root / "ring.db")
    providers["sensors"] = lambda page: system_view.panel(s.deck_root)
    providers["media"] = lambda page: media.panel(s.deck_root, s.state, page)
    bridge = Bridge(s.bridge_url, s.bridge_token)
    providers["agents"] = lambda page: agents_view.panel(bridge)
    from exo_nownodes.blockbook import Blockbook    # packages/nownodes-py: on the Pi via PYTHONPATH in the kiosk unit
    balances = chain_view.Balances(lambda chain: Blockbook(chain), chain_view.load_wallets())
    providers["wallets"] = balances.panel
    # 10.3-10.8 add: agents, transactions, wallets, cre, body, sensors, media (add each one's imports here too)
    return providers


def check_listen(listen: str) -> str:
    """Refuse any wildcard bind: the kiosk is for loopback and the tailnet IP only."""
    for addr in listen.split():
        host = addr.rsplit(":", 1)[0].strip("[]")
        if host in ("0.0.0.0", "", "*", "::"):
            raise SystemExit(f"refusing to bind {addr!r}: set EXO_KIOSK_LISTEN to 127.0.0.1 and the tailnet IP")
    return listen


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s exo-kiosk %(message)s")
    s = Settings.from_env()

    def hermes_ok() -> bool:
        if not s.bridge_url:
            return False
        try:
            return requests.get(f"{s.bridge_url}/health", timeout=2).ok
        except requests.RequestException:
            return False

    app = create_app(s, build_providers(s), hermes_ok)
    # add the tailnet IP for the phone: EXO_KIOSK_LISTEN="127.0.0.1:8080 <tailnet-ip>:8080"
    listen = check_listen(os.environ.get("EXO_KIOSK_LISTEN", "127.0.0.1:8080"))
    serve(app, listen=listen, threads=6)


if __name__ == "__main__":
    sys.exit(main())

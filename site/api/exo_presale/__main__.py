import logging
import os

from waitress import serve

from . import config
from .app import create_app
from .chain import Sale
from .claims import ClaimStore
from .rpc import base_rpc

STATE_TIMEOUT_S = 4


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    c = config.load()
    if "EXO_BASE_RPC_URL" in os.environ:
        logging.getLogger("exo-presale").warning("EXO_BASE_RPC_URL set: reading a LOCAL FORK, not Base mainnet")
    sale = Sale(base_rpc(os.environ, usage=True), c["contract"], c["tiers"],
                state_rpc=base_rpc(os.environ, timeout=STATE_TIMEOUT_S, usage=True))
    app = create_app(sale, ClaimStore(c["db"]), c["countries"], c["rate"])
    serve(app, host=c["host"], port=c["port"], threads=4)


if __name__ == "__main__":
    main()

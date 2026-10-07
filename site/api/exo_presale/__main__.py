import logging

from waitress import serve

from exo_nownodes.rpc import Rpc, default_usage

from . import config
from .app import create_app
from .chain import Sale
from .claims import ClaimStore


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    c = config.load()
    sale = Sale(Rpc("base", counter=default_usage()), c["contract"], c["tiers"])
    app = create_app(sale, ClaimStore(c["db"]), c["countries"], c["rate"])
    serve(app, host=c["host"], port=c["port"], threads=4)


if __name__ == "__main__":
    main()

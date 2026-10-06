import logging
import os
import sys

from waitress import serve

from .app import create_app
from .config import Config
from .kanban import Kanban
from .talk import Talker


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        cfg = Config.from_env(os.environ)
    except ValueError as exc:
        print(f"exo-bridge: {exc}", file=sys.stderr)
        return 2
    app = create_app(cfg, Kanban(cfg.hermes_bin, cfg.board), Talker(cfg.api_server_url, cfg.api_server_key))
    serve(app, host=cfg.host, port=cfg.port, threads=4)
    return 0


if __name__ == "__main__":
    sys.exit(main())

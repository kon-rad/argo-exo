import logging
import os
import sys

from waitress import serve

from .app import create_app
from .config import Config
from .guardian_routes import guardian_blueprint
from .kanban import Kanban
from .talk import Talker

log = logging.getLogger("exo-bridge")


def build_guardian(env):
    """The Guardian when EXO_LEDGER_WRITER_DSN is set (chain/runner must be on PYTHONPATH), else None (503s).
    EXO_GUARDIAN_BROADCAST=1 makes guard/freeze real mainnet report transactions; without it every run is a dry
    run and nothing is ever queued for the key."""
    dsn = env.get("EXO_LEDGER_WRITER_DSN", "")
    if not dsn:
        log.info("guardian: EXO_LEDGER_WRITER_DSN unset, Guardian routes answer 503")
        return None
    from exo_guardian.service import Guardian
    from exo_guardian.store import PgStore
    broadcast = env.get("EXO_GUARDIAN_BROADCAST", "") == "1"
    log.info("guardian: ledger configured, %s", "BROADCAST (reports go onchain)" if broadcast else "dry run (nothing is queued)")
    return Guardian(PgStore(dsn), broadcast=broadcast)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        cfg = Config.from_env(os.environ)
    except ValueError as exc:
        print(f"exo-bridge: {exc}", file=sys.stderr)
        return 2
    try:
        guardian = build_guardian(os.environ)
    except (ImportError, ValueError, OSError, KeyError) as exc:
        print(f"exo-bridge: guardian setup failed ({type(exc).__name__}); is chain/runner on PYTHONPATH?", file=sys.stderr)
        return 2
    app = create_app(cfg, Kanban(cfg.hermes_bin, cfg.board), Talker(cfg.api_server_url, cfg.api_server_key),
                     blueprints=(guardian_blueprint(guardian),))
    serve(app, host=cfg.host, port=cfg.port, threads=4)
    return 0


if __name__ == "__main__":
    sys.exit(main())

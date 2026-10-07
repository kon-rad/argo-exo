import logging
import os
import sys
import threading

from waitress import serve

from .app import create_app
from .config import Config
from .guardian_routes import guardian_blueprint
from .kanban import Kanban
from .ledger import Ledger
from .ledger_routes import ledger_blueprint
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
    from exo_guardian.simulate import ensure_wasm
    from exo_guardian.store import PgStore
    broadcast = env.get("EXO_GUARDIAN_BROADCAST", "") == "1"
    log.info("guardian: ledger configured, %s", "BROADCAST (reports go onchain)" if broadcast else "dry run (nothing is queued)")
    guardian = Guardian(PgStore(dsn), broadcast=broadcast)
    # Build the workflow binary now, so the first guard request doesn't pay the ~23 s compile.
    threading.Thread(target=ensure_wasm, name="cre-wasm-warmup", daemon=True).start()
    return guardian


def guardian_setup_error(exc: BaseException) -> str:
    """The startup failure, named precisely: for an import error, the module that is missing (exo_guardian when
    chain/runner isn't on PYTHONPATH; eth_abi, eth_utils or psycopg when the venv lacks a dependency). Other
    errors print their type only (their text may quote the DSN or config)."""
    if isinstance(exc, ImportError):
        missing = getattr(exc, "name", None) or "?"
        hint = "is chain/runner on PYTHONPATH?" if missing.split(".")[0] == "exo_guardian" else \
            "install agents/bridge/requirements.txt into the bridge venv"
        return f"guardian setup failed: {type(exc).__name__}: cannot import {missing} ({hint})"
    return f"guardian setup failed: {type(exc).__name__}"


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
        print(f"exo-bridge: {guardian_setup_error(exc)}", file=sys.stderr)
        return 2
    try:
        ledger = Ledger(cfg.ledger_dsn) if cfg.ledger_dsn else None   # psycopg is imported here: fail at startup, not on first read
    except ImportError:
        print("exo-bridge: EXO_LEDGER_DSN is set but psycopg is not installed", file=sys.stderr)
        return 2
    if ledger is None:
        log.info("ledger: EXO_LEDGER_DSN unset, /ledger/* answer 503")
    app = create_app(cfg, Kanban(cfg.hermes_bin, cfg.board), Talker(cfg.api_server_url, cfg.api_server_key),
                     blueprints=(guardian_blueprint(guardian), ledger_blueprint(ledger, cfg.cre_manifest)))
    serve(app, host=cfg.host, port=cfg.port, threads=4)
    return 0


if __name__ == "__main__":
    sys.exit(main())

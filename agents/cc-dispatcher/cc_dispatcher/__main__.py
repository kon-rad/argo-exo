"""python -m cc_dispatcher [--once] [--config PATH]"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

from .board import Board
from .config import load
from .dispatcher import Dispatcher

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "cc-lanes" / "lanes.toml"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cc-dispatcher")
    ap.add_argument("--config", default=os.environ.get("CC_LANES", str(DEFAULT_CONFIG)))
    ap.add_argument("--once", action="store_true", help="one pass, wait for started workers, then exit")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

    if os.environ.get("ANTHROPIC_API_KEY"):
        # It would outrank the subscription login in every worker. Workers get a clean env anyway; refuse loudly.
        logging.error("ANTHROPIC_API_KEY is set in the dispatcher's environment; unset it (subscription login only)")
        return 2
    settings = load(args.config)
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    boards = {b: Board(settings.cli_board_bin, b) for b in settings.boards}
    d = Dispatcher(settings, boards)
    blocked = d.recover()
    if blocked:
        logging.warning("blocked %d task(s) orphaned by a previous run: %s", len(blocked), ", ".join(blocked))
    logging.info("lanes %s on boards %s, max %d in progress", ", ".join(settings.lanes), ", ".join(settings.boards),
                 settings.max_in_progress)
    if d.org is not None:
        logging.info("org: %d agents from %s, routines in %s", len(d.org.agents), settings.org.agents_file, settings.org.timezone)

    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    d.tick()
    while not stopping and (d.running or not args.once):
        time.sleep(2 if args.once else settings.poll_seconds)
        if args.once:
            d._reap(time.time())
            d._enforce_runtime(time.time())
        else:
            d.tick()
    d.stop_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""guardian-run from the shell, for the Hermes `exo-wallet` skill on the droplet:

    EXO_LEDGER_WRITER_DSN=... [EXO_GUARDIAN_BROADCAST=1] python -m exo_guardian.cli guard '<request json>'

Prints the result JSON. Exit 0: a verdict was recorded (approve or refuse; check `queued`). Exit 1: the Guardian was
unavailable (recorded as a refusal). Exit 2: bad usage or an invalid request (nothing recorded).
"""
import json
import logging
import os
import sys

from .service import Guardian, InvalidRequest
from .store import PgStore

USAGE = "usage: python -m exo_guardian.cli guard '<request json>'"


def main(argv, env=os.environ, make_guardian=None) -> int:
    if len(argv) != 2 or argv[0] != "guard":
        print(USAGE, file=sys.stderr)
        return 2
    try:
        req = json.loads(argv[1])
    except json.JSONDecodeError:
        print("guard: the request is not JSON", file=sys.stderr)
        return 2
    if make_guardian is None:
        dsn = env.get("EXO_LEDGER_WRITER_DSN", "")
        if not dsn:
            print("guard: EXO_LEDGER_WRITER_DSN is not set", file=sys.stderr)
            return 2
        g = Guardian(PgStore(dsn), broadcast=env.get("EXO_GUARDIAN_BROADCAST", "") == "1")
    else:
        g = make_guardian()
    try:
        result = g.guard(req)
    except InvalidRequest as exc:
        print(f"guard: invalid request: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 1 if result.get("unavailable") else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(main(sys.argv[1:]))

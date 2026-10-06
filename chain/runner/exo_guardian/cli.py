"""The Hermes `exo-wallet` skill's entry point to the Guardian, on the droplet:

    EXO_BRIDGE_URL=http://<tailnet ip>:8765 EXO_BRIDGE_TOKEN=... python -m exo_guardian.cli guard '<request json>'

It POSTs the request to exo-bridge's /guard and prints the bridge's JSON. The skill never holds the ledger
credentials or decides broadcast: those live only in the bridge's env, behind its bearer token.

Exit 0: a verdict was recorded (approve or refuse; check `queued`). Exit 1: the Guardian or the bridge was
unavailable. Exit 2: bad usage or an invalid request (nothing recorded).
"""
import json
import os
import sys

import requests

USAGE = "usage: python -m exo_guardian.cli guard '<request json>'"
TIMEOUT_S = 270   # a guard blocks for the whole simulation (up to 240 s)


def main(argv, env=os.environ, post=requests.post) -> int:
    if len(argv) != 2 or argv[0] != "guard":
        print(USAGE, file=sys.stderr)
        return 2
    try:
        req = json.loads(argv[1])
    except json.JSONDecodeError:
        print("guard: the request is not JSON", file=sys.stderr)
        return 2
    if not isinstance(req, dict):
        print("guard: the request must be a JSON object", file=sys.stderr)
        return 2
    url, token = env.get("EXO_BRIDGE_URL", "").rstrip("/"), env.get("EXO_BRIDGE_TOKEN", "")
    if not url or not token:
        print("guard: EXO_BRIDGE_URL and EXO_BRIDGE_TOKEN must be set", file=sys.stderr)
        return 2
    try:
        r = post(f"{url}/guard", json=req, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT_S)
    except requests.RequestException as exc:
        print(f"guard: bridge unreachable ({type(exc).__name__})", file=sys.stderr)
        return 1
    try:
        body = r.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        print(f"guard: bridge answered HTTP {r.status_code} without a JSON object", file=sys.stderr)
        return 1
    print(json.dumps(body))
    if r.status_code == 200:
        return 0
    return 2 if r.status_code == 400 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

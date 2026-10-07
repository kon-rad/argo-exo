#!/usr/bin/env bash
# Writes site/static/preorder.json for the page from what the chain actually reports.
# Usage: EXO_PREORDER=<address> site/contracts/export.sh
#   EXO_PREORDER  the deployed ExoPreorder, or the zero address before a deploy (the site reads zero as "not open")
#   EXO_RPC_URL   optional; defaults to the local NOWNodes proxy (infra/nownodes-proxy) on :8545/base.
#                 Pass any other Base RPC from the environment; none is ever written into this repo.
# Refuses to write unless the RPC is Base mainnet (8453), and, for a non-zero EXO_PREORDER, unless that address
# has code and its usdc() is Base native USDC.
set -euo pipefail
CAST="${CAST:-$(command -v cast || echo "$HOME/.foundry/bin/cast")}"
R="${EXO_RPC_URL:-http://127.0.0.1:8545/base}"
U=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
ZERO=0x0000000000000000000000000000000000000000
OUT="$(cd "$(dirname "$0")/.." && pwd)/static/preorder.json"
P="${EXO_PREORDER:?set EXO_PREORDER (the zero address before a deploy)}"

[[ "$P" =~ ^0x[0-9a-fA-F]{40}$ ]] || { echo "EXO_PREORDER is not an address: $P" >&2; exit 1; }
CHAIN=$("$CAST" chain-id --rpc-url "$R")
[[ "$CHAIN" == 8453 ]] || { echo "RPC reports chain $CHAIN, not Base mainnet 8453" >&2; exit 1; }
if [[ "$P" != "$ZERO" ]]; then
  P=$("$CAST" to-check-sum-address "$P")
  [[ "$("$CAST" code "$P" --rpc-url "$R")" != 0x ]] || { echo "no contract at $P on Base" >&2; exit 1; }
  PU=$("$CAST" to-check-sum-address "$("$CAST" call "$P" 'usdc()(address)' --rpc-url "$R")")
  [[ "$PU" == "$U" ]] || { echo "$P pays in $PU, not Base native USDC" >&2; exit 1; }
fi

TMP=$(mktemp "$OUT.XXXXXX")
trap 'rm -f "$TMP"' EXIT
python3 - "$P" "$U" "$CHAIN" \
  "$("$CAST" call $U 'name()(string)' --rpc-url "$R")" "$("$CAST" call $U 'version()(string)' --rpc-url "$R")" \
  "$("$CAST" call $U 'decimals()(uint8)' --rpc-url "$R")" \
  "$("$CAST" sig 'preorder(uint8,uint256)')" "$("$CAST" sig 'preorderWithPermit(uint8,uint256,uint256,uint8,bytes32,bytes32)')" \
  "$("$CAST" sig 'approve(address,uint256)')" "$("$CAST" sig 'allowance(address,address)')" "$("$CAST" sig 'nonces(address)')" \
  "$("$CAST" keccak 'Preordered(uint256,address,uint8,uint256)')" \
  "$("$CAST" sig 'price(uint8)')" "$("$CAST" sig 'paused()')" "$("$CAST" sig 'totalMinted()')" \
  "$("$CAST" sig 'maxSupply()')" "$("$CAST" sig 'balanceOf(address)')" \
  "$("$CAST" sig 'NotForSale(uint8)')" "$("$CAST" sig 'PriceAboveMax(uint256,uint256)')" "$("$CAST" sig 'SoldOut()')" \
  "$("$CAST" sig 'EnforcedPause()')" > "$TMP" <<'PY'
import json, sys
a = sys.argv[1:]
chain = int(a[2])
print(json.dumps({"chainId": chain, "chainIdHex": hex(chain), "chainName": "Base", "contract": a[0], "usdc": a[1],
  "usdcName": json.loads(a[3]), "usdcVersion": json.loads(a[4]), "usdcDecimals": int(a[5]),
  "selectors": {"preorder": a[6], "preorderWithPermit": a[7], "approve": a[8], "allowance": a[9], "nonces": a[10],
                "price": a[12], "paused": a[13], "totalMinted": a[14], "maxSupply": a[15], "balanceOf": a[16]},
  "errors": {"NotForSale": a[17], "PriceAboveMax": a[18], "SoldOut": a[19], "EnforcedPause": a[20]},
  "preorderedTopic": a[11], "explorer": "https://basescan.org"}, indent=2))
PY
mv "$TMP" "$OUT"
trap - EXIT
cat "$OUT"

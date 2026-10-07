#!/usr/bin/env bash
# Fork rehearsal of the whole pre-order flow against REAL Base native USDC with NO real money.
#
# Starts a local anvil fork of Base mainnet on 127.0.0.1, deploys ExoPreorder with the real deploy script, funds a
# test buyer with forked USDC, buys once with permit + preorderWithPermit and once with approve(exact) + preorder,
# checks the PriceAboveMax and paused reverts, runs the sale API against the fork, saves a signed shipping claim
# per receipt, and checks `exo_presale.admin summary/export`. Everything is killed and deleted on exit.
#
# Every transaction goes to http://127.0.0.1:$FORK_PORT and nowhere else; the script refuses any other RPC.
# Signing keys are anvil's well-known dev accounts, read at runtime from anvil's own --config-out file.
#
# Usage: site/deploy/rehearse-fork.sh            (KEEP=1 keeps the work dir)
#   FORK_URL   upstream Base RPC to fork from (read-only; default the public https://mainnet.base.org)
#   FORK_PORT  anvil port (default 8546)   API_PORT  sale API port (default 5399)
set -euo pipefail

FOUNDRY_BIN="${FOUNDRY_BIN:-$HOME/.foundry/bin}"
ANVIL="$FOUNDRY_BIN/anvil" CAST="$FOUNDRY_BIN/cast" FORGE="$FOUNDRY_BIN/forge"
FORK_URL="${FORK_URL:-https://mainnet.base.org}"
FORK_PORT="${FORK_PORT:-8546}" API_PORT="${API_PORT:-5399}"
R="http://127.0.0.1:$FORK_PORT" API="http://127.0.0.1:$API_PORT"
U=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913          # Base native USDC (the real contract, on the fork)
SITE="$(cd "$(dirname "$0")/.." && pwd)" REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PY:-$REPO/.venv/bin/python}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/exo-rehearse.XXXXXX")"
ANVIL_PID="" API_PID=""

cleanup() {
  [[ -n "$API_PID" ]] && kill "$API_PID" 2>/dev/null || true
  [[ -n "$ANVIL_PID" ]] && kill "$ANVIL_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  if [[ "${KEEP:-}" == 1 ]]; then echo "kept $WORK"; else rm -rf "$WORK"; fi
}
trap cleanup EXIT

step() { printf '\n== %s\n' "$*"; }
ok() { printf '   ok  %s\n' "$*"; }
die() { printf '   FAIL %s\n' "$*" >&2; exit 1; }
expect() { [[ "$1" == "$2" ]] && ok "$3 = $2" || die "$3: expected $2, got $1"; }
send() {  # send <key> <to> <sig> [args...] -> prints tx hash; fails on revert
  local key="$1"; shift
  "$CAST" send --rpc-url "$R" --private-key "$key" --json "$@" | "$PY" -c 'import json,sys; r=json.load(sys.stdin); assert r["status"] in ("0x1", 1), r; print(r["transactionHash"])'
}
send_reverting() {  # like send, but skips gas estimation so the revert is mined; prints status
  local key="$1"; shift
  "$CAST" send --rpc-url "$R" --private-key "$key" --gas-limit 400000 --json "$@" | "$PY" -c 'import json,sys; r=json.load(sys.stdin); print(r["transactionHash"], int(str(r["status"]), 0))'
}
call() { "$CAST" call --rpc-url "$R" "$@" | awk '{print $1}'; }
revert_data() {  # revert_data <from> <to> <sig> [args...] -> the 4-byte selector an eth_call reverts with
  "$PY" - "$R" "$1" "$2" "$("$CAST" calldata "${@:3}")" <<'PY'
import json, sys, urllib.request
url, frm, to, data = sys.argv[1:]
body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"from": frm, "to": to, "data": data}, "latest"]}).encode()
r = json.load(urllib.request.urlopen(urllib.request.Request(url, body, {"Content-Type": "application/json"})))
print((r.get("error", {}).get("data") or "none")[:10])
PY
}

[[ "$R" =~ ^http://127\.0\.0\.1:[0-9]+$ ]] || die "fork RPC must be loopback"
for b in "$ANVIL" "$CAST" "$FORGE" "$PY"; do [[ -x "$b" ]] || die "missing $b"; done

step "start anvil fork of Base on $R"
"$ANVIL" --fork-url "$FORK_URL" --host 127.0.0.1 --port "$FORK_PORT" --chain-id 8453 \
  --config-out "$WORK/anvil.json" --silent > "$WORK/anvil.log" 2>&1 &
ANVIL_PID=$!
for _ in $(seq 60); do "$CAST" chain-id --rpc-url "$R" >/dev/null 2>&1 && break; sleep 0.5; done
expect "$("$CAST" chain-id --rpc-url "$R")" 8453 "fork chain id"
FORK_BLOCK=$("$CAST" block-number --rpc-url "$R"); ok "forked at Base block $FORK_BLOCK"
expect "$("$CAST" call --rpc-url "$R" $U 'name()(string)')" '"USD Coin"' "USDC name"
expect "$("$CAST" call --rpc-url "$R" $U 'version()(string)')" '"2"' "USDC version"

acct() { "$PY" -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d[sys.argv[2]][int(sys.argv[3])])' "$WORK/anvil.json" "$1" "$2"; }
addr() { "$CAST" to-check-sum-address "$(acct available_accounts "$1")"; }
DEPLOYER_KEY=$(acct private_keys 0)
OWNER=$(addr 1) OWNER_KEY=$(acct private_keys 1)
TREASURY=$(addr 2)
DELEGATED=$(addr 3)
# Every anvil dev account carries an EIP-7702 delegation (0xef0100…) on Base mainnet, so USDC's permit checks them
# with EIP-1271 and refuses an ECDSA signature. The buyer and stranger are therefore fresh keys made for this run.
fresh() { "$CAST" wallet new --json | "$PY" -c 'import json,sys; w=json.load(sys.stdin)[0]; print(w["address"], w["private_key"])'; }
read -r BUYER BUYER_KEY < <(fresh)
read -r STRANGER STRANGER_KEY < <(fresh)
BUYER=$("$CAST" to-check-sum-address "$BUYER") STRANGER=$("$CAST" to-check-sum-address "$STRANGER")
for a in "$BUYER" "$STRANGER"; do
  expect "$("$CAST" code "$a" --rpc-url "$R")" 0x "code at fresh key $a"
  "$CAST" rpc --rpc-url "$R" anvil_setBalance "$a" 0x16345785d8a0000 >/dev/null     # 0.1 ETH (fork only) for gas
done
ok "owner $OWNER · treasury $TREASURY (anvil dev accounts) · buyer $BUYER · stranger $STRANGER (fresh keys)"

step "deploy ExoPreorder with script/DeployPreorder.s.sol (broadcast + cache redirected into the work dir)"
(cd "$SITE/contracts" && FOUNDRY_BROADCAST="$WORK/broadcast" FOUNDRY_CACHE_PATH="$WORK/cache" \
  BASE_USDC=$U EXO_TREASURY="$TREASURY" EXO_PRESALE_OWNER="$OWNER" EXO_MAX_SUPPLY=10 EXO_CONFIRM_CHAIN_ID=8453 \
  "$FORGE" script script/DeployPreorder.s.sol --rpc-url "$R" --broadcast --private-key "$DEPLOYER_KEY" > "$WORK/deploy.log" 2>&1) \
  || { cat "$WORK/deploy.log"; die "deploy"; }
read -r P DEPLOY_TX DEPLOY_BLOCK < <("$PY" - "$WORK/broadcast" <<'PY'
import glob, json, sys
f = glob.glob(sys.argv[1] + "/DeployPreorder.s.sol/8453/run-latest.json")[0]
d = json.load(open(f))
print(d["transactions"][0]["contractAddress"], d["receipts"][0]["transactionHash"], int(d["receipts"][0]["blockNumber"], 16))
PY
)
ok "ExoPreorder $P (tx $DEPLOY_TX, block $DEPLOY_BLOCK)"
expect "$("$CAST" to-check-sum-address "$(call "$P" 'usdc()(address)')")" "$U" "usdc()"
expect "$(call "$P" 'owner()(address)')" "$OWNER" "owner()"
expect "$(call "$P" 'price(uint8)(uint256)' 1)" 0 "price(1) after deploy (closed)"

step "fund the buyer with 10 forked USDC (anvil_dealERC20; falls back to the FiatToken balance slot)"
"$CAST" rpc --rpc-url "$R" anvil_dealERC20 $U "$BUYER" "0x$(printf '%x' 10000000)" >/dev/null 2>&1 || {
  SLOT=$("$CAST" index address "$BUYER" 9)                      # FiatTokenV2_2 balanceAndBlacklistStates
  "$CAST" rpc --rpc-url "$R" anvil_setStorageAt $U "$SLOT" "0x$(printf '%064x' 10000000)" >/dev/null; }
expect "$(call $U 'balanceOf(address)(uint256)' "$BUYER")" 10000000 "buyer USDC"

step "owner sets prices: tier 1 = 1.00 USDC, tier 2 = 2.00 USDC"
ok "setPrice(1) tx $(send "$OWNER_KEY" "$P" 'setPrice(uint8,uint256)' 1 1000000)"
ok "setPrice(2) tx $(send "$OWNER_KEY" "$P" 'setPrice(uint8,uint256)' 2 2000000)"

step "route A: EIP-2612 permit (typed data from site/static/wallet.mjs) + preorderWithPermit(1)"
QUOTE=$(call "$P" 'price(uint8)(uint256)' 1)
NONCE=$(call $U 'nonces(address)(uint256)' "$BUYER")
DEADLINE=$(( $("$CAST" block latest -f timestamp --rpc-url "$R") + 1800 ))
node --input-type=module - "$SITE/static/wallet.mjs" "$BUYER" "$P" "$QUOTE" "$NONCE" "$DEADLINE" > "$WORK/permit.json" <<'JS'
const [mod, owner, spender, value, nonce, deadline] = process.argv.slice(2);
const w = await import('file://' + mod);
process.stdout.write(JSON.stringify(w.permitTypedData({ name: 'USD Coin', version: '2', chainId: 8453,
  verifyingContract: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', owner, spender, value, nonce, deadline })));
JS
SIG=$("$CAST" wallet sign --data --from-file "$WORK/permit.json" --private-key "$BUYER_KEY")
read -r V RR SS < <(node --input-type=module - "$SITE/static/wallet.mjs" "$SIG" <<'JS'
const [mod, sig] = process.argv.slice(2);
const { splitSig } = await import('file://' + mod);
const s = splitSig(sig); console.log(s.v, s.r, s.s);
JS
)
TX_A=$(send "$BUYER_KEY" "$P" 'preorderWithPermit(uint8,uint256,uint256,uint8,bytes32,bytes32)' 1 "$QUOTE" "$DEADLINE" "$V" "$RR" "$SS")
ok "preorderWithPermit tx $TX_A"
expect "$(call "$P" 'ownerOf(uint256)(address)' 1)" "$BUYER" "ownerOf(1)"
expect "$(call "$P" 'tierOf(uint256)(uint8)' 1)" 1 "tierOf(1)"
expect "$(call $U 'balanceOf(address)(uint256)' "$TREASURY")" 1000000 "treasury USDC"
expect "$(call $U 'allowance(address,address)(uint256)' "$BUYER" "$P")" 0 "leftover allowance"

step "7702-delegated wallet (anvil #3 has 0xef0100 code on Base): its permit is refused, the purchase reverts unpaid"
expect "$("$CAST" code "$DELEGATED" --rpc-url "$R" | cut -c1-8)" 0xef0100 "code prefix at $DELEGATED"
"$CAST" rpc --rpc-url "$R" anvil_dealERC20 $U "$DELEGATED" "0x$(printf '%x' 5000000)" >/dev/null 2>&1 || {
  "$CAST" rpc --rpc-url "$R" anvil_setStorageAt $U "$("$CAST" index address "$DELEGATED" 9)" "0x$(printf '%064x' 5000000)" >/dev/null; }
node --input-type=module - "$SITE/static/wallet.mjs" "$DELEGATED" "$P" 1000000 "$(call $U 'nonces(address)(uint256)' "$DELEGATED")" "$DEADLINE" > "$WORK/permit-7702.json" <<'JS'
const [mod, owner, spender, value, nonce, deadline] = process.argv.slice(2);
const w = await import('file://' + mod);
process.stdout.write(JSON.stringify(w.permitTypedData({ name: 'USD Coin', version: '2', chainId: 8453,
  verifyingContract: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', owner, spender, value, nonce, deadline })));
JS
SIG7=$("$CAST" wallet sign --data --from-file "$WORK/permit-7702.json" --private-key "$(acct private_keys 3)")
read -r V7 R7 S7 < <(node --input-type=module - "$SITE/static/wallet.mjs" "$SIG7" <<'JS'
const [mod, sig] = process.argv.slice(2);
const { splitSig } = await import('file://' + mod);
const s = splitSig(sig); console.log(s.v, s.r, s.s);
JS
)
OUT=$(revert_data "$DELEGATED" "$P" 'preorderWithPermit(uint8,uint256,uint256,uint8,bytes32,bytes32)' 1 1000000 "$DEADLINE" "$V7" "$R7" "$S7")
[[ "$OUT" != none ]] && ok "preorderWithPermit from a 7702 account reverts ($OUT: USDC allowance error), nothing paid — the page's getCode check sends these wallets down route B" || die "7702 permit unexpectedly succeeded"

step "route B: approve(exact price) + preorder(2)"
QUOTE=$(call "$P" 'price(uint8)(uint256)' 2)
ok "approve tx $(send "$BUYER_KEY" $U 'approve(address,uint256)' "$P" "$QUOTE")"
TX_B=$(send "$BUYER_KEY" "$P" 'preorder(uint8,uint256)' 2 "$QUOTE")
ok "preorder tx $TX_B"
expect "$(call "$P" 'ownerOf(uint256)(address)' 2)" "$BUYER" "ownerOf(2)"
expect "$(call $U 'balanceOf(address)(uint256)' "$TREASURY")" 3000000 "treasury USDC"
expect "$(call $U 'balanceOf(address)(uint256)' "$BUYER")" 7000000 "buyer USDC"
expect "$(call "$P" 'totalMinted()(uint256)')" 2 "totalMinted"

step "PriceAboveMax: buyer quotes 1.00, owner raises tier 1 to 1.50 before the send"
QUOTE=$(call "$P" 'price(uint8)(uint256)' 1)
send "$BUYER_KEY" $U 'approve(address,uint256)' "$P" "$QUOTE" >/dev/null
ok "raise tx $(send "$OWNER_KEY" "$P" 'setPrice(uint8,uint256)' 1 1500000)"
expect "$(revert_data "$BUYER" "$P" 'preorder(uint8,uint256)' 1 "$QUOTE")" "$("$CAST" sig 'PriceAboveMax(uint256,uint256)')" "simulated revert selector"
read -r TX_PAM ST < <(send_reverting "$BUYER_KEY" "$P" 'preorder(uint8,uint256)' 1 "$QUOTE")
expect "$ST" 0 "mined preorder status (tx $TX_PAM)"
expect "$(call $U 'balanceOf(address)(uint256)' "$BUYER")" 7000000 "buyer USDC unchanged"
expect "$(call "$P" 'totalMinted()(uint256)')" 2 "totalMinted unchanged"
send "$OWNER_KEY" "$P" 'setPrice(uint8,uint256)' 1 1000000 >/dev/null
send "$BUYER_KEY" $U 'approve(address,uint256)' "$P" 0 >/dev/null; ok "price restored to 1.00, allowance reset to 0"

step "sale API on $API against the fork (EXO_BASE_RPC_URL override, scratch content with countries [Japan])"
mkdir -p "$WORK/content"; cp "$SITE/content/"*.json "$WORK/content/"
"$PY" - "$WORK/content/copy.json" <<'PY'
import json, sys
p = sys.argv[1]; c = json.load(open(p)); c["terms"]["countries"] = ["Japan"]; json.dump(c, open(p, "w"))
PY
APIENV=(env -i PATH="$PATH" HOME="$WORK" PYTHONPATH="$SITE/api:$REPO/packages/nownodes-py" EXO_BASE_RPC_URL="$R"
  EXO_PREORDER="$P" EXO_PREORDER_FROM_BLOCK="$DEPLOY_BLOCK" EXO_SITE_CONTENT="$WORK/content" EXO_CLAIMS_DB="$WORK/claims.db"
  EXO_PRESALE_PORT="$API_PORT" EXO_NOWNODES_USAGE="$WORK/usage.json")
REFUSE=$(cd "$SITE/api" && perl -e "alarm 15; exec @ARGV" "${APIENV[@]}" EXO_BASE_RPC_URL=https://mainnet.base.org EXO_PRESALE_PORT=$((API_PORT + 1)) "$PY" -m exo_presale 2>&1 >/dev/null || true)
[[ "$REFUSE" == *"must be http://127.0.0.1:<port>"* ]] || die "API did not refuse a non-loopback override: $REFUSE"
ok "API refuses EXO_BASE_RPC_URL=https://mainnet.base.org"
(cd "$SITE/api" && exec "${APIENV[@]}" "$PY" -m exo_presale > "$WORK/api.log" 2>&1) &
API_PID=$!
for _ in $(seq 40); do curl -sf "$API/api/sale" >/dev/null 2>&1 && break; sleep 0.25; done
SALE=$(curl -sf "$API/api/sale"); echo "   /api/sale -> $SALE"
"$PY" - "$SALE" <<'PY' || die "/api/sale state"
import json, sys
s = json.loads(sys.argv[1])
assert s["deployed"] is True and s["open"] is True and s["paused"] is False, s
assert s["minted"] == 2 and s["max_supply"] == 10, s
assert [(t["tier"], t["price_units"], t["price"]) for t in s["tiers"]] == [(1, 1000000, "1.00"), (2, 2000000, "2.00")], s
PY
ok "/api/sale: deployed, open, minted 2 of 10, prices 1.00 / 2.00 read live from the fork"

EMAIL="rehearsal@example.com"  # public-ok (fixture; the fork run never leaves this machine)
claim() {  # claim <key> <device> <name> -> prints HTTP status and body
  local at msg sig
  at=$(date +%s)
  msg=$(cd "$SITE/api" && PYTHONPATH=. "$PY" -c 'import sys; from exo_presale.claims import claim_message, details_hash as h; print(claim_message(int(sys.argv[1]), h(sys.argv[2], sys.argv[3], "Japan"), int(sys.argv[4])), end="")' "$2" "$3" "$EMAIL" "$at")
  sig=$("$CAST" wallet sign --private-key "$1" "$msg")              # personal_sign (EIP-191) over claim_message
  "$PY" -c 'import json,sys; print(json.dumps({"device_number": int(sys.argv[1]), "name": sys.argv[2], "email": sys.argv[5], "country": "Japan", "signed_at": int(sys.argv[3]), "signature": sys.argv[4]}))' "$2" "$3" "$at" "$sig" "$EMAIL" \
    | curl -s -o "$WORK/claim.out" -w '%{http_code}' -H 'Content-Type: application/json' --data-binary @- "$API/api/claims"
  printf ' %s\n' "$(cat "$WORK/claim.out")"
}
step "shipping claims (personal_sign by the holder over claim_message)"
expect "$(claim "$BUYER_KEY" 1 'Rehearsal One')" '200 {"ok":true}' "claim No. 1 by holder"
expect "$(claim "$BUYER_KEY" 2 'Rehearsal Two')" '200 {"ok":true}' "claim No. 2 by holder"
OUT=$(claim "$STRANGER_KEY" 1 'Not The Holder'); [[ "$OUT" == 403* ]] && ok "stranger's claim on No. 1 -> $OUT" || die "stranger claim: $OUT"
grep -q "Rehearsal" "$WORK/api.log" && die "personal data in the API log" || ok "no names in the API log"

ADMIN=(env -i PATH="$PATH" HOME="$WORK" PYTHONPATH="$SITE/api:$REPO/packages/nownodes-py" EXO_BASE_RPC_URL="$R"
  EXO_PREORDER="$P" EXO_PREORDER_FROM_BLOCK="$DEPLOY_BLOCK" EXO_CLAIMS_DB="$WORK/claims.db" EXO_NOWNODES_USAGE="$WORK/usage.json"
  "$PY" -m exo_presale.admin)
step "admin summary / export"
expect "$("${ADMIN[@]}" summary)" "minted 2 · revenue 3.00 USDC · shipping claimed 2 (0 stale)" "summary"
"${ADMIN[@]}" export | tr -d '\r' > "$WORK/orders.csv"; sed 's/^/   | /' "$WORK/orders.csv"
expect "$(grep -c ',ok,Rehearsal' "$WORK/orders.csv")" 2 "export rows with a valid claim"

step "receipt No. 2 is sold on: its old shipping claim must drop out of the export"
ok "transfer tx $(send "$BUYER_KEY" "$P" 'transferFrom(address,address,uint256)' "$BUYER" "$STRANGER" 2)"
expect "$("${ADMIN[@]}" summary)" "minted 2 · revenue 3.00 USDC · shipping claimed 1 (1 stale)" "summary"
"${ADMIN[@]}" export | tr -d '\r' | grep -q '^2,.*,stale-owner,,,$' && ok "export row 2: stale-owner, no name/email/country" || die "stale row"

step "paused: owner pauses; a preorder reverts EnforcedPause; /api/sale shows paused after its 30 s cache"
ok "pause tx $(send "$OWNER_KEY" "$P" 'pause()')"
send "$BUYER_KEY" $U 'approve(address,uint256)' "$P" 1000000 >/dev/null
expect "$(revert_data "$BUYER" "$P" 'preorder(uint8,uint256)' 1 1000000)" "$("$CAST" sig 'EnforcedPause()')" "simulated revert selector"
read -r TX_PAUSED ST < <(send_reverting "$BUYER_KEY" "$P" 'preorder(uint8,uint256)' 1 1000000)
expect "$ST" 0 "mined preorder status (tx $TX_PAUSED)"
expect "$(call $U 'balanceOf(address)(uint256)' "$BUYER")" 7000000 "buyer USDC unchanged"
sleep 31
SALE=$(curl -sf "$API/api/sale"); echo "   /api/sale -> $SALE"
"$PY" -c 'import json,sys; s=json.loads(sys.argv[1]); assert s["paused"] is True and s["open"] is False, s' "$SALE" || die "paused state"
ok "/api/sale: paused true, open false"

step "done: fork rehearsal passed (nothing left running; work dir removed unless KEEP=1)"
echo "   contract $P · deploy $DEPLOY_TX · permit buy $TX_A · approve buy $TX_B · PriceAboveMax $TX_PAM · paused $TX_PAUSED"

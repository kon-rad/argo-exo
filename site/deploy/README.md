# site/deploy

How the pre-sale site gets to the droplet, plus the four go-ahead steps. Each go-ahead is Konrad's call, made at the moment he runs it.

| File | What it is |
|---|---|
| `Caddyfile.exo` | The `exo.myargoquest.com` site block. It is installed as `/etc/caddy/Caddyfile.exo` and pulled in by one `import` line in the main Caddyfile. It sends `/api/*` to `127.0.0.1:5310` and serves `/srv/exo-site/dist` for everything else. `.mjs` files are forced to `text/javascript`. There is no access log. |
| `exo-presale-api.service` | systemd unit for the sale API. It runs as `exosite` on loopback with `ProtectSystem=strict`, and it can write only to `/var/lib/exo-presale`. |
| `install.sh` | Run locally by Konrad. It builds the site, rsyncs the staged tree to `~/exo-site-stage` on `$SITE_HOST`, then runs `remote-install.sh` under `sudo` in a single `ssh -t`. It prints its plan and asks before doing anything. `--dry-run` prints the plan only. It is safe to rerun. |
| `remote-install.sh` | Runs on the droplet as root. It creates the user, the files, the venv, the state dir (700) and the unit. For Caddy it backs up the main Caddyfile, appends the import line once, and runs `caddy validate`, which restores the backup on failure. Then it reloads Caddy and restarts the API. It never overwrites the main Caddyfile. |
| `rehearse-fork.sh` | The full purchase flow on a local anvil fork of Base, using real USDC and no real money. See below. |

The droplet's host name and address are never written into this repo. `install.sh` reads them from `SITE_HOST`, which is your ssh alias for the droplet.

## Before any go-ahead: rehearse on a fork

```bash
site/deploy/rehearse-fork.sh          # about 1 minute; needs foundry (~/.foundry/bin), node, .venv
```

The script does all of the following, and every transaction goes to `127.0.0.1:8546` only:

1. Forks Base from `https://mainnet.base.org`.
2. Deploys with the real `DeployPreorder.s.sol`. Its broadcast and cache files go to a temp dir.
3. Buys once with permit plus `preorderWithPermit`, and once with `approve` of the exact price plus `preorder`.
4. Checks that a 7702-delegated wallet's permit is refused without charging it.
5. Checks the `PriceAboveMax` and `EnforcedPause` reverts.
6. Runs the API against the fork (`EXO_BASE_RPC_URL`, accepted only as `http://127.0.0.1:<port>`).
7. Saves a signed shipping claim for each receipt and confirms a stranger's claim gets a 403.
8. Checks `admin summary` and `admin export`, including after a receipt is sold on.

At the end it kills anvil and the API. Run it again just before go-ahead #1.

## Go-ahead #1: deploy ExoPreorder to Base mainnet

```bash
cd site/contracts
BASE_USDC=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913 EXO_TREASURY=<treasury> EXO_PRESALE_OWNER=<owner> \
EXO_MAX_SUPPLY=<n> EXO_CONFIRM_CHAIN_ID=8453 \
forge script script/DeployPreorder.s.sol --rpc-url http://127.0.0.1:8545/base --broadcast --account <deployer keystore>
#   (or --interactives 1 to paste a key instead of a keystore)
EXO_PREORDER=<address> ./export.sh                    # rewrites site/static/preorder.json from the chain; commit it
cast receipt <deploy tx hash> blockNumber --rpc-url http://127.0.0.1:8545/base   # -> EXO_PREORDER_FROM_BLOCK
```

Next, the owner sets the tier names and the description from their own wallet. Both must be Konrad's own words, and a tier name is 24 bytes at most:

```bash
P=<address>
cast send $P 'setTierName(uint8,string)' 1 '<name>' --rpc-url http://127.0.0.1:8545/base --account <owner keystore>
cast send $P 'setTierName(uint8,string)' 2 '<name>' --rpc-url http://127.0.0.1:8545/base --account <owner keystore>
cast send $P 'setDescription(string)' '<description>' --rpc-url http://127.0.0.1:8545/base --account <owner keystore>
```

Do **not** set any price yet. With no price, the page shows "opening soon". Optionally verify the source with `forge verify-contract`, which needs a Basescan key.

## Go-ahead #2: staging on the droplet

This needs the DNS `A` record `exo.myargoquest.com` pointing at the droplet. Write the env file once, by hand, on the droplet:

```bash
sudo install -d -m 750 /etc/exo-presale
sudo install -m 640 /dev/null /etc/exo-presale/env
sudoedit /etc/exo-presale/env
#   EXO_PREORDER=<address from #1>
#   EXO_PREORDER_FROM_BLOCK=<deploy block>
#   NOWNODES_API_KEY=<key>
```

Then, from this repo:

```bash
SITE_HOST=<ssh alias> site/deploy/install.sh --dry-run    # read the plan
SITE_HOST=<ssh alias> site/deploy/install.sh              # draft build; asks before it starts
```

To check the result:

```bash
curl -s https://exo.myargoquest.com/api/sale                                    # "deployed": true, "open": false
curl -sI https://exo.myargoquest.com/static/wallet.mjs | grep -i content-type   # text/javascript
```

- Load the page and confirm the plates say "opening soon" and the certificate is valid.
- Check `free -m` (the installer prints it).
- If the Caddy reload fails, the installer runs `chown -R caddy:caddy /var/log/caddy` and retries.
- **Rollback:** `sudo systemctl disable --now exo-presale-api`, then delete the `import /etc/caddy/Caddyfile.exo` line (or restore `/etc/caddy/Caddyfile.bak-exo-<ts>`), then `sudo systemctl reload caddy`.

## Go-ahead #3: one real 1 USDC pre-order

This spends real money. Use a tier that isn't on the page, for example 9.

```bash
R=http://127.0.0.1:8545/base U=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913 P=<address>
cast send $P 'setPrice(uint8,uint256)' 9 1000000 --rpc-url $R --account <owner keystore>
cast send $U 'approve(address,uint256)' $P 1000000 --rpc-url $R --account <buyer keystore>
cast send $P 'preorder(uint8,uint256)' 9 1000000 --rpc-url $R --account <buyer keystore>
cast call $P 'ownerOf(uint256)(address)' 1 --rpc-url $R                  # the buyer
cast call $U 'balanceOf(address)(uint256)' <treasury> --rpc-url $R       # +1000000
cast send $P 'setPrice(uint8,uint256)' 9 0 --rpc-url $R --account <owner keystore>
```

- Claim shipping on `https://exo.myargoquest.com/receipt.html?n=1` with the buyer wallet. It must be a plain EOA signer, because the API checks the signature with `ecrecover`.
- On the droplet, run `sudo -u exosite exo-presale-admin summary`. It should show `minted 1 · revenue 1.00 USDC · shipping claimed 1 (0 stale)`.
- Then decide: keep No. 0001 as the founder's receipt, or run `cast send $P 'markRefunded(uint256)' 1 …`. That call burns the receipt and records the refund; the USDC itself goes back by a separate transfer from the treasury.

## Go-ahead #4: open the sale

Do this only after the terms and every `TODO(konrad)` slot are filled. `--release` refuses while any slot is still empty.

```bash
SITE_HOST=<ssh alias> site/deploy/install.sh --release
cast send $P 'setPrice(uint8,uint256)' 1 <units> --rpc-url $R --account <owner keystore>   # whenever prices are decided
```

The page picks up a new price within 30 s.

## Orders

```bash
sudo -u exosite exo-presale-admin summary
(umask 077; sudo -u exosite exo-presale-admin export > ~/orders.csv)   # names and emails: keep the file 600, delete after use
```

The export includes a claim only while its signer still holds the receipt. A receipt that was sold on shows `stale-owner` with its details blank, and a burned one shows `burned`. Cells starting with `= + - @` are quoted.

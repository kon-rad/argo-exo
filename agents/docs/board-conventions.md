# Board conventions

The board is the Second Brain engine's Postgres board, reached with the `cli-board` CLI (the Hermes kanban is retired, 2026-10-27). Reference: the engine's `docs/board-design.md` section 5.

- One board per workstream. `exo` is the wearable; add `argo` and `vault` later.
- Assign by roster name (an engine bot such as `vega` or `rune`, or a `cc-*` lane). Cards for a name nobody claims just sit in `ready`.
- Delegation = a child card (`cli-board create "<title>" --to <agent> --after <parent>`). The child waits on the parent's link.
- Agent-to-agent messages = comments on the shared card (`cli-board comment`).
- Every bridge-created card goes to the roster bot that `BRIDGE_AGENT_MAP` names for the agent the wearer asked for (default `researcher` and `librarian` to `vega`, `builder` to `rune`). The engine has no per-card Telegram subscription, so the bridge pings Telegram itself through `EXO_NOTIFY_CMD` when the card finishes or blocks (no ping when it is unset).
- Money agents (trader, portfolio, wallet) never sign; their output is a Guardian request.
- **The Guardian is not the agents' user.** Every Hermes agent runs as `hermes` with file and terminal tools, so anything `hermes` can read or write is the agents'. `exo-bridge`, guardian-run, `cre` and the NOWNodes proxy run as the system user `exoguard`, from its own checkout `/srv/exo-guard/argo-exo` (exoguard-owned, mode 750, cloned from the public remote), with `bridge.env` and `chain/cre/.env` exoguard-owned, mode 600, in `/srv/exo-guard`. The simulator key in `chain/cre/.env` is Guardian-equivalent: whoever holds it can write approval reports. `hermes` is never in the `exoguard` group. The skills are symlinked from the hermes checkout (`~/argo-exo`), never from exoguard's.
- No profile or skill holds a database DSN. The ledger writer DSN lives only in exoguard's `bridge.env`; skills reach the Guardian via `EXO_BRIDGE_URL` / `EXO_GUARD_TOKEN`. `EXO_GUARD_TOKEN` is a narrow bridge token that opens `POST /guard` only, and with it the bridge accepts only the sources `agent:<profile>`, `camera` and `dashboard` (never `voice`). `EXO_BRIDGE_TOKEN` (freeze, executed, talk, tasks, board) must never go into the Hermes env, because any agent can read that env. The Hermes env gets only `EXO_GUARD_TOKEN`, `EXO_BRIDGE_URL`, `EXO_SAFE`, plus `EXO_AGENT_PROFILE` (per profile, `<profile>/.env`, set by `install-profiles.sh`) and optionally `EXO_ADDRESS_BOOK`: `agents/hermes.env.example`.
- The bridge reaches the board with `cli-board` over HTTPS (`BOARD_URL` and `BOARD_TOKEN` in exoguard's `bridge.env`), so `exoguard` needs no sudo to `hermes` and no database credential. Nothing runs from `exoguard` as `hermes`, and nothing runs the other way.
- `api_server` and `hermes dashboard` stay on `127.0.0.1`; only `exo-bridge` binds the tailnet IP.

## Droplet runbook (Konrad runs these, in order; nothing here has been run)

1. Install Tailscale on the droplet and join the tailnet (`install-bridge.sh` refuses without it).
2. Config (vault `.hermes/server/config.yaml`, symlinked on the droplet): add `platforms.api_server` (`enabled: true`, `extra.host: 127.0.0.1`, `extra.port: 8642`). Confirm the key names with `hermes config --help` / `hermes gateway --help` first.
3. As `hermes`: add `API_SERVER_KEY` (`openssl rand -hex 32`) and `API_SERVER_HOST=127.0.0.1` to `~/.hermes/.env` if absent.
4. Restart the existing gateway once: `hermes gateway restart`. Never `hermes gateway start` a second one.  Each profile has its own gateway (default running, others stopped); never start a profile's gateway.
5. As `hermes`, from the hermes checkout: `bash agents/install-profiles.sh` (existing profiles, including `researcher`, are skipped; `--force` backs up SOUL.md then replaces). It also sets `EXO_AGENT_PROFILE` in each Exo profile's `.env` and creates the skills venv `~/.venvs/exo-skills`.
6. As root: `sudo bash agents/install-bridge.sh`. It creates `exoguard`, `/srv/exo-guard/argo-exo` (cloned from the public remote), the venv, `/srv/exo-guard/config/bridge.env` and `chain/cre/.env` (both 600), (no board wrapper any more). Fill `BOARD_URL`, `BOARD_TOKEN` and `EXO_NOTIFY_CMD` (and `EXO_LEDGER_WRITER_DSN` / `EXO_LEDGER_DSN` once the ledger exists) with `sudoedit`.
7. The sudo commands the script prints, in order: `cli-board` on exoguard's PATH, bun + `cre` as `exoguard` and `cre login`, the secrets, copying `EXO_GUARD_TOKEN` into the Hermes env, then the units (install, enable, check `ss -ltnp`). If the bridge ever ran as `hermes`, delete the old env files in `~hermes/.config/exo` and rotate everything they held.
8. Smoke test over the tailnet: `/health`, `/talk`, `/tasks` with `"agent":"researcher"`, `/board`.

## Claude Code lanes (`cc-*`), added 2026-10-11

- `cc-coder`, `cc-research`, `cc-writer`, `cc-reviewer` are assignee names on the engine board. `cc-dispatcher` (`agents/cc-dispatcher/`, unit `agents/systemd/cc-dispatcher.service`) claims their ready tasks and runs one headless `claude -p` per task on Konrad's subscription login. Lanes, models, tool rules, limits: `agents/cc-lanes/lanes.toml`; role prompts `agents/cc-lanes/<lane>.md`; shared rules `worker-protocol.md`.
- Delegate to them like any agent: `cli-board create "<title>" --to cc-coder --board exo --json`, the bridge's `/tasks` with `"agent": "cc-coder"`, or by voice ("have the coder …", "ask the writer …", "tell the reviewer …"). Claude workers can create child cards for each other.
- Claims: the dispatcher claims with a lease = lane runtime + 15 min (`cli-board claim`) and enforces the runtime itself (SIGINT, then SIGTERM after 30 s). A worker that exits without `finish --done`/`--blocked` gets its task blocked with the tail of its log. Logs: `~hermes/cc-runs/<task>.jsonl` (its path is also commented on the card).
- Stops itself: `~hermes/cc-runs/PAUSE` stops new spawns (it writes the file itself when Claude isn't logged in); a rate-limit retry pauses spawns for an hour; `daily_cap` spawns per day. Delete `PAUSE` to resume.
- Workers get a clean environment (never `~/.hermes/.env`), no `--bare` (it ignores the subscription), no `--dangerously-skip-permissions`; anything that would prompt is denied (`--permission-prompts none`). Denied for every lane: sudo, push, ssh, systemctl, `.env` files, credentials, `/home/trader`, `/srv/exo-guard`.

### Model routing for org bots (`[org.routing]`), added 2026-10-30

The dispatcher can ask the engine's model router which model runs a Claude Code bot's card wake, the same integration the zen-agent dispatcher has (engine `docs/org-routing.md`, "Dispatcher integration"; vault design `Areas/argo/second-brain/agent-company/org-config-and-model-routing-design.md`). Code: `agents/cc-dispatcher/cc_dispatcher/routing.py`; tests `tests/test_routing.py`.

| Key in `lanes.toml` | Default | Meaning |
|---|---|---|
| `[org.routing] mode` | `"off"` | `off`: the bot's own model, the router is not asked. `shadow`: ask, log the decision (`route_decisions`) and the run's outcome (`route_outcomes`), run unchanged. `on`: the decision runs |
| `config` | `<vault>/.agents/org/routing.json` | The compiled catalog (`compile.py routing`); relative paths resolve against `[org] vault`. Re-read on every routed wake |
| `max_tier` | unset | Top of each bot's envelope. Unset: the bot's own tier only, so nothing climbs |
| `timeout_seconds` | `20` | A router slower than this is skipped |

- **Scope:** only card wakes (a ready card claimed for the bot). DMs, mentions and routines keep the bot's model. Work type is the card's `work_type`, else the bot's `work`, else `ops`; the card title is passed as `--text` for risk words.
- **The call:** `cli-org route decide --work <type> --tiers <envelope> --card <id> --config <routing.json> --record --agent <bot> --run <run id> --json [--text <title>]`. `--card` makes earlier failed runs on the card escalate. Exit 0 is a `run`, exit 2 any other decision.
- **`on`:** a `run` sets `--model` (catalog ids `haiku`, `sonnet`, `opus` or a `claude-*` id from an `anthropic` provider) and `--max-turns` (the tier's turns). `park`, `ask-spec`, `wait` and `blocked` start no run: the claimed wakeups are finished with `routed <action>: <reason>` and the card is blocked with that reason (`wait` as transient). The wake spends no cooldown or daily wake.
- **After the run:** `cli-org route outcome <id> done|failed --turns N`. `failed` is a non-zero exit, an `error_*` result (max turns), a runtime-cap stop, or a card left running; the router reads it as a turn-cap miss and starts the next wake on that card a tier up.
- **Never blocks a wake:** a timeout, missing `cli-org`, non-zero exit, bad JSON, missing `routing.json` or a bot model not in the catalog logs a line and runs the bot's own model. A `run` naming a model `claude` cannot run (a provider or Ollama id) is logged as `route skipped, claude cannot run <id>` and the bot's model runs.
- **Fixed bots:** `"routing": "fixed"` (or `{"mode": "fixed"}`) on an agent in `org-agents.json` keeps its model and `[org] max_turns`, and its envelope is its own tier only. A `park` and the other no-run decisions still apply to it.
- **Rollout:** `shadow` first, compare with `cli-org route report`, then `on`. Needs the engine with `cli-org route` (kon-rad/argo PR #90) on the droplet.

### Runbook (Konrad, once)

1. Log in as hermes: `ssh -t do-agent 'sudo -iu hermes claude'`, then `/login` and paste the code from the browser. `/status` must show the claude.ai account, not an API key. Exit.
2. Update the hermes checkout: `sudo -iu hermes git -C ~/argo-exo pull --ff-only`.
3. One smoke card first: `sudo -iu hermes bash -c 'cli-board create "Write one line in Areas/argo/argo-exo/dev-log/README.md saying cc-writer works" --to cc-writer --board exo --json && cd ~/argo-exo/agents/cc-dispatcher && python3 -m cc_dispatcher --once'`. The task should end `done`.
4. Install: `sudo install -m 644 ~hermes/argo-exo/agents/systemd/cc-dispatcher.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now cc-dispatcher`. Watch: `journalctl -u cc-dispatcher -f`.
5. Voice delegation: add the four lanes to `EXO_AGENTS` in `/srv/exo-guard/config/bridge.env` if that line is set (the default already includes them), then update exoguard's checkout and restart `exo-bridge` as in `install-bridge.sh`.

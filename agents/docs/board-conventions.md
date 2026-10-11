# Board conventions

- One board per workstream. `exo` is the wearable; add `argo` and `vault` later.
- Assign by profile name. A profile's description is its job ad: the orchestrator routes by it.
- Delegation = a child task (`kanban create --parent <id> --assignee <profile>`) or `kanban decompose`.
  The parent waits on its children.
- Agent-to-agent messages = comments on the shared task. Files = attachments.
- Vague requests go in with `--triage`; the specifier writes the spec before anyone claims it.
- Every bridge-created task has `--max-runtime 30m` and a Telegram home subscription.
- Money agents (trader, portfolio, wallet) never sign; their output is a Guardian request.
- `kanban.max_in_progress: 1` on a small server: one worker process at a time.
- **The Guardian is not the agents' user.** Every Hermes agent runs as `hermes` with file and terminal tools, so anything `hermes` can read or write is the agents'. `exo-bridge`, guardian-run, `cre` and the NOWNodes proxy run as the system user `exoguard`, from its own checkout `/srv/exo-guard/argo-exo` (exoguard-owned, mode 750, cloned from the public remote), with `bridge.env` and `chain/cre/.env` exoguard-owned, mode 600, in `/srv/exo-guard`. The simulator key in `chain/cre/.env` is Guardian-equivalent: whoever holds it can write approval reports. `hermes` is never in the `exoguard` group. The skills are symlinked from the hermes checkout (`~/argo-exo`), never from exoguard's.
- No profile or skill holds a database DSN. The ledger writer DSN lives only in exoguard's `bridge.env`; skills reach the Guardian via `EXO_BRIDGE_URL` / `EXO_GUARD_TOKEN`. `EXO_GUARD_TOKEN` is a narrow bridge token that opens `POST /guard` only, and with it the bridge accepts only the sources `agent:<profile>`, `camera` and `dashboard` (never `voice`). `EXO_BRIDGE_TOKEN` (freeze, executed, talk, tasks, board) must never go into the Hermes env, because any agent can read that env. The Hermes env gets only `EXO_GUARD_TOKEN`, `EXO_BRIDGE_URL`, `EXO_SAFE`, plus `EXO_AGENT_PROFILE` (per profile, `<profile>/.env`, set by `install-profiles.sh`) and optionally `EXO_ADDRESS_BOOK`: `agents/hermes.env.example`.
- The bridge reaches the kanban board through `/usr/local/bin/exo-hermes`, which runs `hermes kanban …` as `hermes` under one sudoers rule (`/etc/sudoers.d/exo-bridge`). That is the only thing `exoguard` may run as `hermes`, and nothing runs the other way.
- `api_server` and `hermes dashboard` stay on `127.0.0.1`; only `exo-bridge` binds the tailnet IP.

## Droplet runbook (Konrad runs these, in order; nothing here has been run)

1. Install Tailscale on the droplet and join the tailnet (`install-bridge.sh` refuses without it).
2. Config (vault `.hermes/server/config.yaml`, symlinked on the droplet): add `platforms.api_server` (`enabled: true`, `extra.host: 127.0.0.1`, `extra.port: 8642`) and `kanban.max_in_progress: 1`. Confirm the key names with `hermes config --help` / `hermes gateway --help` first.
3. As `hermes`: add `API_SERVER_KEY` (`openssl rand -hex 32`) and `API_SERVER_HOST=127.0.0.1` to `~/.hermes/.env` if absent.
4. Restart the existing gateway once: `hermes gateway restart`. Never `hermes gateway start` a second one.  Each profile has its own gateway (default running, others stopped); never start a profile's gateway.
5. As `hermes`, from the hermes checkout: `bash agents/install-profiles.sh` (existing profiles, including `researcher`, are skipped; `--force` backs up SOUL.md then replaces). It also sets `EXO_AGENT_PROFILE` in each Exo profile's `.env` and creates the skills venv `~/.venvs/exo-skills`.
6. As root: `sudo bash agents/install-bridge.sh`. It creates `exoguard`, `/srv/exo-guard/argo-exo` (cloned from the public remote), the venv, `/srv/exo-guard/config/bridge.env` and `chain/cre/.env` (both 600), and the kanban wrapper. Fill `EXO_TELEGRAM_CHAT_ID` (and `EXO_LEDGER_WRITER_DSN` / `EXO_LEDGER_DSN` once the ledger exists) with `sudoedit`.
7. The sudo commands the script prints, in order: the sudoers rule, bun + `cre` as `exoguard` and `cre login`, the secrets, copying `EXO_GUARD_TOKEN` into the Hermes env, then the units (install, enable, check `ss -ltnp`). If the bridge ever ran as `hermes`, delete the old env files in `~hermes/.config/exo` and rotate everything they held.
8. Smoke test over the tailnet: `/health`, `/talk`, `/tasks` with `"agent":"researcher"`, `/board`.

## Claude Code lanes (`cc-*`), added 2026-10-11

- `cc-coder`, `cc-research`, `cc-writer`, `cc-reviewer` are assignees, not Hermes profiles, so the Hermes dispatcher skips them (`skipped_nonspawnable`). `cc-dispatcher` (`agents/cc-dispatcher/`, unit `agents/systemd/cc-dispatcher.service`) claims their ready tasks and runs one headless `claude -p` per task on Konrad's subscription login. Lanes, models, tool rules, limits: `agents/cc-lanes/lanes.toml`; role prompts `agents/cc-lanes/<lane>.md`; shared rules `worker-protocol.md`.
- Delegate to them like any agent: `hermes kanban --board exo create --assignee cc-coder --json -- "<title>"`, the bridge's `/tasks` with `"agent": "cc-coder"`, or by voice ("have the coder …", "ask the writer …", "tell the reviewer …"). Claude workers can create child tasks for the Hermes agents and the reverse.
- Claims: the dispatcher claims with `--ttl` = lane runtime + 15 min and enforces the runtime itself (SIGINT, then SIGTERM after 30 s). A worker that exits without `complete`/`block` gets its task blocked with the tail of its log. Logs: `~hermes/cc-runs/<task>.jsonl` (also attached to the task).
- Stops itself: `~hermes/cc-runs/PAUSE` stops new spawns (it writes the file itself when Claude isn't logged in); a rate-limit retry pauses spawns for an hour; `daily_cap` spawns per day. Delete `PAUSE` to resume.
- Workers get a clean environment (never `~/.hermes/.env`), no `--bare` (it ignores the subscription), no `--dangerously-skip-permissions`; anything that would prompt is denied (`--permission-prompts none`). Denied for every lane: sudo, push, ssh, systemctl, `.env` files, credentials, `/home/trader`, `/srv/exo-guard`.

### Runbook (Konrad, once)

1. Log in as hermes: `ssh -t do-agent 'sudo -iu hermes claude'`, then `/login` and paste the code from the browser. `/status` must show the claude.ai account, not an API key. Exit.
2. Update the hermes checkout: `sudo -iu hermes git -C ~/argo-exo pull --ff-only`.
3. One smoke task first: `sudo -iu hermes bash -c 'hermes kanban --board exo create --assignee cc-writer --json -- "Write one line in Areas/argo/argo-exo/dev-log/README.md saying cc-writer works" && cd ~/argo-exo/agents/cc-dispatcher && python3 -m cc_dispatcher --once'`. The task should end `done`.
4. Install: `sudo install -m 644 ~hermes/argo-exo/agents/systemd/cc-dispatcher.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now cc-dispatcher`. Watch: `journalctl -u cc-dispatcher -f`.
5. Voice delegation: add the four lanes to `EXO_AGENTS` in `/srv/exo-guard/config/bridge.env` if that line is set (the default already includes them), then update exoguard's checkout and restart `exo-bridge` as in `install-bridge.sh`.

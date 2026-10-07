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
- No profile or skill holds a database DSN. The ledger writer DSN lives only in the bridge env file; skills reach the Guardian via `EXO_BRIDGE_URL` / `EXO_GUARD_TOKEN`. `EXO_GUARD_TOKEN` is a narrow bridge token that opens `POST /guard` only; `EXO_BRIDGE_TOKEN` (freeze, executed, talk, tasks, board) must never go into the Hermes env, because any agent can read that env. Only `EXO_GUARD_TOKEN`, `EXO_BRIDGE_URL` and `EXO_SAFE` do.
- `api_server` and `hermes dashboard` stay on `127.0.0.1`; only `exo-bridge` binds the tailnet IP.

## Droplet runbook (Konrad runs these, in order; nothing here has been run)

1. Install Tailscale on the droplet and join the tailnet (`install-bridge.sh` refuses without it).
2. Config (vault `.hermes/server/config.yaml`, symlinked on the droplet): add `platforms.api_server` (`enabled: true`, `extra.host: 127.0.0.1`, `extra.port: 8642`) and `kanban.max_in_progress: 1`. Confirm the key names with `hermes config --help` / `hermes gateway --help` first.
3. As `hermes`: add `API_SERVER_KEY` (`openssl rand -hex 32`) and `API_SERVER_HOST=127.0.0.1` to `~/.hermes/.env` if absent.
4. Restart the existing gateway once: `hermes gateway restart`. Never `hermes gateway start` a second one.  Each profile has its own gateway (default running, others stopped); never start a profile's gateway.
5. `bash agents/install-profiles.sh` (existing profiles, including `researcher`, are skipped; `--force` backs up SOUL.md then replaces).
6. `bash agents/install-bridge.sh`, fill `EXO_TELEGRAM_CHAT_ID` (and `EXO_LEDGER_WRITER_DSN` once the ledger exists) in `~/.config/exo/bridge.env`.
7. The sudo commands the script prints (install unit, enable, check `ss -ltnp`).
8. Smoke test over the tailnet: `/health`, `/talk`, `/tasks` with `"agent":"researcher"`, `/board`.

#!/usr/bin/env python3
"""Bot tools: the narrow, checked commands that give the org's bots git, deploy and M1 powers.

One file, three commands, picked by the name it is called as (install.sh links them into /usr/local/bin):

  cli-git     branches, commits, pushes, PRs and reviews for bots with `code`, `review` or `merge`; new private
              repos in the bots' own GitHub org for bots with `code` (there is no delete, here or in the token)
  cli-deploy  named deploy targets for bots with `deploy`
  m1-job      allowlisted jobs on the home M1 Max for bots with `m1`

Identity is ORG_AGENT (the dispatcher sets it); capabilities come from the database through `cli-org whoami --json`,
never from a file a bot can edit. Merging and deploying also need `cli-org ship check` to pass: the owner's approval,
or a card the owner made --ship-now. The registries live in /etc/secondbrain (root-owned): bot-repos.toml and
deploy-targets.toml. The GitHub token is read from a file the bots' tool rules deny, and is passed to git and gh only
through the environment of the child process.

Design: vault Areas/argo/second-brain/agent-company/bot-powers-and-shared-memory-design.md.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

CARD = re.compile(r"^t_[0-9a-f]{6,32}$")
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
# What must not reach a public repo beyond what gitleaks finds: our hosts, people, paths and chat ids. A line ending
# in `public-ok` is a reviewed exception. Each entry: (label, pattern).
PUBLIC_LINE_RULES = [
    ("ipv4", re.compile(r"(?<![\d.])(?!127\.|0\.0\.0\.0|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.|255\.)(\d{1,3}\.){3}\d{1,3}(?![\d.])")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@(?!(?:[a-z0-9-]+\.)*(?:example\.(?:com|org|net)|noreply\.github\.com|anthropic\.com|bots\.argo\.local)\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("telegram chat id", re.compile(r"(?<!\d)-100\d{9,}(?!\d)")),
    ("private path", re.compile(r"/home/(?:hermes|konrad|trader)\b|/Users/konrad|/etc/secondbrain|/srv/exo-|\.config/bot-github")),
    ("private vault folder", re.compile(r"(?<![\w/])(?:CURRENT/|Areas/Finance\b)")),
]
PUBLIC_FILE_RULE = re.compile(r"(?:^|/)(?:\.env(?:\.[\w-]+)?|id_(?:rsa|ed25519|ecdsa)|[^/]+\.(?:pem|key|p12|keystore))$")
PR_URL = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/(\d+)$")


class BotError(Exception):
    pass


# -- environment ---------------------------------------------------------------------------------------------------
def conf_dir() -> Path:
    return Path(os.environ.get("BOT_TOOLS_ETC", "/etc/secondbrain"))


def load_toml(name: str) -> dict:
    p = conf_dir() / name
    try:
        return tomllib.loads(p.read_text())
    except FileNotFoundError:
        raise BotError(f"{p} is not installed; ask the owner (agents/bot-tools/install.sh)")


def binary(var: str, default: str) -> str:
    return os.environ.get(var, default)


def run(cmd: list[str], cwd: str | None = None, env: dict | None = None, timeout: int = 600, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise BotError(f"{Path(cmd[0]).name} {' '.join(cmd[1:3])}: exit {p.returncode}: {(p.stderr or p.stdout).strip()[-800:]}")
    return p


def me() -> dict:
    """The caller from the database: name, team, capabilities. Refuses without ORG_AGENT."""
    name = os.environ.get("ORG_AGENT", "").strip()
    if not NAME.match(name):
        raise BotError("ORG_AGENT is not set; bot tools run inside a bot's wake")
    out = run([binary("CLI_ORG", "cli-org"), "whoami", "--json"]).stdout
    agent = json.loads(out)["agent"]
    agent["capabilities"] = agent.get("capabilities") or []
    return agent


def need(agent: dict, *caps: str) -> None:
    if not any(c in agent["capabilities"] for c in caps):
        raise BotError(f"{agent['name']} lacks the {' or '.join(caps)} capability (has: {', '.join(agent['capabilities']) or 'none'}); "
                       "capabilities come from .agents/org.yaml, imported by the owner")


def ship_check(card: str, *, pr: str | None = None, deploy: str | None = None) -> str:
    cmd = [binary("CLI_ORG", "cli-org"), "ship", "check", card]
    if pr:
        cmd += ["--pr", pr]
    if deploy:
        cmd += ["--deploy", deploy]
    p = run(cmd, check=False)
    if p.returncode != 0:
        raise BotError(f"not approved: {(p.stdout or p.stderr).strip()}. Ask with `cli-org ship request {card} \"<summary>\" --pr <url>`"
                       f"{' --deploy ' + deploy if deploy else ''}, block the card, and stop; the answer wakes you")
    return p.stdout.strip()


def card_comment(card: str, text: str, author: str) -> None:
    run([binary("HERMES_BIN", "hermes"), "kanban", "--board", os.environ.get("HERMES_KANBAN_BOARD", "exo"), "comment",
         "--author", author, card, text[:4000]], check=False)


def check_card(card: str) -> str:
    if not CARD.match(card):
        raise BotError(f"bad card id {card!r}")
    return card


# -- git -------------------------------------------------------------------------------------------------------------
def github_env() -> dict:
    """The child's environment with the token as an HTTP header (git) and GH_TOKEN (gh); nothing is written to disk."""
    conf = load_toml("bot-repos.toml")
    token_file = Path(conf.get("token_file", "/home/hermes/.config/bot-github/token"))
    try:
        token = token_file.read_text().strip()
    except FileNotFoundError:
        raise BotError(f"no GitHub token at {token_file}; the owner creates it (the bot account's classic token: repo, read:org, never delete_repo)")
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TZ", "USER")}
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env.update({"GH_TOKEN": token, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader", "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"})
    return env


def created_file() -> Path:
    return work_root() / "created-repos.json"


def created_repos() -> dict:
    """Repos the bots made with `cli-git repo create`. The file is the bots' own (hermes-writable), so only entries
    under the configured org count; what the token can reach is set on GitHub, not here."""
    owner = load_toml("bot-repos.toml").get("create", {}).get("owner", "")
    try:
        data = json.loads(created_file().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {n: r for n, r in data.items() if owner and NAME.match(n) and r.get("github") == f"{owner}/{n}"}


def repos() -> dict:
    return {**created_repos(), **load_toml("bot-repos.toml").get("repos", {})}


def repo_conf(name: str) -> dict:
    r = repos().get(name)
    if not r:
        raise BotError(f"repo {name!r} is not one the bots work in ({', '.join(sorted(repos())) or 'none installed'})")
    return r


def work_root() -> Path:
    return Path(load_toml("bot-repos.toml").get("work_dir", "/home/hermes/cc-work/bots"))


def branch_for(agent: str, card: str) -> str:
    return f"bot/{agent}/{card}"


def find_worktree(card: str) -> tuple[str, Path]:
    for name in repos():
        p = work_root() / "wt" / f"{name}-{card}"
        if p.exists():
            return name, p
    raise BotError(f"no worktree for {card}; start one with `cli-git start <repo> {card}`")


def git(args: list[str], cwd: Path | str, env: dict | None = None, timeout: int = 600) -> str:
    return run([binary("GIT_BIN", "git"), *args], cwd=str(cwd), env=env, timeout=timeout).stdout


def cmd_start(agent: dict, repo: str, card: str) -> str:
    need(agent, "code")
    r = repo_conf(repo)
    check_card(card)
    env = github_env()
    base = work_root() / "base" / repo
    wt = work_root() / "wt" / f"{repo}-{card}"
    branch = branch_for(agent["name"], card)
    default = r.get("default_branch", "main")
    if not base.exists():
        base.parent.mkdir(parents=True, exist_ok=True)
        git(["clone", "--quiet", r.get("clone_url", f"https://github.com/{r['github']}.git"), str(base)], cwd=base.parent, env=env, timeout=900)
    git(["fetch", "--quiet", "origin", default], cwd=base, env=env)
    if wt.exists():
        return f"{wt} (branch {branch}, already started)"
    git(["worktree", "add", "--quiet", "-b", branch, str(wt), f"origin/{default}"], cwd=base, env=env)
    card_comment(card, f"{agent['name']} started branch {branch} in {r['github']} ({wt})", agent["name"])
    return f"{wt} (branch {branch} off origin/{default}); edit files there, then cli-git test|commit|push|pr create {card}"


def own_worktree(agent: dict, card: str) -> tuple[str, Path, dict]:
    check_card(card)
    repo, wt = find_worktree(card)
    head = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=wt).strip()
    if head != branch_for(agent["name"], card):
        raise BotError(f"{wt} is on {head}, not {branch_for(agent['name'], card)}; only the bot that started it works there")
    return repo, wt, repo_conf(repo)


def cmd_status(agent: dict, card: str, diff: bool = False) -> str:
    need(agent, "code", "review")
    _, wt = find_worktree(check_card(card))
    return git(["diff", "HEAD"] if diff else ["status", "--short", "--branch"], cwd=wt)[:20000] or "clean"


def cmd_test(agent: dict, card: str) -> str:
    need(agent, "code")
    repo, wt, r = own_worktree(agent, card)
    test = r.get("test")
    if not test and r.get("created_by"):
        if not (wt / "scripts" / "test.sh").exists():
            raise BotError(f"{repo} is a bot-made repo: its tests are scripts/test.sh, which doesn't exist yet; add it")
        test = ["bash", "scripts/test.sh"]
    if not test:
        raise BotError(f"repo {repo} has no test command in bot-repos.toml")
    p = run(test, cwd=str(wt), check=False, timeout=int(r.get("test_timeout_seconds", 900)))
    tail = (p.stdout + p.stderr)[-3000:]
    return f"exit {p.returncode}\n{tail}"


def cmd_commit(agent: dict, card: str, message: str) -> str:
    need(agent, "code")
    _, wt, _ = own_worktree(agent, card)
    if not message.strip():
        raise BotError("a commit needs a message")
    git(["add", "-A"], cwd=wt)
    if not git(["status", "--porcelain"], cwd=wt).strip():
        return "nothing to commit"
    who = agent["name"]
    git(["-c", f"user.name={who} (Argo bot)", "-c", f"user.email={who}@bots.argo.local", "commit", "--quiet", "-m",
         f"{message.strip()}\n\nCard: {card}\nBot: {who}"], cwd=wt)
    return git(["log", "-1", "--oneline"], cwd=wt).strip()


def cmd_uncommit(agent: dict, card: str) -> str:
    """Folds the commits not yet pushed back into the working files (git reset --soft), so a bad one can be redone.
    Pushed commits stay: rewriting them would need a force push, which bots don't have."""
    need(agent, "code")
    _, wt, r = own_worktree(agent, card)
    branch = branch_for(agent["name"], card)
    git(["fetch", "--quiet", "origin"], cwd=wt, env=github_env())
    remote = f"origin/{branch}"
    has_remote = run([binary("GIT_BIN", "git"), "rev-parse", "--verify", "--quiet", remote], cwd=str(wt), check=False).returncode == 0
    base = remote if has_remote else f"origin/{r.get('default_branch', 'main')}"
    n = git(["rev-list", "--count", f"{base}..HEAD"], cwd=wt).strip()
    git(["reset", "--soft", base], cwd=wt)
    return f"{n} unpushed commit(s) undone; the changes are still in {wt}. Fix them, then `cli-git commit {card} \"<msg>\"`"


def cmd_push(agent: dict, card: str) -> str:
    need(agent, "code")
    _, wt, r = own_worktree(agent, card)
    branch = branch_for(agent["name"], card)
    if branch in r.get("protected", ["main"]) or not branch.startswith("bot/"):
        raise BotError(f"{branch} is protected")
    if is_public(r):
        public_check(wt, r)
    if r.get("pre_push"):   # a repo's own extra check (argo-lifeos: check-public.sh with its private denylist)
        p = run(r["pre_push"], cwd=str(wt), check=False, timeout=300)
        if p.returncode != 0:
            raise BotError(f"not pushed: `{shlex.join(r['pre_push'])}` failed (exit {p.returncode}):\n{(p.stdout + p.stderr)[-2000:]}")
    git(["push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"], cwd=wt, env=github_env())
    return f"pushed {branch}"


def is_public(r: dict) -> bool:
    """Asked of GitHub at push time, so a bot repo the owner later makes public is covered. Unknown counts as public."""
    try:
        return json.loads(gh(["api", f"repos/{r['github']}", "--jq", "{private: .private}"])).get("private") is not True
    except (BotError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return True


def scan_lines(lines: list[tuple[str, str]]) -> list[str]:
    """(where, text) pairs → findings. Findings name the rule, never echo the matched value."""
    hits = []
    for where, text in lines:
        if text.rstrip().endswith("public-ok"):
            continue
        for label, rx in PUBLIC_LINE_RULES:
            if rx.search(text):
                hits.append(f"{where}: {label}")
    return hits


def public_check(wt: Path, r: dict) -> None:
    """Every commit the push would publish: gitleaks for secrets, then our own rules on each added line and file name.
    Fails closed: no gitleaks on a public repo means no push."""
    rng = f"origin/{r.get('default_branch', 'main')}..HEAD"
    leaks = run([binary("GITLEAKS_BIN", "gitleaks"), "git", "--no-banner", "--redact", "--log-opts", rng, "."], cwd=str(wt), check=False, timeout=300) \
        if shutil.which(binary("GITLEAKS_BIN", "gitleaks")) else None
    if leaks is None:
        raise BotError("not pushed: gitleaks is not installed, and this repo is public (owner: rerun install.sh)")
    if leaks.returncode != 0:
        raise BotError(f"not pushed: gitleaks found a secret in {rng}:\n{(leaks.stdout + leaks.stderr)[-1500:]}")
    patch = git(["log", "-p", "--format=commit %h", "--no-color", rng], cwd=wt)
    lines, files, commit, path = [], [], "?", "?"
    for line in patch.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1]
        elif line.startswith("+++ b/"):
            path = line[6:]
            files.append((commit, path))
        elif line.startswith("+") and not line.startswith("+++"):
            lines.append((f"{commit} {path}", line[1:]))
    hits = [f"{c} {f}: file that shouldn't be public" for c, f in files if PUBLIC_FILE_RULE.search(f)] + scan_lines(lines)
    if hits:
        raise BotError("not pushed: this repo is public and the branch adds what shouldn't be:\n" + "\n".join(hits[:40])
                       + f"\nA fix in a new commit won't do (the old commit still ships): `cli-git uncommit <card>`, fix the files, "
                       "commit again. A reviewed, harmless line can end with `public-ok`")


def gh(args: list[str], cwd: Path | str | None = None, timeout: int = 300) -> str:
    return run([binary("GH_BIN", "gh"), *args], cwd=str(cwd) if cwd else None, env=github_env(), timeout=timeout).stdout


def cmd_pr_create(agent: dict, card: str, title: str, body: str) -> str:
    need(agent, "code")
    _, wt, r = own_worktree(agent, card)
    branch = branch_for(agent["name"], card)
    if is_public(r):
        hits = scan_lines([("title", title)] + [("body", l) for l in body.splitlines()])
        if hits:
            raise BotError("this repo is public; the PR text has: " + ", ".join(hits))
    out = gh(["pr", "create", "--repo", r["github"], "--head", branch, "--base", r.get("default_branch", "main"),
              "--title", title.strip()[:200], "--body", f"{body.strip()}\n\nCard: {card}\nBot: {agent['name']}"], cwd=wt).strip()
    url = out.splitlines()[-1] if out else ""
    card_comment(card, f"PR by {agent['name']}: {url}", agent["name"])
    return url


def pr_parts(url: str) -> tuple[str, str]:
    m = PR_URL.match(url)
    if not m:
        raise BotError(f"{url!r} is not a GitHub PR URL")
    full = m.group(1)
    if full not in {r["github"] for r in repos().values()}:
        raise BotError(f"{full} is not a repo the bots work in")
    return full, m.group(2)


def pr_info(url: str) -> dict:
    full, num = pr_parts(url)
    return json.loads(gh(["pr", "view", num, "--repo", full, "--json", "headRefName,body,state,title,mergeable"]))


def cmd_pr_view(agent: dict, url: str, diff: bool) -> str:
    need(agent, "code", "review", "merge")
    full, num = pr_parts(url)
    return gh(["pr", "diff" if diff else "view", num, "--repo", full])[:30000]


def cmd_pr_review(agent: dict, url: str, verdict: str, text: str) -> str:
    """One GitHub account backs every bot, and GitHub refuses to approve your own PR, so a review is a comment
    whose first line carries the bot and its verdict."""
    need(agent, "review")
    info = pr_info(url)
    if info["headRefName"].startswith(f"bot/{agent['name']}/"):
        raise BotError("a bot doesn't review its own PR")
    label = {"approve": "Approve", "request-changes": "Changes requested", "comment": "Comment"}[verdict]
    full, num = pr_parts(url)
    gh(["pr", "review", num, "--repo", full, "--comment", "--body", f"**{agent['name']}: {label}**\n\n{text.strip()}"])
    return f"{label.lower()} posted on {url}"


def cmd_pr_merge(agent: dict, url: str, card: str) -> str:
    need(agent, "merge")
    check_card(card)
    info = pr_info(url)
    if f"Card: {card}" not in (info.get("body") or ""):
        raise BotError(f"{url} is not card {card}'s PR (its body has no 'Card: {card}')")
    if info.get("state") != "OPEN":
        raise BotError(f"{url} is {info.get('state')}")
    why = ship_check(card, pr=url)
    full, num = pr_parts(url)
    gh(["pr", "merge", num, "--repo", full, "--squash", "--delete-branch"])
    card_comment(card, f"{agent['name']} merged {url} ({why})", agent["name"])
    return f"merged {url} ({why})"


def cmd_repo_create(agent: dict, name: str, card: str, description: str) -> str:
    """A new private repo in the bots' org, with a README on main. Public is the owner's call (he flips it on GitHub);
    deleting is nobody's here: no verb, no delete_repo scope, and the org lets only owners delete."""
    need(agent, "code")
    check_card(card)
    conf = load_toml("bot-repos.toml").get("create", {})
    owner = conf.get("owner", "")
    if not owner:
        raise BotError("repo creation is off: bot-repos.toml has no [create] owner (the owner sets up the bots' GitHub org)")
    if not NAME.match(name):
        raise BotError(f"bad repo name {name!r}: lowercase letters, digits and dashes")
    if name in repos():
        raise BotError(f"{name} already exists ({repos()[name]['github']}); `cli-git start {name} {card}`")
    made = json.loads(created_file().read_text()) if created_file().exists() else {}
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if sum(1 for r in made.values() if r.get("created", "").startswith(today)) >= int(conf.get("per_day", 3)):
        raise BotError(f"the bots already made {conf.get('per_day', 3)} repos today; ask the owner")
    full = f"{owner}/{name}"
    gh(["repo", "create", full, "--private", "--add-readme",
        "--description", (description.strip() or f"Made by {agent['name']} for {card}")[:300]])
    made[name] = {"github": full, "default_branch": "main", "protected": ["main"], "created_by": agent["name"],
                  "card": card, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    created_file().parent.mkdir(parents=True, exist_ok=True)
    tmp = created_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(made, indent=2))
    tmp.replace(created_file())
    card_comment(card, f"{agent['name']} created private repo https://github.com/{full}", agent["name"])
    return (f"created https://github.com/{full} (private, README on main); now `cli-git start {name} {card}`. "
            "Its tests are scripts/test.sh; add one in your first PR")


# -- deploy ----------------------------------------------------------------------------------------------------------
def targets() -> dict:
    return load_toml("deploy-targets.toml").get("targets", {})


def cmd_deploy_list(agent: dict) -> str:
    need(agent, "deploy")
    return "\n".join(f"{n}  {t.get('description', '')}" for n, t in sorted(targets().items())) or "no targets installed"


def cmd_deploy(agent: dict, target: str, card: str) -> str:
    need(agent, "deploy")
    check_card(card)
    t = targets().get(target)
    if not t:
        raise BotError(f"no deploy target {target!r} (cli-deploy list)")
    why = ship_check(card, deploy=target)
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TZ", "USER")}
    env.update({k: str(v) for k, v in t.get("env", {}).items()})
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    log = Path(os.environ.get("BOT_TOOLS_LOGS", "/home/hermes/cc-runs")) / f"deploy-{target}-{stamp}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    timeout = int(t.get("timeout_minutes", 20)) * 60
    with open(log, "w") as f:
        for step in [*t.get("pre", []), t["cmd"]]:
            f.write(f"$ {shlex.join(step)}\n")
            f.flush()
            p = subprocess.run(step, cwd=t.get("cwd"), env=env, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
            if p.returncode != 0:
                card_comment(card, f"deploy {target} by {agent['name']} FAILED at `{shlex.join(step)}` (exit {p.returncode}); log {log}", agent["name"])
                raise BotError(f"deploy {target} failed at {shlex.join(step)} (exit {p.returncode}); log {log}")
        check = t.get("check")
        ok = True
        if check:
            c = subprocess.run(check, cwd=t.get("cwd"), env=env, stdout=f, stderr=subprocess.STDOUT, timeout=120)
            ok = c.returncode == 0
    result = f"deploy {target} by {agent['name']}: {'live, check passed' if ok else 'ran, but the check FAILED'} ({why}); log {log}"
    card_comment(card, result, agent["name"])
    if not ok:
        raise BotError(result)
    return result


# -- M1 jobs ---------------------------------------------------------------------------------------------------------
def m1(args: list[str], timeout: int = 60) -> str:
    """The droplet's key for the M1 can only run m1-jobd (a forced command), so the arguments travel as the command."""
    host = os.environ.get("M1_JOBS_HOST", "konrad@m1max")
    key = os.environ.get("M1_JOBS_KEY", "/home/hermes/.ssh/m1_jobs")
    p = run([binary("SSH_BIN", "ssh"), "-i", key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             host, shlex.join(args)], timeout=timeout, check=False)
    if p.returncode != 0:
        raise BotError(f"m1: {(p.stderr or p.stdout).strip()[-600:] or f'exit {p.returncode}'} (is the M1 awake and on the tailnet?)")
    return p.stdout.strip()


def cmd_m1(agent: dict, argv: list[str]) -> str:
    need(agent, "m1")
    if not argv or argv[0] not in ("list", "run", "status", "log"):
        raise BotError("m1-job list | run <job> --card t_.. [-- args] | status <id> | log <id>")
    if argv[0] == "run":
        if len(argv) < 4 or argv[2] != "--card":
            raise BotError("m1-job run <job> --card t_.. [-- args...]")
        job, card = argv[1], check_card(argv[3])
        rest = argv[5:] if len(argv) > 4 and argv[4] == "--" else argv[4:]
        out = m1(["run", job, card, agent["name"], *rest])
        return (f"{out}\nNow `hermes kanban block {card} \"waiting on m1 job\"` and stop: the M1 comments the result on the card "
                "and unblocks it when the job ends, which wakes you.")
    return m1(argv)


# -- entry -----------------------------------------------------------------------------------------------------------
def opt(argv: list[str], flag: str) -> str | None:
    if flag in argv:
        i = argv.index(flag)
        if i + 1 >= len(argv):
            raise BotError(f"{flag} needs a value")
        return argv[i + 1]
    return None


GIT_USAGE = """cli-git start <repo> <card>            worktree on bot/<you>/<card> off the default branch (code)
cli-git status|diff <card>             in the card's worktree (code, review)
cli-git test <card>                    the repo's test command in your worktree (code)
cli-git commit <card> "<message>"      add everything, commit as you (code)
cli-git push <card>                    push bot/<you>/<card>; never the default branch, never --force; a public repo's
                                       push is scanned first (gitleaks + our hosts, emails, paths, chat ids) (code)
cli-git uncommit <card>                undo your unpushed commits, keeping the changes, to redo a bad one (code)
cli-git pr create <card> "<title>" --body "<what, why, tests run>"   (code)
cli-git pr view|diff <url>             (code, review, merge)
cli-git pr review <url> --approve|--request-changes|--comment "<text>"   not your own PR (review)
cli-git pr merge <url> --card <card>   only with the owner's approval or a ship-now card (merge)
cli-git repo create <name> --card <card> [--description "..."]   new private repo in the bots' org (code)
cli-git repos                          the repos bots work in (yours are marked)"""


def main_git(argv: list[str]) -> str:
    if not argv or argv[0] in ("-h", "--help", "help"):
        return GIT_USAGE
    if argv[0] == "repos":
        return "\n".join(f"{n}  {r['github']}  default {r.get('default_branch', 'main')}"
                         + (f"  (made by {r['created_by']} for {r['card']})" if r.get("created_by") else "")
                         for n, r in sorted(repos().items()))
    agent = me()
    v = argv[0]
    if v == "start" and len(argv) == 3:
        return cmd_start(agent, argv[1], argv[2])
    if v in ("status", "diff") and len(argv) == 2:
        return cmd_status(agent, argv[1], diff=v == "diff")
    if v == "test" and len(argv) == 2:
        return cmd_test(agent, argv[1])
    if v == "commit" and len(argv) == 3:
        return cmd_commit(agent, argv[1], argv[2])
    if v == "uncommit" and len(argv) == 2:
        return cmd_uncommit(agent, argv[1])
    if v == "push" and len(argv) == 2:
        return cmd_push(agent, argv[1])
    if v == "repo" and len(argv) >= 3 and argv[1] == "create":
        card = opt(argv, "--card")
        if not card:
            raise BotError("repo create <name> --card <card> [--description \"...\"]")
        return cmd_repo_create(agent, argv[2], card, opt(argv, "--description") or "")
    if v == "pr" and len(argv) >= 3:
        sub = argv[1]
        if sub == "create" and len(argv) >= 4:
            return cmd_pr_create(agent, argv[2], argv[3], opt(argv, "--body") or "")
        if sub in ("view", "diff"):
            return cmd_pr_view(agent, argv[2], diff=sub == "diff")
        if sub == "review":
            for flag, verdict in (("--approve", "approve"), ("--request-changes", "request-changes"), ("--comment", "comment")):
                text = opt(argv, flag)
                if text is not None:
                    return cmd_pr_review(agent, argv[2], verdict, text)
            raise BotError("pr review <url> --approve|--request-changes|--comment \"<text>\"")
        if sub == "merge":
            card = opt(argv, "--card")
            if not card:
                raise BotError("pr merge <url> --card <card>")
            return cmd_pr_merge(agent, argv[2], card)
    raise BotError(GIT_USAGE)


def main_deploy(argv: list[str]) -> str:
    if not argv or argv[0] in ("-h", "--help", "help"):
        return "cli-deploy list | cli-deploy <target> --card <card>   (deploy; needs the owner's approval or a ship-now card)"
    agent = me()
    if argv[0] == "list":
        return cmd_deploy_list(agent)
    card = opt(argv, "--card")
    if not card:
        raise BotError("cli-deploy <target> --card <card>")
    return cmd_deploy(agent, argv[0], card)


def main(argv: list[str]) -> int:
    name = Path(argv[0]).name
    try:
        if name == "cli-git":
            out = main_git(argv[1:])
        elif name == "cli-deploy":
            out = main_deploy(argv[1:])
        elif name == "m1-job":
            out = cmd_m1(me(), argv[1:])
        else:
            raise BotError("call me as cli-git, cli-deploy or m1-job")
    except BotError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired as exc:
        print(f"timed out: {exc}", file=sys.stderr)
        return 124
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

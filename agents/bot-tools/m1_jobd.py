#!/opt/homebrew/bin/python3
"""m1-jobd: the only thing the droplet's bots can run on the M1 Max.

The droplet's `hermes` key sits in the M1's ~/.ssh/authorized_keys as

  command="/Users/konrad/.local/bin/m1-jobd",restrict ssh-ed25519 AAAA... hermes@droplet m1-jobs

so whatever the bot asks for arrives as SSH_ORIGINAL_COMMAND and this script decides. Verbs:

  list                                  the jobs in ~/.config/m1-jobs/jobs.toml
  run <job> <card> <bot> [args...]      start a job detached; prints its id
  test <repo> <card> <bot> <sha>        stdin is a tar.gz of a bot's worktree; runs that repo's [tests.<repo>] command
                                        detached, sandboxed, one at a time; prints its id
  status <id> | log <id>                where a job is, the tail of its log

The allowlist lives outside the vault on purpose (bots can edit the vault on the droplet and it syncs here). Path
arguments must resolve under the job's roots; flags must be in the job's `flags`. When a job ends, its result goes to
the card as a comment and the card is unblocked with `kb` (the M1's own route to the droplet's kanban), which wakes the
bot that asked. Design: vault Areas/argo/second-brain/agent-company/bot-powers-and-shared-memory-design.md section 5.

Tests run bot-written code as konrad, so they run under sandbox-exec (TEST_PROFILE): no reads of ~/.ssh, ~/.config,
the vault, the keychain and the other private folders, writes only to the test's own folder, the package caches and
temp. Design: vault Areas/argo/second-brain/agent-company/m1-coding-base-design.md, "Step 1: tests on the M1".
"""
from __future__ import annotations

import json
import os
import re
import fcntl
import io
import shlex
import shutil
import subprocess
import sys
import time
import tarfile
import tomllib
from pathlib import Path

HOME = Path.home()
JOBS_FILE = Path(os.environ.get("M1_JOBS_FILE", HOME / ".config" / "m1-jobs" / "jobs.toml"))
STATE = Path(os.environ.get("M1_JOBS_STATE", HOME / "m1-jobs"))
TESTS_DIR = Path(os.environ.get("M1_TESTS_DIR", HOME / "bot-tests"))   # outside STATE: the sandbox hides ~/m1-jobs
MAX_TREE = 300 * 1024 * 1024
SANDBOX = os.environ.get("M1_SANDBOX", "/usr/bin/sandbox-exec")
KB = os.environ.get("M1_JOBS_KB", str(HOME / "Documents" / "secondbrain" / ".hermes" / "scripts" / "kb"))
CARD = re.compile(r"^t_[0-9a-f]{6,32}$")
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
JOB_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]{1,40}$")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.,:+-]{1,80}$")
REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$")
SHA = re.compile(r"^[0-9a-f]{7,40}$")
PRIVATE = [".ssh", ".config", ".claude", ".hermes", ".aws", ".gnupg", ".local", "Documents", "Desktop", "Downloads",
           "Movies", "Pictures", "m1-jobs", "Library/Keychains", "Library/Application Support", "Library/Messages",
           "Library/Mail"]
WRITABLE = [".npm", ".bun", ".cache", "Library/Caches"]
TEST_PROFILE = """(version 1)
(allow default)
(deny file-read* file-write* %s)
(deny mach-lookup (global-name "com.apple.SecurityServer") (global-name "com.apple.securityd"))
(deny file-write* (require-not (require-any (subpath (param "SRC")) %s
  (subpath "/private/tmp") (subpath "/private/var/folders") (subpath "/dev"))))
""" % (" ".join(f'(subpath (string-append (param "HOME") "/{d}"))' for d in PRIVATE),
       " ".join(f'(subpath (string-append (param "HOME") "/{d}"))' for d in WRITABLE))


class JobError(Exception):
    pass


def allowlist() -> dict:
    try:
        return tomllib.loads(JOBS_FILE.read_text())
    except FileNotFoundError:
        raise JobError(f"no allowlist at {JOBS_FILE}")


def jobs() -> dict:
    return allowlist().get("jobs", {})


def expand(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve()


def check_args(job: dict, args: list[str]) -> list[str]:
    roots = [expand(r) for r in job.get("roots", [])]
    flags = set(job.get("flags", []))
    path_flags = set(job.get("path_flags", []))

    def inside(a: str, must_exist: bool) -> str:
        p = expand(a)
        if not any(p == r or r in p.parents for r in roots):
            raise JobError(f"{a} is outside this job's folders ({', '.join(str(r) for r in roots)})")
        if must_exist and not p.exists():
            raise JobError(f"{a} does not exist on the M1")
        return str(p)

    out: list[str] = []
    for a in args:
        if a.startswith("--"):
            name, eq, value = a.partition("=")
            if name in path_flags and value:
                out.append(f"{name}={inside(value, must_exist=False)}")   # an output folder may not exist yet
                continue
            if name not in flags:
                raise JobError(f"flag {name} is not allowed for this job (allowed: {', '.join(sorted(flags | path_flags)) or 'none'})")
            if value and not SAFE_VALUE.match(value):
                raise JobError(f"bad value for {name}")
            out.append(a)
            continue
        out.append(inside(a, must_exist=True))
    return out


def job_dir(job_id: str) -> Path:
    if not JOB_ID.match(job_id):
        raise JobError(f"bad job id {job_id!r}")
    return STATE / job_id


def cmd_list() -> str:
    lines = []
    for name, j in sorted(jobs().items()):
        script = expand(j["cmd"][-1]) if j.get("cmd") else None
        ready = "" if script is None or not str(j["cmd"][-1]).startswith(("~", "/")) or script.exists() else "  (NOT INSTALLED)"
        lines.append(f"{name}  {j.get('description', '')}  args: {j.get('usage', 'paths')}{ready}")
    for repo in sorted(allowlist().get("tests", {})):
        lines.append(f"tests: {repo}  (cli-git test)")
    return "\n".join(lines) or "no jobs"


def cmd_run(name: str, card: str, bot: str, args: list[str]) -> str:
    j = jobs().get(name)
    if not j:
        raise JobError(f"no job {name!r} (m1-job list)")
    if not CARD.match(card) or not NAME.match(bot):
        raise JobError("run <job> <card> <bot> [args]")
    argv = [os.path.expanduser(c) for c in j["cmd"]] + check_args(j, args)
    job_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
    spawn(job_id, {"job": name, "card": card, "bot": bot, "argv": argv, "timeout_minutes": j.get("timeout_minutes", 60),
                   "cwd": os.path.expanduser(j.get("cwd", "~"))})
    return f"job {job_id} started on the M1 for {card}"


def cmd_test(repo: str, card: str, bot: str, sha: str, tree: bytes) -> str:
    """A bot's worktree arrives as a tar.gz on stdin (the M1 has no GitHub token); its tests run from this allowlist,
    never from a command the droplet sends."""
    t = allowlist().get("tests", {}).get(repo)
    if not REPO.match(repo) or not t:
        raise JobError(f"no tests for {repo!r} on the M1 (m1-jobd list)")
    if not CARD.match(card) or not NAME.match(bot) or not SHA.match(sha):
        raise JobError("test <repo> <card> <bot> <sha>, with the worktree as a tar.gz on stdin")
    if not tree or len(tree) > MAX_TREE:
        raise JobError(f"the worktree must arrive on stdin, under {MAX_TREE >> 20} MB (got {len(tree) >> 20} MB)")
    job_id = f"{time.strftime('%Y%m%d-%H%M%S')}-test-" + re.sub(r"[^a-z0-9-]", "-", repo.lower())[:30]
    src = TESTS_DIR / job_id / "src"
    src.mkdir(parents=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(tree), mode="r:gz") as tar:
            tar.extractall(src, filter="data")   # no absolute paths, no links out, no devices
    except (tarfile.TarError, OSError) as exc:
        shutil.rmtree(src.parent, ignore_errors=True)
        raise JobError(f"bad worktree archive: {exc}")
    src = src.resolve()
    sandbox = [SANDBOX, "-p", TEST_PROFILE, "-D", f"HOME={HOME.resolve()}", "-D", f"SRC={src}"]
    spawn(job_id, {"job": f"tests for {repo} at {sha[:9]}", "kind": "test", "card": card, "bot": bot,
                   "argv": ["/usr/bin/nice", "-n", "10", *sandbox, *t["cmd"]], "cwd": str(src), "cleanup": str(src.parent),
                   "output": str(src.parent / "output"),
                   "timeout_minutes": int(t.get("timeout_minutes", 30))})
    return f"job {job_id} started on the M1 for {card}: {repo} tests at {sha[:9]}"


def spawn(job_id: str, spec: dict) -> None:
    d = STATE / job_id
    d.mkdir(parents=True, exist_ok=False)
    (d / "job.json").write_text(json.dumps({**spec, "started": time.time()}))
    child_env = {k: v for k, v in os.environ.items() if not k.startswith("SSH_")}   # `_exec` is refused over ssh
    with open(d / "log", "ab") as log:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "_exec", job_id], stdin=subprocess.DEVNULL, stdout=log,
                         stderr=subprocess.STDOUT, start_new_session=True, close_fds=True, env=child_env)


def board_door() -> tuple[str, str] | None:
    """BOARD_URL (it already ends in /api/board, as cli-board expects) and BOARD_TOKEN: the environment, else the M1's own vault .env."""
    url, token = os.environ.get("BOARD_URL", ""), os.environ.get("BOARD_TOKEN", "")
    if not (url and token):
        env_file = Path(os.environ.get("M1_JOBS_VAULT_ENV", HOME / "Documents" / "secondbrain" / ".env"))
        try:
            for line in env_file.read_text().splitlines():
                k, _, v = line.partition("=")
                if k.strip() == "BOARD_URL" and not url:
                    url = v.strip().strip("\"'")
                if k.strip() == "BOARD_TOKEN" and not token:
                    token = v.strip().strip("\"'")
        except OSError:
            pass
    return (url.rstrip("/"), token) if url and token else None


def board_post(door: tuple[str, str], path: str, body: dict) -> bool:
    import urllib.request
    req = urllib.request.Request(f"{door[0]}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {door[1]}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def report(card: str, text: str) -> None:
    door = board_door()
    if door:
        # The engine board: comment the result, then put the card back in `ready` so its bot wakes and reads it.
        board_post(door, f"/cards/{card}/comment", {"body": text[:3800]})
        board_post(door, f"/cards/{card}/move", {"status": "ready", "note": "m1 job finished"})
        return
    subprocess.run([KB, "comment", card, text[:3800]], timeout=60, capture_output=True)
    subprocess.run([KB, "unblock", card], timeout=60, capture_output=True)


def cmd_exec(job_id: str) -> int:
    """The detached half: runs the job, writes the result, tells the card."""
    d = job_dir(job_id)
    spec = json.loads((d / "job.json").read_text())
    env = dict(os.environ)
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    lock = None
    if spec.get("kind") == "test":
        env.update(CI="1", NO_COLOR="1", FORCE_COLOR="0")   # run once instead of watching; plain text for the card
        lock = open(STATE / "test.lock", "a")
        fcntl.flock(lock, fcntl.LOCK_EX)   # one bot test at a time; the timeout starts once it has the M1
    # A sandboxed test can't see ~/m1-jobs, and node aborts at start when it can't fstat its own stdout (2026-10-22),
    # so a test writes to a file in its own folder, copied into the log when it ends.
    out_path = Path(spec.get("output") or d / "log")
    start = time.time()
    try:
        with open(out_path, "ab") as log:
            p = subprocess.run(spec["argv"], cwd=spec["cwd"], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                               timeout=int(spec["timeout_minutes"]) * 60)
        code = p.returncode
    except subprocess.TimeoutExpired:
        code = 124
    except OSError as exc:
        (d / "log").open("a").write(f"\ncould not start: {exc}\n")
        code = 127
    finally:
        if lock:
            lock.close()
        if out_path != d / "log" and out_path.exists():
            with open(d / "log", "ab") as log:
                log.write(out_path.read_bytes())
        if spec.get("cleanup"):
            shutil.rmtree(spec["cleanup"], ignore_errors=True)   # node_modules are big; the log stays in STATE
    tail = (d / "log").read_text(errors="replace")[-1500:]
    result = {"exit": code, "seconds": round(time.time() - start), "ended": time.time()}
    (d / "result.json").write_text(json.dumps(result))
    if spec.get("kind") == "test":
        verdict = "PASSED" if code == 0 else ("timed out" if code == 124 else f"FAILED (exit {code})")
    else:
        verdict = "finished" if code == 0 else ("timed out" if code == 124 else f"FAILED (exit {code})")
    report(spec["card"], f"M1 job {job_id} ({spec['job']}) {verdict} in {result['seconds']} s. Log on the M1: {d / 'log'}\n\n{tail}")
    return code


def cmd_status(job_id: str, tail: bool) -> str:
    d = job_dir(job_id)
    if not d.exists():
        raise JobError(f"no job {job_id}")
    if tail:
        live = Path(json.loads((d / "job.json").read_text()).get("output") or d / "log")
        return (live if live.exists() else d / "log").read_text(errors="replace")[-4000:]
    r = d / "result.json"
    return f"{job_id}: " + (f"done, {r.read_text()}" if r.exists() else "running")


USAGE = "m1-jobd list | run <job> <card> <bot> [args] | test <repo> <card> <bot> <sha> < tree.tgz | status <id> | log <id>"


def main() -> int:
    raw = os.environ.get("SSH_ORIGINAL_COMMAND")
    argv = shlex.split(raw) if raw is not None else sys.argv[1:]
    try:
        if not argv:
            raise JobError(USAGE)
        v = argv[0]
        if v == "_exec" and raw is None and len(argv) == 2:
            return cmd_exec(argv[1])
        if v == "list":
            print(cmd_list())
        elif v == "run" and len(argv) >= 4:
            print(cmd_run(argv[1], argv[2], argv[3], argv[4:]))
        elif v == "test" and len(argv) == 5:
            print(cmd_test(argv[1], argv[2], argv[3], argv[4], sys.stdin.buffer.read(MAX_TREE + 1)))
        elif v in ("status", "log") and len(argv) == 2:
            print(cmd_status(argv[1], tail=v == "log"))
        else:
            raise JobError(USAGE)
    except JobError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

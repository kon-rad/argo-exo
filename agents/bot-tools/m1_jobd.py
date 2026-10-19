#!/opt/homebrew/bin/python3
"""m1-jobd: the only thing the droplet's bots can run on the M1 Max.

The droplet's `hermes` key sits in the M1's ~/.ssh/authorized_keys as

  command="/Users/konrad/.local/bin/m1-jobd",restrict ssh-ed25519 AAAA... hermes@droplet m1-jobs

so whatever the bot asks for arrives as SSH_ORIGINAL_COMMAND and this script decides. Verbs:

  list                                  the jobs in ~/.config/m1-jobs/jobs.toml
  run <job> <card> <bot> [args...]      start a job detached; prints its id
  status <id> | log <id>                where a job is, the tail of its log

The allowlist lives outside the vault on purpose (bots can edit the vault on the droplet and it syncs here). Path
arguments must resolve under the job's roots; flags must be in the job's `flags`. When a job ends, its result goes to
the card as a comment and the card is unblocked with `kb` (the M1's own route to the droplet's kanban), which wakes the
bot that asked. Design: vault Areas/argo/second-brain/agent-company/bot-powers-and-shared-memory-design.md section 5.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HOME = Path.home()
JOBS_FILE = Path(os.environ.get("M1_JOBS_FILE", HOME / ".config" / "m1-jobs" / "jobs.toml"))
STATE = Path(os.environ.get("M1_JOBS_STATE", HOME / "m1-jobs"))
KB = os.environ.get("M1_JOBS_KB", str(HOME / "Documents" / "secondbrain" / ".hermes" / "scripts" / "kb"))
CARD = re.compile(r"^t_[0-9a-f]{6,32}$")
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
JOB_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]{1,40}$")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.,:+-]{1,80}$")


class JobError(Exception):
    pass


def jobs() -> dict:
    try:
        return tomllib.loads(JOBS_FILE.read_text()).get("jobs", {})
    except FileNotFoundError:
        raise JobError(f"no allowlist at {JOBS_FILE}")


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
    return "\n".join(lines) or "no jobs"


def cmd_run(name: str, card: str, bot: str, args: list[str]) -> str:
    j = jobs().get(name)
    if not j:
        raise JobError(f"no job {name!r} (m1-job list)")
    if not CARD.match(card) or not NAME.match(bot):
        raise JobError("run <job> <card> <bot> [args]")
    argv = [os.path.expanduser(c) for c in j["cmd"]] + check_args(j, args)
    job_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
    d = STATE / job_id
    d.mkdir(parents=True, exist_ok=False)
    (d / "job.json").write_text(json.dumps({"job": name, "card": card, "bot": bot, "argv": argv, "started": time.time(),
                                            "timeout_minutes": j.get("timeout_minutes", 60), "cwd": os.path.expanduser(j.get("cwd", "~"))}))
    child_env = {k: v for k, v in os.environ.items() if not k.startswith("SSH_")}   # `_exec` is refused over ssh
    with open(d / "log", "ab") as log:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "_exec", job_id], stdin=subprocess.DEVNULL, stdout=log,
                         stderr=subprocess.STDOUT, start_new_session=True, close_fds=True, env=child_env)
    return f"job {job_id} started on the M1 for {card}"


def report(card: str, text: str) -> None:
    subprocess.run([KB, "comment", card, text[:3800]], timeout=60, capture_output=True)
    subprocess.run([KB, "unblock", card], timeout=60, capture_output=True)


def cmd_exec(job_id: str) -> int:
    """The detached half: runs the job, writes the result, tells the card."""
    d = job_dir(job_id)
    spec = json.loads((d / "job.json").read_text())
    env = dict(os.environ)
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    start = time.time()
    try:
        with open(d / "log", "ab") as log:
            p = subprocess.run(spec["argv"], cwd=spec["cwd"], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                               timeout=int(spec["timeout_minutes"]) * 60)
        code = p.returncode
    except subprocess.TimeoutExpired:
        code = 124
    except OSError as exc:
        (d / "log").open("a").write(f"\ncould not start: {exc}\n")
        code = 127
    tail = (d / "log").read_text(errors="replace")[-1500:]
    result = {"exit": code, "seconds": round(time.time() - start), "ended": time.time()}
    (d / "result.json").write_text(json.dumps(result))
    verdict = "finished" if code == 0 else ("timed out" if code == 124 else f"FAILED (exit {code})")
    report(spec["card"], f"M1 job {job_id} ({spec['job']}) {verdict} in {result['seconds']} s. Log on the M1: {d / 'log'}\n\n{tail}")
    return code


def cmd_status(job_id: str, tail: bool) -> str:
    d = job_dir(job_id)
    if not d.exists():
        raise JobError(f"no job {job_id}")
    if tail:
        return (d / "log").read_text(errors="replace")[-4000:]
    r = d / "result.json"
    return f"{job_id}: " + (f"done, {r.read_text()}" if r.exists() else "running")


def main() -> int:
    raw = os.environ.get("SSH_ORIGINAL_COMMAND")
    argv = shlex.split(raw) if raw is not None else sys.argv[1:]
    try:
        if not argv:
            raise JobError("m1-jobd list | run <job> <card> <bot> [args] | status <id> | log <id>")
        v = argv[0]
        if v == "_exec" and raw is None and len(argv) == 2:
            return cmd_exec(argv[1])
        if v == "list":
            print(cmd_list())
        elif v == "run" and len(argv) >= 4:
            print(cmd_run(argv[1], argv[2], argv[3], argv[4:]))
        elif v in ("status", "log") and len(argv) == 2:
            print(cmd_status(argv[1], tail=v == "log"))
        else:
            raise JobError("m1-jobd list | run <job> <card> <bot> [args] | status <id> | log <id>")
    except JobError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Bot tools against a local bare repo, a fake cli-org, a fake gh, a fake kanban and a fake ssh."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import bot_tools as bt  # noqa: E402
import m1_jobd  # noqa: E402

PR = "https://github.com/kon-rad/argo-exo/pull/7"


def script(path: Path, body: str) -> str:
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    etc = tmp_path / "etc"
    etc.mkdir()
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True)
    (seed / "README.md").write_text("hi\n")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(["git", "-C", str(seed), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed"], check=True)
    subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "main"], check=True)
    token = tmp_path / "token"
    token.write_text("ghp_test\n")
    (etc / "bot-repos.toml").write_text(f"""
token_file = "{token}"
work_dir = "{tmp_path / 'work'}"
[repos.argo-exo]
github = "kon-rad/argo-exo"
clone_url = "{bare}"
default_branch = "main"
protected = ["main"]
test = ["python3", "-c", "print('3 passed')"]
""")
    marker = tmp_path / "deployed"
    (etc / "deploy-targets.toml").write_text(f"""
[targets.demo]
description = "writes a marker"
cwd = "{tmp_path}"
pre = [["true"]]
cmd = ["touch", "{marker}"]
check = ["test", "-f", "{marker}"]
[targets.broken]
cmd = ["false"]
""")
    calls = tmp_path / "calls.jsonl"
    log = f"open({str(calls)!r}, 'a').write(json.dumps(sys.argv) + '\\n')"
    clio = script(tmp_path / "cli-org", f"""import json, os, sys
{log}
if sys.argv[1] == 'whoami':
    print(json.dumps({{'agent': {{'name': os.environ['ORG_AGENT'], 'capabilities': os.environ.get('FAKE_CAPS', '').split(',') if os.environ.get('FAKE_CAPS') else []}}}}))
elif sys.argv[1:3] == ['ship', 'check']:
    ok = os.environ.get('FAKE_SHIP') == 'ok'
    print('ok: approved by owner (#1)' if ok else 'no: ship #1 is waiting on the owner')
    sys.exit(0 if ok else 3)
""")
    gh = script(tmp_path / "gh", f"""import json, os, sys
{log}
assert os.environ.get('GH_TOKEN') == 'ghp_test'
if sys.argv[1:3] == ['pr', 'create']:
    print('{PR}')
elif sys.argv[1:3] == ['pr', 'view'] and 'files' in sys.argv[-1]:
    print(json.dumps({{'number': 7, 'title': 't', 'state': 'OPEN', 'author': {{'login': 'myargoquest'}}, 'headRefName': 'bot/rune/t_abc123', 'baseRefName': 'main', 'mergeable': 'MERGEABLE', 'reviewDecision': '', 'body': 'b', 'files': [{{'path': 'a.py', 'additions': 3, 'deletions': 1}}], 'statusCheckRollup': [{{'name': 'app', 'conclusion': 'SUCCESS'}}]}}))
elif sys.argv[1:3] == ['pr', 'checks']:
    print('app\tpass')
elif sys.argv[1:3] == ['pr', 'view'] and '--json' in sys.argv:
    head = open({str(tmp_path / 'head.txt')!r}).read() if os.path.exists({str(tmp_path / 'head.txt')!r}) else 'bot/rune/t_abc123'
    print(json.dumps({{'headRefName': head, 'body': os.environ.get('FAKE_BODY', 'x\\n\\nCard: t_abc123'), 'state': 'OPEN', 'title': 't'}}))
""")
    hermes = script(tmp_path / "hermes", f"import json, sys\n{log}\n")
    ssh = script(tmp_path / "ssh", f"""import json, os, sys
{log}
open({str(tmp_path / 'ssh-stdin')!r}, 'wb').write(sys.stdin.buffer.read())
if os.environ.get('FAKE_M1_OFF'):
    print('ssh: connect to host m1max port 22: Operation timed out', file=sys.stderr)
    sys.exit(255)
print('job 20261019-120000-caption started on the M1 for t_abc123')
""")
    monkeypatch.setenv("BOT_TOOLS_ETC", str(etc))
    monkeypatch.setenv("BOT_TOOLS_LOGS", str(tmp_path / "logs"))
    leaks = script(tmp_path / "gitleaks", "import sys, pathlib\nsys.exit(1 if pathlib.Path('FAKE_LEAK').exists() else 0)\n")
    monkeypatch.setenv("GITLEAKS_BIN", leaks)
    for var, path in (("CLI_ORG", clio), ("GH_BIN", gh), ("HERMES_BIN", hermes), ("SSH_BIN", ssh)):
        monkeypatch.setenv(var, path)
    monkeypatch.setenv("ORG_AGENT", "rune")
    monkeypatch.setenv("FAKE_CAPS", "code")

    def recorded():
        return [json.loads(l) for l in calls.read_text().splitlines()] if calls.exists() else []

    return {"tmp": tmp_path, "bare": bare, "marker": marker, "calls": recorded}


def call(name, *args):
    return bt.main([f"/usr/local/bin/{name}", *args])


def test_refuses_without_identity_or_capability(env, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_CAPS", "")
    assert call("cli-git", "start", "argo-exo", "t_abc123") == 2
    assert "rune lacks the code capability" in capsys.readouterr().err
    monkeypatch.delenv("ORG_AGENT")
    assert call("cli-deploy", "list") == 2
    assert "ORG_AGENT is not set" in capsys.readouterr().err


def test_branch_commit_push_pr(env, capsys):
    assert call("cli-git", "start", "argo-exo", "t_abc123") == 0
    wt = env["tmp"] / "work" / "wt" / "argo-exo-t_abc123"
    assert "bot/rune/t_abc123" in capsys.readouterr().out
    (wt / "new.txt").write_text("work\n")
    assert call("cli-git", "test", "t_abc123") == 0
    assert "3 passed" in capsys.readouterr().out
    assert call("cli-git", "commit", "t_abc123", "Add new.txt") == 0
    log = subprocess.run(["git", "-C", str(wt), "log", "-1", "--format=%an|%B"], capture_output=True, text=True).stdout
    assert log.startswith("rune (Argo bot)|Add new.txt") and "Card: t_abc123" in log
    assert call("cli-git", "push", "t_abc123") == 0
    heads = subprocess.run(["git", "-C", str(env["bare"]), "branch", "--list"], capture_output=True, text=True).stdout
    assert "bot/rune/t_abc123" in heads
    main_log = subprocess.run(["git", "-C", str(env["bare"]), "log", "--oneline", "main"], capture_output=True, text=True).stdout
    assert "Add new.txt" not in main_log
    assert call("cli-git", "pr", "create", "t_abc123", "Add new.txt", "--body", "tests: 3 passed") == 0
    gh_create = [c for c in env["calls"]() if c[1:3] == ["pr", "create"]][0]
    assert gh_create[gh_create.index("--head") + 1] == "bot/rune/t_abc123"
    assert "Card: t_abc123" in gh_create[gh_create.index("--body") + 1]
    assert any(c[1:3] == ["kanban", "--board"] and PR in c[-1] for c in env["calls"]())


def test_pre_push_check_blocks_the_push(env, capsys):
    conf = env["tmp"] / "etc" / "bot-repos.toml"
    conf.write_text(conf.read_text() + 'pre_push = ["bash", "-c", "! grep -rq SECRET --exclude-dir=.git ."]\n')
    assert call("cli-git", "start", "argo-exo", "t_abc123") == 0
    wt = env["tmp"] / "work" / "wt" / "argo-exo-t_abc123"
    (wt / "leak.txt").write_text("SECRET\n")
    assert call("cli-git", "commit", "t_abc123", "oops") == 0
    assert call("cli-git", "push", "t_abc123") == 2
    assert "not pushed" in capsys.readouterr().err
    heads = subprocess.run(["git", "-C", str(env["bare"]), "branch", "--list"], capture_output=True, text=True).stdout
    assert "bot/rune/t_abc123" not in heads


def test_public_repo_push_is_scanned_and_uncommit_redoes(env, capsys):
    assert call("cli-git", "start", "argo-exo", "t_abc123") == 0       # the fake gh can't say, so it counts as public
    wt = env["tmp"] / "work" / "wt" / "argo-exo-t_abc123"
    (wt / "notes.md").write_text("ssh root@" + "165.22.103.109 then mail konrad" + "@gmail.com\nchat -1001234567890\n")
    (wt / ".env").write_text("X=1\n")
    assert call("cli-git", "commit", "t_abc123", "leaky") == 0
    assert call("cli-git", "push", "t_abc123") == 2
    err = capsys.readouterr().err
    for label in ("ipv4", "email", "telegram chat id", ".env: file that shouldn't be public"):
        assert label in err
    assert "165.22" not in err and "gmail" not in err                    # findings never echo the value
    assert call("cli-git", "uncommit", "t_abc123") == 0
    (wt / "notes.md").write_text("ssh to the droplet; see the runbook\nlocalhost is 127.0.0.1\nsee 10.1.2.3 # public-ok\n")
    (wt / ".env").unlink()
    assert call("cli-git", "commit", "t_abc123", "clean") == 0
    assert call("cli-git", "push", "t_abc123") == 0
    (wt / "FAKE_LEAK").write_text("x\n")                                 # gitleaks says no
    assert call("cli-git", "commit", "t_abc123", "secret") == 0
    assert call("cli-git", "push", "t_abc123") == 2
    assert "gitleaks found a secret" in capsys.readouterr().err
    assert call("cli-git", "pr", "create", "t_abc123", "t", "--body", "deployed to 165.22.103.109") == 2  # public-ok: the refusal test needs a real address


def test_tests_wait_for_memory(env, monkeypatch, capsys):
    assert call("cli-git", "start", "argo-exo", "t_abc123") == 0
    monkeypatch.setattr(bt, "mem_available_mb", lambda: 300)
    assert call("cli-git", "test", "t_abc123") == 2
    assert "300 MB free" in capsys.readouterr().err
    monkeypatch.setattr(bt, "mem_available_mb", lambda: 3000)
    assert call("cli-git", "test", "t_abc123") == 0


def test_pr_view_uses_named_fields_and_checks(env, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_CAPS", "merge")
    assert call("cli-git", "pr", "view", PR) == 0
    out = capsys.readouterr().out
    assert "app: SUCCESS" in out and "a.py (+3 -1)" in out
    assert call("cli-git", "pr", "checks", PR) == 0
    assert "pass" in capsys.readouterr().out


def test_heavy_repo_tests_go_to_the_m1_with_the_worktree(env, monkeypatch, capsys):
    conf = env["tmp"] / "etc" / "bot-repos.toml"
    conf.write_text(conf.read_text().replace('default_branch = "main"', 'default_branch = "main"\ntest_on = "m1"', 1))
    assert call("cli-git", "start", "argo-exo", "t_abc123") == 0
    wt = env["tmp"] / "work" / "wt" / "argo-exo-t_abc123"
    (wt / "new.py").write_text("x = 1\n")                       # uncommitted, untracked: goes along
    (wt / ".gitignore").write_text("node_modules/\n")
    (wt / "node_modules").mkdir()
    (wt / "node_modules" / "big.js").write_text("ignored")     # ignored: stays behind
    capsys.readouterr()
    assert call("cli-git", "test", "t_abc123") == 0
    out = capsys.readouterr().out
    assert "started on the M1" in out and "uncommitted" in out and "waiting on M1 tests" in out and "3 passed" not in out
    ssh = [c for c in env["calls"]() if c[0].endswith("ssh")][-1]
    verb = ssh[-1].split()
    assert verb[:4] == ["test", "argo-exo", "t_abc123", "rune"] and len(verb[4]) == 40
    with __import__("tarfile").open(env["tmp"] / "ssh-stdin", "r:gz") as tar:
        names = tar.getnames()
    assert {"README.md", "new.py", ".gitignore"} <= set(names) and not any(n.startswith(("node_modules", ".git/")) for n in names)

    monkeypatch.setenv("FAKE_M1_OFF", "1")
    assert call("cli-git", "test", "t_abc123") == 0
    out = capsys.readouterr().out
    assert "M1 off" in out and "never run on the droplet" in out


def test_another_bot_cannot_work_in_my_worktree(env, monkeypatch, capsys):
    assert call("cli-git", "start", "argo-exo", "t_abc123") == 0
    monkeypatch.setenv("ORG_AGENT", "kael")
    assert call("cli-git", "commit", "t_abc123", "sneaky") == 2
    assert "only the bot that started it" in capsys.readouterr().err


def test_review_is_a_comment_and_never_on_your_own_pr(env, monkeypatch, capsys):
    monkeypatch.setenv("ORG_AGENT", "vera")
    monkeypatch.setenv("FAKE_CAPS", "review")
    assert call("cli-git", "pr", "review", PR, "--approve", "looks right; tests ran") == 0
    review = [c for c in env["calls"]() if c[1:3] == ["pr", "review"]][0]
    assert "--comment" in review and review[review.index("--body") + 1].startswith("**vera: Approve**")
    (env["tmp"] / "head.txt").write_text("bot/vera/t_abc123")   # the wrapper strips gh's environment, so the fake reads a file
    assert call("cli-git", "pr", "review", PR, "--approve", "mine") == 2
    assert "own PR" in capsys.readouterr().err
    assert call("cli-git", "pr", "review", "https://github.com/evil/repo/pull/1", "--approve", "x") == 2


def test_merge_needs_merge_capability_the_card_and_approval(env, monkeypatch, capsys):
    assert call("cli-git", "pr", "merge", PR, "--card", "t_abc123") == 2          # rune has code, not merge
    monkeypatch.setenv("ORG_AGENT", "atlas")
    monkeypatch.setenv("FAKE_CAPS", "review,merge")
    assert call("cli-git", "pr", "merge", PR, "--card", "t_abc123") == 2
    assert "not approved" in capsys.readouterr().err
    assert not any(c[1:3] == ["pr", "merge"] for c in env["calls"]())
    monkeypatch.setenv("FAKE_SHIP", "ok")
    assert call("cli-git", "pr", "merge", PR, "--card", "t_999999") == 2           # another card's PR
    assert "not card t_999999's PR" in capsys.readouterr().err
    assert call("cli-git", "pr", "merge", PR, "--card", "t_abc123") == 0
    merge = [c for c in env["calls"]() if c[1:3] == ["pr", "merge"]][0]
    assert "--squash" in merge and "--delete-branch" in merge


def test_repo_create_is_private_in_the_bots_org_and_never_deletes(env, monkeypatch, capsys):
    assert call("cli-git", "repo", "create", "pocket-tool", "--card", "t_abc123") == 2      # no [create] owner yet
    assert "repo creation is off" in capsys.readouterr().err
    conf = env["tmp"] / "etc" / "bot-repos.toml"
    conf.write_text(conf.read_text().replace("[repos.argo-exo]", '[create]\nowner = "argo-bots"\nper_day = 2\n\n[repos.argo-exo]'))
    monkeypatch.setenv("FAKE_CAPS", "web")
    assert call("cli-git", "repo", "create", "pocket-tool", "--card", "t_abc123") == 2      # needs code
    monkeypatch.setenv("FAKE_CAPS", "code")
    assert call("cli-git", "repo", "create", "Bad_Name", "--card", "t_abc123") == 2
    assert call("cli-git", "repo", "create", "argo-exo", "--card", "t_abc123") == 2        # already a repo
    assert call("cli-git", "repo", "create", "pocket-tool", "--card", "t_abc123", "--description", "a tool") == 0
    made = [c for c in env["calls"]() if c[1:3] == ["repo", "create"]][0]
    assert made[3] == "argo-bots/pocket-tool" and "--private" in made and "--public" not in made
    capsys.readouterr()
    assert call("cli-git", "repos") == 0
    assert "argo-bots/pocket-tool" in capsys.readouterr().out
    assert bt.repo_conf("pocket-tool")["created_by"] == "rune"
    # an entry pointing outside the org (the file is hermes-writable) is ignored
    f = env["tmp"] / "work" / "created-repos.json"
    data = json.loads(f.read_text())
    data["sneaky"] = {"github": "kon-rad/private-thing", "created": "x"}
    f.write_text(json.dumps(data))
    assert "sneaky" not in bt.repos()
    assert call("cli-git", "repo", "create", "second-tool", "--card", "t_abc123") == 0
    assert call("cli-git", "repo", "create", "third-tool", "--card", "t_abc123") == 2      # per_day 2
    assert "already made 2 repos today" in capsys.readouterr().err
    assert call("cli-git", "repo", "delete", "pocket-tool") == 2                           # there is no delete
    assert not any("delete" in c[1:3] for c in env["calls"]() if c[0].endswith("gh"))


def test_deploy_runs_only_registered_approved_targets(env, monkeypatch, capsys):
    monkeypatch.setenv("ORG_AGENT", "sable")
    monkeypatch.setenv("FAKE_CAPS", "deploy")
    assert call("cli-deploy", "nowhere", "--card", "t_abc123") == 2
    assert call("cli-deploy", "demo", "--card", "t_abc123") == 2
    assert "not approved" in capsys.readouterr().err and not env["marker"].exists()
    monkeypatch.setenv("FAKE_SHIP", "ok")
    assert call("cli-deploy", "demo", "--card", "t_abc123") == 0
    assert env["marker"].exists() and "check passed" in capsys.readouterr().out
    assert call("cli-deploy", "broken", "--card", "t_abc123") == 2
    assert any("FAILED" in c[-1] for c in env["calls"]() if c[1:2] == ["kanban"])


def test_m1_job_goes_through_ssh_and_says_to_block(env, monkeypatch, capsys):
    monkeypatch.setenv("ORG_AGENT", "echo")
    monkeypatch.setenv("FAKE_CAPS", "m1")
    assert call("m1-job", "run", "caption", "--card", "t_abc123", "--", "~/Movies/a b.mp4") == 0
    out = capsys.readouterr().out
    assert "started on the M1" in out and "hermes kanban schedule t_abc123" in out
    ssh = [c for c in env["calls"]() if c[0].endswith("ssh")][0]
    assert ssh[-1] == "run caption t_abc123 echo '~/Movies/a b.mp4'"


# -- the M1 side ---------------------------------------------------------------------------------------------------
@pytest.fixture
def m1(tmp_path, monkeypatch):
    movies = tmp_path / "Movies"
    movies.mkdir()
    (movies / "clip.mp4").write_text("x")
    jobs = tmp_path / "jobs.toml"
    jobs.write_text(f"""
[jobs.echo]
description = "prints its args"
cmd = ["{sys.executable}", "-c", "import sys; print('ARGS', sys.argv[1:])"]
roots = ["{movies}"]
flags = ["--mode"]
path_flags = ["--dest"]
timeout_minutes = 1
""")
    kb_log = tmp_path / "kb.log"
    kb = script(tmp_path / "kb", f"import sys\nopen({str(kb_log)!r}, 'a').write(' '.join(sys.argv[1:3]) + '\\n')\n")
    monkeypatch.setattr(m1_jobd, "JOBS_FILE", jobs)
    monkeypatch.setattr(m1_jobd, "STATE", tmp_path / "state")
    monkeypatch.setattr(m1_jobd, "KB", kb)
    return {"movies": movies, "kb_log": kb_log, "tmp": tmp_path}


def test_m1_args_stay_inside_roots(m1):
    job = m1_jobd.jobs()["echo"]
    assert m1_jobd.check_args(job, [str(m1["movies"] / "clip.mp4"), "--mode=classic", f"--dest={m1['movies']}/out"])
    with pytest.raises(m1_jobd.JobError, match="outside"):
        m1_jobd.check_args(job, ["/etc/passwd"])
    with pytest.raises(m1_jobd.JobError, match="outside"):
        m1_jobd.check_args(job, [str(m1["movies"] / ".." / "secret")])
    with pytest.raises(m1_jobd.JobError, match="not allowed"):
        m1_jobd.check_args(job, ["--exec=rm"])
    with pytest.raises(m1_jobd.JobError, match="bad value"):
        m1_jobd.check_args(job, ["--mode=a;rm -rf"])
    with pytest.raises(m1_jobd.JobError, match="no job"):
        m1_jobd.cmd_run("rm", "t_abc123", "echo", [])


def test_m1_job_runs_detached_and_reports_to_the_card(m1, monkeypatch):
    monkeypatch.setenv("M1_JOBS_FILE", str(m1_jobd.JOBS_FILE))
    monkeypatch.setenv("M1_JOBS_STATE", str(m1_jobd.STATE))
    monkeypatch.setenv("M1_JOBS_KB", m1_jobd.KB)
    monkeypatch.setenv("M1_JOBS_VAULT_ENV", str(m1_jobd.STATE / "no-such.env"))   # never reach a real board from a test
    monkeypatch.delenv("BOARD_URL", raising=False)
    monkeypatch.delenv("BOARD_TOKEN", raising=False)
    out = m1_jobd.cmd_run("echo", "t_abc123", "echo", [str(m1["movies"] / "clip.mp4")])
    job_id = out.split()[1]
    d = m1_jobd.STATE / job_id
    for _ in range(100):
        if (d / "result.json").exists() and m1["kb_log"].exists() and "unblock" in m1["kb_log"].read_text():
            break
        __import__("time").sleep(0.1)
    assert json.loads((d / "result.json").read_text())["exit"] == 0
    assert "ARGS" in (d / "log").read_text()
    assert m1["kb_log"].read_text().splitlines() == ["comment t_abc123", "unblock t_abc123"]


def test_exec_is_refused_over_ssh(m1, monkeypatch, capsys):
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "_exec 20261019-120000-echo")
    assert m1_jobd.main() == 2
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "rm -rf ~")
    assert m1_jobd.main() == 2


def test_m1_tests_come_from_the_allowlist_run_sandboxed_and_clean_up(m1, monkeypatch, tmp_path):
    import io, tarfile, time
    jobs = m1_jobd.JOBS_FILE
    jobs.write_text(jobs.read_text() + f"""
[tests.demo]
cmd = ["{sys.executable}", "-c", "import os, sys; print('SRC', sorted(os.listdir('.')), 'CI', os.environ.get('CI')); sys.exit(int(open('rc').read()))"]
timeout_minutes = 1
""")
    sandbox = script(tmp_path / "sandbox-exec", "import os, sys\na = sys.argv[1:]\nwhile a and a[0] in ('-p', '-D'):\n    a = a[2:]\nos.execvp(a[0], a)\n")
    monkeypatch.setattr(m1_jobd, "SANDBOX", sandbox)
    monkeypatch.setattr(m1_jobd, "TESTS_DIR", tmp_path / "bot-tests")
    for var, val in (("M1_JOBS_FILE", jobs), ("M1_JOBS_STATE", m1_jobd.STATE), ("M1_JOBS_KB", m1_jobd.KB),
                     ("M1_JOBS_VAULT_ENV", tmp_path / "no-such.env")):
        monkeypatch.setenv(var, str(val))
    monkeypatch.delenv("BOARD_URL", raising=False)
    monkeypatch.delenv("BOARD_TOKEN", raising=False)

    def tree(rc: str, evil: bool = False) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, body in (("rc", rc), ("app.py", "x")) + ((("../escape", "x"),) if evil else ()):
                info = tarfile.TarInfo(name)
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body.encode()))
        return buf.getvalue()

    with pytest.raises(m1_jobd.JobError, match="no tests"):
        m1_jobd.cmd_test("rm", "t_abc123", "rune", "a" * 40, tree("0"))
    with pytest.raises(m1_jobd.JobError, match="stdin"):
        m1_jobd.cmd_test("demo", "t_abc123", "rune", "a" * 40, b"")
    with pytest.raises(m1_jobd.JobError):
        m1_jobd.cmd_test("demo", "t_abc123", "rune", "not-a-sha", tree("0"))
    with pytest.raises(m1_jobd.JobError, match="bad worktree archive"):
        m1_jobd.cmd_test("demo", "t_abc123", "rune", "a" * 40, tree("0", evil=True))
    assert not (tmp_path / "escape").exists() and not list((tmp_path / "bot-tests").iterdir())

    for rc, verdict in (("0", "PASSED"), ("1", "FAILED (exit 1)")):
        if m1["kb_log"].exists():
            m1["kb_log"].unlink()
        out = m1_jobd.cmd_test("demo", "t_abc123", "rune", "b" * 40, tree(rc))
        job_id = out.split()[1]
        d = m1_jobd.STATE / job_id
        for _ in range(100):
            if m1["kb_log"].exists() and "unblock" in m1["kb_log"].read_text():
                break
            time.sleep(0.1)
        spec = json.loads((d / "job.json").read_text())
        assert spec["argv"][3] == sandbox and spec["argv"][4] == "-p" and "file-read*" in spec["argv"][5]
        assert "SRC ['app.py', 'rc'] CI 1" in (d / "log").read_text()
        assert json.loads((d / "result.json").read_text())["exit"] == int(rc)
        assert not (tmp_path / "bot-tests" / job_id).exists()   # the tree and its node_modules are gone; the log stays
        assert m1["kb_log"].read_text().splitlines() == ["comment t_abc123", "unblock t_abc123"]
        time.sleep(1.1)   # job ids are per second


def test_park_and_block_hints_follow_the_board_in_use(monkeypatch):
    import bot_tools
    monkeypatch.delenv("CC_BOARD_BACKEND", raising=False)
    assert bot_tools.park_hint("t_1", "waiting") == '`hermes kanban schedule t_1 "waiting"`'
    assert bot_tools.block_hint("t_1", "off") == '`hermes kanban block t_1 "off"`'
    monkeypatch.setenv("CC_BOARD_BACKEND", "engine")
    assert bot_tools.park_hint("t_1", "waiting") == '`cli-board finish t_1 --blocked dependency "waiting"`'
    assert bot_tools.block_hint("t_1", "off") == '`cli-board finish t_1 --blocked tool-error "off"`'


def test_m1_report_uses_the_engine_board_when_the_m1_has_a_token(monkeypatch, tmp_path):
    posts = []
    monkeypatch.setattr(m1_jobd, "board_post", lambda door, path, body: posts.append((door, path, body)) or True)
    env_file = tmp_path / ".env"
    env_file.write_text('BOARD_URL="https://board.example/api/board/"\nBOARD_TOKEN=sbb_x\n')
    monkeypatch.setenv("M1_JOBS_VAULT_ENV", str(env_file))
    monkeypatch.delenv("BOARD_URL", raising=False)
    monkeypatch.delenv("BOARD_TOKEN", raising=False)
    m1_jobd.report("t_abc123", "tests passed")
    assert posts == [(("https://board.example/api/board", "sbb_x"), "/cards/t_abc123/comment", {"body": "tests passed"}),
                     (("https://board.example/api/board", "sbb_x"), "/cards/t_abc123/move", {"status": "ready", "note": "m1 job finished"})]

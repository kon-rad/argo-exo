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
elif sys.argv[1:3] == ['pr', 'view'] and '--json' in sys.argv:
    head = open({str(tmp_path / 'head.txt')!r}).read() if os.path.exists({str(tmp_path / 'head.txt')!r}) else 'bot/rune/t_abc123'
    print(json.dumps({{'headRefName': head, 'body': os.environ.get('FAKE_BODY', 'x\\n\\nCard: t_abc123'), 'state': 'OPEN', 'title': 't'}}))
""")
    hermes = script(tmp_path / "hermes", f"import json, sys\n{log}\n")
    ssh = script(tmp_path / "ssh", f"import json, sys\n{log}\nprint('job 20261019-120000-caption started on the M1 for t_abc123')\n")
    monkeypatch.setenv("BOT_TOOLS_ETC", str(etc))
    monkeypatch.setenv("BOT_TOOLS_LOGS", str(tmp_path / "logs"))
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
    assert "started on the M1" in out and "hermes kanban block t_abc123" in out
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

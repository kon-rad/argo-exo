import json
import subprocess
import pytest
from exo_bridge.kanban import Kanban, KanbanError

TASK = {"id": "t_1", "title": "find gas APIs", "assignee": "researcher", "status": "ready"}


class Runner:
    def __init__(self, stdout="{}", code=0, stderr=""):
        self.calls, self.out, self.code, self.err = [], stdout, code, stderr

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        assert kw["timeout"] <= 30 and kw["capture_output"] and kw["text"]
        assert not kw.get("shell")
        return subprocess.CompletedProcess(cmd, self.code, self.out, self.err)


def test_create_builds_the_cli_call():
    r = Runner(json.dumps(TASK))
    task = Kanban("hermes", "exo", r).create("find gas APIs", "full text", "researcher", "30m", "idem-1")
    assert task == TASK
    cmd = r.calls[0]
    assert cmd[:5] == ["hermes", "kanban", "--board", "exo", "create"]
    assert "--body=full text" in cmd
    for flag, val in [("--assignee", "researcher"),
                      ("--max-runtime", "30m"), ("--idempotency-key", "idem-1"),
                      ("--created-by", "exo-deck")]:
        assert cmd[cmd.index(flag) + 1] == val
    assert "--json" in cmd


def test_create_title_after_double_dash():
    r = Runner(json.dumps(TASK))
    Kanban("hermes", "exo", r).create("-rf --assignee evil", "b", "researcher", "30m", "i")
    cmd = r.calls[0]
    assert cmd[-2:] == ["--", "-rf --assignee evil"]


def test_create_quotes_and_newlines_stay_one_argv_item():
    r = Runner(json.dumps(TASK))
    title = "say \"hi\"\n'; rm -rf /"
    Kanban("hermes", "exo", r).create(title, "b", "researcher", "30m", "i")
    cmd = r.calls[0]
    assert cmd[-2:] == ["--", title] and cmd.count("--") == 1


def test_cli_failure_raises_with_stderr_tail():
    with pytest.raises(KanbanError, match="no such board"):
        Kanban("hermes", "exo", Runner(code=2, stderr="kanban: no such board")).list()


def test_timeout_raises():
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 30)
    with pytest.raises(KanbanError):
        Kanban("hermes", "exo", run).list()


def test_bad_json_raises():
    with pytest.raises(KanbanError):
        Kanban("hermes", "exo", Runner("not json")).list()


def test_list_and_subscribe():
    r = Runner(json.dumps([TASK]))
    k = Kanban("hermes", "exo", r)
    assert k.list() == [TASK]
    k.subscribe("t_1", "12345")
    assert r.calls[1][4:] == ["notify-subscribe", "--platform", "telegram", "--chat-id", "12345", "t_1"]


@pytest.mark.parametrize("body", ["-x", "--help", "--json"])
def test_flaglike_body_stays_inside_one_item(body):
    r = Runner(json.dumps(TASK))
    Kanban("hermes", "exo", r).create("t", body, "researcher", "30m", "i")
    cmd = r.calls[0]
    assert f"--body={body}" in cmd and cmd.count(body) == (1 if body == "--json" else 0)

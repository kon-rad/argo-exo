import json
import subprocess

import pytest
from exo_bridge.board import Board, BoardError
from exo_bridge.notify import Watcher, summary

CARD = {"id": "t_1", "title": "find gas APIs", "assignee": "vega", "status": "ready"}


class Runner:
    def __init__(self, stdout="{}", code=0, stderr=""):
        self.calls, self.out, self.code, self.err = [], stdout, code, stderr

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        assert kw["timeout"] <= 60 and kw["capture_output"] and kw["text"]
        assert not kw.get("shell")
        return subprocess.CompletedProcess(cmd, self.code, self.out, self.err)


def test_create_builds_the_cli_call():
    r = Runner(json.dumps({"card": CARD}))
    card = Board("cli-board", "exo", r).create("find gas APIs", "full text", "vega", "idem-1")
    assert card == CARD
    assert r.calls[0] == ["cli-board", "create", "find gas APIs", "--to", "vega", "--board", "exo", "--body", "full text", "--json"]


def test_no_flag_for_identity_or_runtime():
    r = Runner(json.dumps(CARD))
    Board("cli-board", "exo", r).create("t", "b", "vega", "i")
    assert not {"--author", "--created-by", "--max-runtime", "--idempotency-key", "--assignee"} & set(r.calls[0])


@pytest.mark.parametrize("text", ["--help", "--json", "--to evil"])
def test_flaglike_title_and_body_get_a_leading_space(text):
    r = Runner(json.dumps(CARD))
    Board("cli-board", "exo", r).create(text, text, "vega", "i")
    cmd = r.calls[0]
    assert cmd[2] == " " + text and cmd[cmd.index("--body") + 1] == " " + text
    assert cmd.count(text) == (1 if text == "--json" else 0)     # only the real --json flag, never the text as a flag


def test_quotes_and_newlines_stay_one_argv_item():
    r = Runner(json.dumps(CARD))
    title = "say \"hi\"\n'; rm -rf /"
    Board("cli-board", "exo", r).create(title, "b", "vega", "i")
    assert r.calls[0][2] == title


def test_the_same_key_returns_the_card_already_made():
    r = Runner(json.dumps(CARD))
    b = Board("cli-board", "exo", r)
    assert b.create("t", "b", "vega", "same") == b.create("t", "b", "vega", "same")
    assert len(r.calls) == 1
    b.create("t", "b", "vega", "other")
    assert len(r.calls) == 2


def test_cli_failure_raises_with_stderr_tail():
    with pytest.raises(BoardError, match="no board"):
        Board("cli-board", "exo", Runner(code=2, stderr="no board")).show("t_1")


def test_timeout_raises():
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 30)
    with pytest.raises(BoardError):
        Board("cli-board", "exo", run).show("t_1")


def test_bad_json_raises():
    with pytest.raises(BoardError):
        Board("cli-board", "exo", Runner("not json")).show("t_1")


def test_list_is_one_narrow_call_per_assignee_newest_first():
    rows = {"vega": [{"id": "t_1", "created_at": "2026-10-26T01:00:00Z"}], "rune": [{"id": "t_2", "created_at": "2026-10-27T01:00:00Z"}]}

    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, json.dumps(rows[cmd[cmd.index("--assignee") + 1]]), "")
    got = Board("cli-board", "exo", run).list(("vega", "rune"))
    assert [t["id"] for t in got] == ["t_2", "t_1"]


def test_list_always_filters_and_limits():
    r = Runner("[]")
    Board("cli-board", "exo", r).list(("vega",))
    assert r.calls[0] == ["cli-board", "list", "--board", "exo", "--assignee", "vega", "--limit", "50", "--json"]


# -- the Telegram ping the Hermes board used to send ---------------------------------------------------------------
class ShowBoard:
    def __init__(self, *states):
        self.states = list(states)

    def show(self, card_id):
        s = self.states.pop(0)
        if isinstance(s, Exception):
            raise s
        return {"id": card_id, "title": "find gas APIs", **s}


def run_watch(board, cmd="/bin/notify --chat 7", **kw):
    r = Runner()
    w = Watcher(board, cmd, run=r, sleep=lambda s: None, start=lambda fn: fn(), **kw)
    w.watch("t_1")
    return r


def test_done_card_runs_the_notifier_with_the_result():
    r = run_watch(ShowBoard({"status": "running"}, BoardError("blip"), {"status": "done", "result": "3 APIs, see note"}))
    assert r.calls == [["/bin/notify", "--chat", "7", "Exo card t_1 finished: find gas APIs - 3 APIs, see note"]]


def test_blocked_card_is_reported_with_its_reason():
    r = run_watch(ShowBoard({"status": "blocked", "block_reason": "needs a key"}))
    assert "is blocked" in r.calls[0][-1] and "needs a key" in r.calls[0][-1]


def test_no_notifier_means_nothing_is_watched():
    started = []
    w = Watcher(ShowBoard(), "", start=started.append)
    w.watch("t_1")
    assert started == [] and not w.enabled


def test_the_watch_gives_up():
    r = run_watch(ShowBoard(*[{"status": "running"}] * 3), poll_seconds=10, give_up_seconds=30)
    assert r.calls == []


def test_summary_is_short():
    assert len(summary({"id": "t", "title": "x", "status": "done", "result": "y" * 2000})) <= 600

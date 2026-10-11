import json
import signal
import subprocess
from pathlib import Path

import pytest

from cc_dispatcher import worker
from cc_dispatcher.board import Board, BoardError
from cc_dispatcher.config import load
from cc_dispatcher.dispatcher import Dispatcher

REPO_LANES = Path(__file__).resolve().parents[2] / "cc-lanes" / "lanes.toml"


# -- fixtures ------------------------------------------------------------------------------------------------------
def lanes_dir(tmp_path, extra="", mode="acceptEdits", name="cc-test"):
    d = tmp_path / "lanes"
    d.mkdir()
    (d / "worker-protocol.md").write_text("PROTOCOL")
    (d / f"{name}.md").write_text("ROLE")
    (d / "lanes.toml").write_text(f"""
[dispatcher]
boards = ["exo"]
max_in_progress = 2
daily_cap = 3
runs_dir = "{tmp_path / 'runs'}"
work_dir = "{tmp_path / 'work'}"
claude_bin = "/opt/claude/claude"
hermes_bin = "/opt/hermes/hermes"

[defaults]
cwd = "/vault"
allowed_tools = ["Read"]
denied_tools = ["Bash(sudo *)"]

[lanes.{name}]
description = "test lane"
permission_mode = "{mode}"
max_runtime_minutes = 10
{extra}
""")
    return d / "lanes.toml"


class FakeBoard:
    def __init__(self, ready=(), running=(), status="done"):
        self.ready, self.running_tasks, self.final = list(ready), list(running), status
        self.claims, self.comments, self.blocks, self.attached = [], [], [], []
        self.claimable = True

    def tasks(self, assignee, status):
        rows = self.ready if status == "ready" else self.running_tasks
        return [t for t in rows if t.get("assignee", assignee) == assignee]

    def claim(self, task_id, ttl):
        self.claims.append((task_id, ttl))
        if self.claimable:
            self.ready = [t for t in self.ready if t["id"] != task_id]
        return self.claimable

    def status(self, task_id):
        return self.final

    def comment(self, task_id, text, author):
        self.comments.append((task_id, text))

    def block(self, task_id, reason, kind="transient"):
        self.blocks.append((task_id, reason, kind))

    def attach(self, task_id, path, author):
        self.attached.append((task_id, path))


class FakeProc:
    def __init__(self):
        self.code, self.signals, self.pid = None, [], 4242

    def poll(self):
        return self.code

    def send_signal(self, sig):
        self.signals.append(sig)


def make(tmp_path, board, **kw):
    settings = load(lanes_dir(tmp_path, **kw))
    procs = []

    def spawn(settings, lane, board_name, task_id, cwd, log_path):
        p = FakeProc()
        procs.append((task_id, cwd, p))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("")
        return p
    clock = {"t": 1_800_000_000.0}
    d = Dispatcher(settings, {"exo": board}, spawn=spawn, prepare_cwd=lambda s, l, t: l.cwd,
                   clock=lambda: clock["t"])
    return d, procs, clock


# -- config --------------------------------------------------------------------------------------------------------
def test_repo_lanes_file_loads():
    s = load(REPO_LANES)
    assert set(s.lanes) == {"cc-coder", "cc-research", "cc-writer", "cc-reviewer"}
    for lane in s.lanes.values():
        assert "Bash(sudo *)" in lane.denied_tools and "Read(/home/trader/**)" in lane.denied_tools
        assert "Kanban worker protocol" in lane.prompt
    assert s.lanes["cc-coder"].worktree and "WebSearch" in s.lanes["cc-research"].allowed_tools


def test_lane_tools_add_to_defaults(tmp_path):
    s = load(lanes_dir(tmp_path, extra='allowed_tools = ["WebFetch"]\ndenied_tools = ["Bash(rm *)"]'))
    lane = s.lanes["cc-test"]
    assert lane.allowed_tools == ("Read", "WebFetch")
    assert lane.denied_tools == ("Bash(sudo *)", "Bash(rm *)")
    assert lane.prompt.startswith("ROLE") and "PROTOCOL" in lane.prompt


def test_bypass_permissions_refused(tmp_path):
    with pytest.raises(ValueError, match="permission_mode"):
        load(lanes_dir(tmp_path, mode="bypassPermissions"))


def test_lane_must_not_look_like_a_hermes_profile(tmp_path):
    with pytest.raises(ValueError, match="names must match"):
        load(lanes_dir(tmp_path, name="researcher"))


# -- worker command and env ----------------------------------------------------------------------------------------
def test_command_is_headless_subscription_safe(tmp_path):
    s = load(lanes_dir(tmp_path))
    cmd = worker.command(s, s.lanes["cc-test"], "exo", "t_1")
    assert cmd[0] == "/opt/claude/claude" and cmd[1] == "-p" and "t_1" in cmd[2]
    assert "--bare" not in cmd                       # bare mode ignores the subscription login
    assert "--dangerously-skip-permissions" not in cmd
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"
    assert cmd[cmd.index("--output-format") + 1] == "stream-json" and "--verbose" in cmd
    allowed = cmd[cmd.index("--allowedTools") + 1].split(",")
    assert "Bash(hermes kanban --board exo complete *)" in allowed
    assert not any("--board other" in a for a in allowed)
    assert cmd[cmd.index("--disallowedTools") + 1] == "Bash(sudo *)"


def test_worker_env_is_built_from_scratch(tmp_path):
    s = load(lanes_dir(tmp_path))
    base = {"HOME": "/home/hermes", "ANTHROPIC_API_KEY": "sk-x", "OPENROUTER_API_KEY": "or", "TZ": "Asia/Tokyo"}
    env = worker.worker_env(base, s, s.lanes["cc-test"], "exo", "t_1")
    assert "ANTHROPIC_API_KEY" not in env and "OPENROUTER_API_KEY" not in env
    assert env["HOME"] == "/home/hermes" and env["TZ"] == "Asia/Tokyo"
    assert env["HERMES_KANBAN_TASK"] == "t_1" and env["HERMES_PROFILE"] == "cc-test"
    assert env["PATH"].startswith("/opt/claude:/opt/hermes:")


def test_tail_and_rate_limit(tmp_path):
    log = tmp_path / "t.jsonl"
    log.write_text("\n".join([
        json.dumps({"type": "system", "subtype": "api_retry", "error": "rate_limit"}),
        "not json",
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "reading the task"},
                                                                   {"type": "tool_use", "name": "Bash"}]}}),
        json.dumps({"type": "result", "subtype": "success", "result": "done"}),
    ]))
    t = worker.tail(log)
    assert "reading the task" in t and "[tool] Bash" in t and "[result] success: done" in t
    assert worker.hit_rate_limit(log)
    log.write_text(json.dumps({"type": "result", "result": "the words rate_limit in text"}))
    assert not worker.hit_rate_limit(log)


# -- board CLI -----------------------------------------------------------------------------------------------------
class Runner:
    def __init__(self, stdout="[]", code=0, stderr=""):
        self.calls, self.out, self.code, self.err = [], stdout, code, stderr

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        assert not kw.get("shell") and kw["timeout"] <= 30
        return subprocess.CompletedProcess(cmd, self.code, self.out, self.err)


def test_board_calls():
    r = Runner(json.dumps([{"id": "t_1"}]))
    b = Board("hermes", "exo", r)
    assert b.tasks("cc-coder", "ready") == [{"id": "t_1"}]
    assert r.calls[0] == ["hermes", "kanban", "--board", "exo", "list", "--assignee", "cc-coder",
                          "--status", "ready", "--json"]
    b.claim("t_1", 4500)
    assert r.calls[1][-3:] == ["t_1", "--ttl", "4500"]
    r.out = json.dumps({"task": {"status": "running"}})
    assert b.status("t_1") == "running"


def test_failed_claim_is_false_not_an_error():
    assert Board("hermes", "exo", Runner(code=1, stderr="not ready")).claim("t_1", 60) is False
    with pytest.raises(BoardError, match="boom"):
        Board("hermes", "exo", Runner(code=1, stderr="boom")).tasks("cc-x", "ready")


# -- dispatcher ----------------------------------------------------------------------------------------------------
def test_claims_and_spawns_up_to_capacity(tmp_path):
    board = FakeBoard(ready=[{"id": f"t_{i}", "created_at": i} for i in range(3)])
    d, procs, _ = make(tmp_path, board)
    d.tick()
    assert [p[0] for p in procs] == ["t_0", "t_1"]          # max_in_progress = 2
    assert board.claims[0] == ("t_0", 10 * 60 + 900)        # TTL outlives the runtime limit
    assert len(board.comments) == 2 and d.state.spawned_today == 2


def test_priority_then_age(tmp_path):
    board = FakeBoard(ready=[{"id": "old", "created_at": 1}, {"id": "urgent", "created_at": 5, "priority": 2}])
    d, procs, _ = make(tmp_path, board)
    d.tick()
    assert [p[0] for p in procs] == ["urgent", "old"]


def test_lost_claim_spawns_nothing(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}])
    board.claimable = False
    d, procs, _ = make(tmp_path, board)
    d.tick()
    assert procs == [] and d.state.spawned_today == 0


def test_pause_file_and_daily_cap(tmp_path):
    board = FakeBoard(ready=[{"id": f"t_{i}", "created_at": i} for i in range(5)])
    d, procs, _ = make(tmp_path, board)
    d.settings.pause_file.parent.mkdir(parents=True, exist_ok=True)
    d.settings.pause_file.touch()
    d.tick()
    assert procs == []
    d.settings.pause_file.unlink()
    for _ in range(4):
        d.tick()
        for _, _, p in procs:
            p.code = 0
    assert len(procs) == 3                                  # daily_cap = 3


def test_worker_that_completes_is_left_alone(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}], status="done")
    d, procs, _ = make(tmp_path, board)
    d.tick()
    procs[0][2].code = 0
    d.tick()
    assert board.blocks == [] and board.attached and not d.running


def test_worker_that_exits_without_complete_is_blocked(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}], status="running")
    d, procs, _ = make(tmp_path, board)
    d.tick()
    (tmp_path / "runs" / "t_1.jsonl").write_text(json.dumps({"type": "result", "subtype": "success", "result": "hi"}))
    procs[0][2].code = 0
    d.tick()
    task_id, reason, kind = board.blocks[0]
    assert task_id == "t_1" and "without complete or block" in reason and "[result] success: hi" in reason


def test_runtime_limit_sigint_then_sigterm(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}], status="running")
    d, procs, clock = make(tmp_path, board)
    d.tick()
    p = procs[0][2]
    clock["t"] += 10 * 60 + 1
    d.tick()
    assert p.signals == [signal.SIGINT]
    clock["t"] += 31
    d.tick()
    assert p.signals == [signal.SIGINT, signal.SIGTERM]
    p.code = 130
    d.tick()
    assert "max runtime" in board.blocks[0][1]


def test_rate_limit_pauses_spawning(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}, {"id": "t_2"}, {"id": "t_3"}], status="done")
    d, procs, clock = make(tmp_path, board)
    d.tick()
    (tmp_path / "runs" / "t_1.jsonl").write_text(
        json.dumps({"type": "system", "subtype": "api_retry", "error": "rate_limit"}))
    procs[0][2].code = 1
    d.tick()
    assert [p[0] for p in procs] == ["t_1", "t_2"]          # no new spawn after the rate limit
    clock["t"] += 61 * 60
    d.tick()
    assert [p[0] for p in procs] == ["t_1", "t_2", "t_3"]


def test_spawn_failure_blocks_instead_of_holding_the_claim(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}])
    d, _, _ = make(tmp_path, board)

    def boom(*a):
        raise subprocess.CalledProcessError(128, "git worktree add")
    d.prepare_cwd = boom
    d.tick()
    assert board.blocks and board.blocks[0][2] == "capability" and not d.running


def test_recover_blocks_orphans(tmp_path):
    board = FakeBoard(running=[{"id": "t_9"}])
    d, _, _ = make(tmp_path, board)
    assert d.recover() == ["t_9"] and "restarted" in board.blocks[0][1]


def test_state_survives_restart(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}])
    d, _, _ = make(tmp_path, board)
    d.tick()
    again = Dispatcher(d.settings, {"exo": FakeBoard()})
    assert again.state.spawned_today == 1


def test_stop_all_interrupts_then_terminates(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}, {"id": "t_2"}])
    d, procs, _ = make(tmp_path, board)
    d.tick()
    procs[0][2].code = 0                                    # this one exits on SIGINT
    d.stop_all(wait_seconds=1, sleep=lambda s: None)
    assert procs[0][2].signals == [signal.SIGINT]
    assert procs[1][2].signals == [signal.SIGINT, signal.SIGTERM]


def test_auth_failure_detected(tmp_path):
    # Seen live on the droplet before /login: the result text is "Not logged in · Please run /login".
    log = tmp_path / "t.jsonl"
    log.write_text(json.dumps({"type": "result", "subtype": "success", "result": "Not logged in · Please run /login"}))
    assert worker.auth_failed(log)
    log.write_text(json.dumps({"type": "system", "subtype": "api_retry", "error": "authentication_failed"}))
    assert worker.auth_failed(log)
    log.write_text(json.dumps({"type": "result", "subtype": "success", "result": "wrote the note"}))
    assert not worker.auth_failed(log)


def test_auth_failure_pauses_instead_of_draining_the_queue(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}, {"id": "t_2"}, {"id": "t_3"}], status="running")
    d, procs, _ = make(tmp_path, board)
    d.settings = d.settings.__class__(**{**d.settings.__dict__, "max_in_progress": 1})
    d.tick()
    (tmp_path / "runs" / "t_1.jsonl").write_text(json.dumps({"type": "result", "result": "Not logged in · Please run /login"}))
    procs[0][2].code = 1
    d.tick()
    assert [p[0] for p in procs] == ["t_1"]
    assert "not logged in" in board.blocks[0][1].lower() and "/login" in board.blocks[0][1]

import json
import re
import signal
import subprocess
from pathlib import Path

import pytest

from cc_dispatcher import verify, worker
from cc_dispatcher.board import Board, BoardError
from cc_dispatcher.config import load
from cc_dispatcher.dispatcher import Dispatcher

REPO_LANES = Path(__file__).resolve().parents[2] / "cc-lanes" / "lanes.toml"


# -- fixtures ------------------------------------------------------------------------------------------------------
def lanes_dir(tmp_path, extra="", mode="acceptEdits", name="cc-test"):
    tmp_path.mkdir(parents=True, exist_ok=True)
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
    def __init__(self, ready=(), running=(), status="done", body=""):
        self.ready, self.running_tasks, self.final = list(ready), list(running), status
        self.claims, self.comments, self.blocks, self.attached = [], [], [], []
        self.claimable = True
        self.body, self.created = body, []

    def task(self, task_id):
        return {"title": "Fix the parser", "body": self.body, "status": self.final}

    def create(self, title, body, assignee, created_by, parents=(), initial_status=None):
        self.created.append({"title": title, "body": body, "assignee": assignee, "parents": parents, "status": initial_status})
        return {"id": f"t_new{len(self.created)}"}

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


def make_from(path, board):
    settings = load(path)
    procs = []

    def spawn(settings, lane, board_name, task_id, cwd, log_path):
        p = FakeProc()
        procs.append((task_id, cwd, p))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()
        return p
    clock = {"t": 1_800_000_000.0}
    d = Dispatcher(settings, {"exo": board}, spawn=spawn, prepare_cwd=lambda s, l, t, base=None: l.cwd,
                   clock=lambda: clock["t"])
    return d, procs, clock


def make(tmp_path, board, **kw):
    return make_from(lanes_dir(tmp_path, **kw), board)


def make_verified(tmp_path, board, outcome):
    path = lanes_dir(tmp_path)
    path.write_text(path.read_text() + '\n[verifiers.unit]\ncmd = ["true"]\nlanes = ["cc-test"]\n')
    d, procs, clock = make_from(path, board)
    d.run_verifier = lambda v, cwd, env: outcome
    return d, procs, clock


def finish(d, procs):
    d.tick()
    procs[0][2].code = 0
    d.tick()


PASS = verify.Outcome("unit", 0, True, 905.0, "905 passed", 1.0)
FAIL = verify.Outcome("unit", 1, False, 900.0, "3 failed", 1.0)


def test_dispatch_line_comes_first_in_the_log(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}])
    d, procs, _ = make(tmp_path, board)
    d.tick()
    first = json.loads((tmp_path / "runs" / "t_1.jsonl").read_text().splitlines()[0])
    assert first["type"] == "cc_dispatch" and first["task_id"] == "t_1" and first["lane"] == "cc-test"


def test_passing_verify_comments_and_records(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}], body="Do it\nverify: unit")
    d, procs, _ = make_verified(tmp_path, board, PASS)
    finish(d, procs)
    assert any("Verified: unit passed (905" in c[1] for c in board.comments)
    assert board.created == []
    lines = [json.loads(l) for l in (tmp_path / "runs" / "t_1.jsonl").read_text().splitlines()]
    assert lines[-1]["type"] == "cc_verify" and lines[-1]["passed"] is True


def test_failing_verify_opens_a_retry_card_on_the_same_lane(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}], body="Do it\nverify: unit")
    d, procs, _ = make_verified(tmp_path, board, FAIL)
    finish(d, procs)
    [card] = board.created
    assert card["title"] == "[retry 2/3] Fix the parser" and card["assignee"] == "cc-test"
    assert card["parents"] == ("t_1",) and card["status"] is None and "cc-attempt: 2/3" in card["body"]


def test_last_failed_attempt_escalates_as_a_blocked_card(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}], body="Do it\nverify: unit\ncc-attempt: 3/3")
    d, procs, _ = make_verified(tmp_path, board, FAIL)
    finish(d, procs)
    [card] = board.created
    assert card["title"] == "[needs Konrad] Fix the parser" and card["status"] == "blocked"


def test_unknown_verifier_escalates_without_running_anything(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}], body="verify: nope")
    d, procs, _ = make_verified(tmp_path, board, PASS)
    d.run_verifier = lambda *a: (_ for _ in ()).throw(AssertionError("must not run"))
    finish(d, procs)
    [card] = board.created
    assert card["status"] == "blocked" and "not in lanes.toml" in card["body"]


def test_verifier_crash_is_contained_and_dispatch_continues(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}, {"id": "t_2"}], body="verify: unit")
    d, procs, _ = make_verified(tmp_path, board, PASS)
    d.run_verifier = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
    d.tick()
    procs[0][2].code = 0
    d.tick()
    assert any("crashed" in c[1] and "boom" in c[1] for c in board.comments)
    last = json.loads((tmp_path / "runs" / "t_1.jsonl").read_text().splitlines()[-1])
    assert last["type"] == "cc_verify" and last["passed"] is False
    board.ready.append({"id": "t_3"})
    d.tick()
    assert "t_3" in [p[0] for p in procs]


def test_failed_card_creation_is_not_reported_as_opened(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}], body="verify: unit")
    board.create = lambda *a: (_ for _ in ()).throw(BoardError("nope"))
    d, procs, _ = make_verified(tmp_path, board, FAIL)
    finish(d, procs)
    assert any("could not open the retry card" in c[1] for c in board.comments)
    assert not any("opened retry" in c[1] for c in board.comments)


def test_attempt_limit_is_clamped_to_max_attempts(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}], body="verify: unit\ncc-attempt: 3/99")
    d, procs, _ = make_verified(tmp_path, board, FAIL)
    finish(d, procs)
    [card] = board.created
    assert card["status"] == "blocked"


def test_no_verify_line_means_no_check(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}], body="just do it")
    d, procs, _ = make_verified(tmp_path, board, FAIL)
    finish(d, procs)
    assert board.created == [] and not any("Verified" in c[1] for c in board.comments)


def test_blocked_worker_is_not_verified(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}], body="verify: unit", status="blocked")
    d, procs, _ = make_verified(tmp_path, board, FAIL)
    finish(d, procs)
    assert board.created == []


# -- config --------------------------------------------------------------------------------------------------------
def test_repo_lanes_file_loads():
    s = load(REPO_LANES)
    assert set(s.lanes) == {"cc-coder", "cc-research", "cc-writer", "cc-reviewer"}
    for lane in s.lanes.values():
        assert "Bash(sudo *)" in lane.denied_tools and "Read(//home/trader/**)" in lane.denied_tools
        # A single leading slash is relative to the project, so an absolute rule written that way never matches.
        assert not [r for r in lane.denied_tools if re.match(r"\w+\(/[^/]", r)]
        assert "Kanban worker protocol" in lane.prompt
    assert s.lanes["cc-coder"].worktree and "WebSearch" in s.lanes["cc-research"].allowed_tools
    assert set(s.verifiers) == {"argo-exo-tests", "cc-dispatcher-tests"}
    for v in s.verifiers.values():
        # The droplet's system python3 has no pytest or the repo's deps; the worker PATH is minimal, so absolute.
        assert v.cmd[0] == "/home/hermes/.venvs/exo-verify/bin/python" and v.cmd[1:3] == ("-m", "pytest")
        assert v.lanes == ("cc-coder",)                  # code-running checks only where Bash already runs tests
    assert s.max_attempts == 3


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


def test_the_cap_day_rolls_at_midnight_in_the_configured_zone(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}])
    d, procs, clock = make(tmp_path, board)
    clock["t"] = 1792000000.0                               # 2026-10-14 17:46 UTC = 2026-10-15 02:46 Tokyo
    d.tick()
    assert d.settings.day_timezone == "Asia/Tokyo" and d.state.day == "2026-10-15" and d.state.spawned_today == 1
    board.ready = [{"id": "t_2"}]
    clock["t"] = 1792000000.0 + 19 * 3600                   # 2026-10-15 12:46 UTC: still the 15th in Tokyo (21:46)
    procs[0][2].code = 0                                    # the first worker finished; the tick reaps it, then spawns
    d.tick()
    assert d.state.day == "2026-10-15" and d.state.spawned_today == 2
    board.ready = [{"id": "t_3"}]
    clock["t"] = 1792000000.0 + 23 * 3600                   # 2026-10-15 16:46 UTC = 01:46 Tokyo on the 16th: fresh cap
    procs[1][2].code = 0
    d.tick()
    assert d.state.day == "2026-10-16" and d.state.spawned_today == 1


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


# -- verifiers ----------------------------------------------------------------------------------------------------
VERIFIERS = '''
[verifiers.unit]
cmd = ["python3", "-m", "pytest", "-q"]
lanes = ["cc-test"]
timeout_seconds = 120
metric_regex = "(\\\\d+) passed"
'''


def test_verifiers_load_with_defaults(tmp_path):
    path = lanes_dir(tmp_path)
    path.write_text(path.read_text() + VERIFIERS)
    s = load(path)
    v = s.verifiers["unit"]
    assert v.cmd == ("python3", "-m", "pytest", "-q") and v.cwd == "worker" and v.timeout_seconds == 120
    assert v.metric_regex == r"(\d+) passed"
    assert s.max_attempts == 3


def test_verifier_needs_a_command_and_a_safe_name(tmp_path):
    path = lanes_dir(tmp_path)
    path.write_text(path.read_text() + '\n[verifiers.unit]\ncmd = []\n')
    with pytest.raises(ValueError, match="cmd"):
        load(path)
    path = lanes_dir(tmp_path / "b")
    path.write_text(path.read_text() + '\n[verifiers."Bad Name"]\ncmd = ["true"]\n')
    with pytest.raises(ValueError, match="verifier"):
        load(path)


def test_verifier_validates_regex_timeout_and_cwd(tmp_path):
    path = lanes_dir(tmp_path)
    path.write_text(path.read_text() + '\n[verifiers.bad]\ncmd = ["true"]\nmetric_regex = "(?P<bad"\n')
    with pytest.raises(ValueError, match="metric_regex is invalid"):
        load(path)
    path = lanes_dir(tmp_path / "c")
    path.write_text(path.read_text() + '\n[verifiers.bad]\ncmd = ["true"]\ntimeout_seconds = 0\n')
    with pytest.raises(ValueError, match="timeout_seconds must be > 0"):
        load(path)
    path = lanes_dir(tmp_path / "d")
    path.write_text(path.read_text() + '\n[verifiers.bad]\ncmd = ["true"]\ncwd = "relative/path"\n')
    with pytest.raises(ValueError, match="cwd must be"):
        load(path)


def test_verifier_timeout_above_ten_minutes_is_rejected(tmp_path):
    path = lanes_dir(tmp_path)
    path.write_text(path.read_text() + '\n[verifiers.slow]\ncmd = ["true"]\ntimeout_seconds = 601\n')
    with pytest.raises(ValueError, match="timeout_seconds"):
        load(path)


def test_verifier_needs_a_non_empty_list_of_known_lanes(tmp_path):
    for i, extra in enumerate(["", 'lanes = []\n', 'lanes = "cc-test"\n', 'lanes = ["cc-nope"]\n']):
        path = lanes_dir(tmp_path / str(i))
        path.write_text(path.read_text() + '\n[verifiers.unit]\ncmd = ["true"]\n' + extra)
        with pytest.raises(ValueError, match="lanes"):
            load(path)


def test_verifier_on_a_lane_it_is_not_allowed_on_escalates_without_running(tmp_path):
    path = lanes_dir(tmp_path)
    (path.parent / "cc-other.md").write_text("ROLE")
    path.write_text(path.read_text() + '\n[lanes.cc-other]\ndescription = "other"\n'
                    '\n[verifiers.unit]\ncmd = ["true"]\nlanes = ["cc-other"]\n')
    board = FakeBoard(ready=[{"id": "t_1", "assignee": "cc-test"}], body="verify: unit")
    d, procs, _ = make_from(path, board)
    d.run_verifier = lambda *a: (_ for _ in ()).throw(AssertionError("must not run"))
    finish(d, procs)
    [card] = board.created
    assert card["status"] == "blocked" and card["title"].startswith("[needs Konrad]")
    assert "verifier unit is not allowed on lane cc-test" in card["body"]
    last = json.loads((tmp_path / "runs" / "t_1.jsonl").read_text().splitlines()[-1])
    assert last["type"] == "cc_verify" and last["passed"] is False and "not allowed" in last["output"]


def test_retry_card_names_the_failed_card_it_builds_on(tmp_path):
    board = FakeBoard(ready=[{"id": "t_1"}], body="Do it\nverify: unit")
    d, procs, _ = make_verified(tmp_path, board, FAIL)
    finish(d, procs)
    [card] = board.created
    assert verify.retry_of(card["body"]) == "t_1"


def test_a_retry_of_a_retry_builds_on_the_card_that_just_failed(tmp_path):
    board = FakeBoard(ready=[{"id": "t_2"}], body="Do it\nverify: unit\ncc-attempt: 2/3\ncc-retry-of: t_1")
    d, procs, _ = make_verified(tmp_path, board, FAIL)
    finish(d, procs)
    [card] = board.created
    assert verify.retry_of(card["body"]) == "t_2" and card["body"].count("cc-retry-of:") == 1


def test_escalation_card_also_names_the_failed_card(tmp_path):
    board = FakeBoard(ready=[{"id": "t_3"}], body="verify: unit\ncc-attempt: 3/3\ncc-retry-of: t_2")
    d, procs, _ = make_verified(tmp_path, board, FAIL)
    finish(d, procs)
    [card] = board.created
    assert card["status"] == "blocked" and verify.retry_of(card["body"]) == "t_3"


def test_worktree_retry_is_prepared_from_the_failed_cards_branch(tmp_path):
    board = FakeBoard(ready=[{"id": "t_r"}], body="Do it\nverify: unit\ncc-retry-of: t_1")
    d, procs, _ = make(tmp_path, board, extra="worktree = true")
    seen = []
    d.prepare_cwd = lambda s, lane, t, base=None: seen.append((t, base)) or "/work"
    d.tick()
    assert seen == [("t_r", "cc/t_1")]
    board2 = FakeBoard(ready=[{"id": "t_n"}], body="fresh card")
    d2, _, _ = make(tmp_path / "b", board2, extra="worktree = true")
    seen2 = []
    d2.prepare_cwd = lambda s, lane, t, base=None: seen2.append((t, base)) or "/work"
    d2.tick()
    assert seen2 == [("t_n", None)]


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _repo_settings(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "root", cwd=repo)
    path = lanes_dir(tmp_path, extra=f'worktree = true\ncwd = "{repo}"')
    s = load(path)
    return s, s.lanes["cc-test"]


def test_prepare_cwd_starts_a_retry_from_the_parent_branch(tmp_path):
    s, lane = _repo_settings(tmp_path)
    parent = Path(worker.prepare_cwd(s, lane, "t_1"))
    (parent / "feature.py").write_text("work = 1\n")
    _git("add", "feature.py", cwd=parent)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "attempt 1", cwd=parent)
    retry = Path(worker.prepare_cwd(s, lane, "t_2", base="cc/t_1"))
    assert (retry / "feature.py").read_text() == "work = 1\n"
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=retry).strip() == "cc/t_2"


def test_prepare_cwd_falls_back_to_head_when_the_parent_branch_is_gone(tmp_path):
    s, lane = _repo_settings(tmp_path)
    retry = Path(worker.prepare_cwd(s, lane, "t_2", base="cc/t_missing"))
    assert not (retry / "feature.py").exists()
    assert _git("rev-parse", "HEAD", cwd=retry) == _git("rev-parse", "main", cwd=tmp_path / "repo")

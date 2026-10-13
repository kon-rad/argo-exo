import json
import subprocess
from datetime import datetime, timezone

from cc_dispatcher import verify
from cc_dispatcher.board import Board
from cc_dispatcher.config import Verifier

V = Verifier(name="unit", cmd=("python3", "-m", "pytest", "-q"), cwd="worker", timeout_seconds=60,
             metric_regex=r"(\d+) passed")


def test_requested_and_attempt_lines():
    body = "Fix the parser.\n\nverify: unit\ncc-attempt: 2/3\n"
    assert verify.requested(body) == "unit" and verify.attempt(body) == (2, 3)
    assert verify.requested("no check here") is None and verify.attempt("") is None
    assert verify.requested("verify: rm -rf /") is None          # not a name: never runs anything


def test_run_passes_and_reads_the_metric():
    seen = {}

    def fake(cmd, **kw):
        seen.update(cmd=cmd, cwd=kw["cwd"], timeout=kw["timeout"], env=kw["env"])
        return subprocess.CompletedProcess(cmd, 0, stdout="..\n905 passed in 3.2s\n", stderr="")
    out = verify.run(V, "/work/t_1", {"PATH": "/usr/bin"}, runner=fake)
    assert out.passed and out.exit_code == 0 and out.metric == 905
    assert seen["cmd"] == ["python3", "-m", "pytest", "-q"] and seen["cwd"] == "/work/t_1" and seen["timeout"] == 60


def test_run_fails_on_exit_code_and_on_timeout():
    fail = verify.run(V, "/w", {}, runner=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "3 failed, 900 passed", ""))
    assert not fail.passed and fail.exit_code == 1 and fail.metric == 900

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    t = verify.run(V, "/w", {}, runner=slow)
    assert not t.passed and t.exit_code is None and "timed out after 60" in t.output


def test_record_appends_a_line_the_ledger_reads(tmp_path):
    log = tmp_path / "t_1.jsonl"
    log.write_text('{"type":"result","session_id":"s"}\n')
    out = verify.Outcome("unit", 1, False, 900.0, "3 failed", 1.5)
    verify.record(log, out, "t_1", "cc-coder", now=datetime(2026, 10, 14, tzinfo=timezone.utc))
    last = json.loads(log.read_text().splitlines()[-1])
    assert last == {"type": "cc_verify", "task_id": "t_1", "lane": "cc-coder", "name": "unit", "passed": False,
                    "exit_code": 1, "metric": 900.0, "output": "3 failed", "seconds": 1.5, "at": "2026-10-14T00:00:00+00:00"}


def test_retry_and_escalation_bodies():
    out = verify.Outcome("unit", 1, False, None, "E   assert 1 == 2", 2.0)
    body = "Fix the parser.\nverify: unit\ncc-attempt: 1/3\n"
    retry = verify.retry_body(body, out, 2, 3)
    assert "cc-attempt: 2/3" in retry and "cc-attempt: 1/3" not in retry
    assert "verify: unit" in retry and "assert 1 == 2" in retry
    esc = verify.escalation_body(body, out, 3, 3)
    assert "failed 3 of 3" in esc and "assert 1 == 2" in esc
    assert verify.base_title("[retry 2/3] Fix the parser") == "Fix the parser"


class Runner:
    def __init__(self, out="{}"):
        self.calls, self.out = [], out

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=self.out, stderr="")


def test_board_create_with_parent_and_blocked_status():
    r = Runner('{"id":"t_2"}')
    Board("/h/hermes", "exo", run=r).create("T", "B", "cc-coder", "cc-dispatcher", parents=("t_1",), initial_status="blocked")
    cmd = r.calls[0]
    assert cmd[:4] == ["/h/hermes", "kanban", "--board", "exo"]
    assert "--parent" in cmd and cmd[cmd.index("--parent") + 1] == "t_1"
    assert cmd[cmd.index("--initial-status") + 1] == "blocked"
    assert cmd[-2:] == ["--", "T"]


def test_board_task_reads_title_and_body():
    r = Runner('{"task":{"title":"T","body":"verify: unit","status":"done"}}')
    assert Board("h", "exo", run=r).task("t_1") == {"title": "T", "body": "verify: unit", "status": "done"}

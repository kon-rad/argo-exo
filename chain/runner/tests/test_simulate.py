import json
import subprocess
from pathlib import Path

import pytest
from exo_guardian.simulate import CRE_DIR, SimulationError, parse_result, run_simulation

RESULT = {"proposal_id": "p1", "verdict": "approve", "tx_hash": "0x" + "1" * 64}


def ok(stdout):
    return lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout, "")


def test_cre_dir_is_the_cre_project():
    assert (CRE_DIR / "project.yaml").is_file() and (CRE_DIR / "exo" / "workflow.yaml").is_file()


def test_parse_plain_json_line():
    out = "Compiling...\n[USER LOG] ok\nWorkflow Simulation Result:\n" + json.dumps(RESULT) + "\n"
    assert parse_result(out)["verdict"] == "approve"


def test_parse_json_encoded_string():
    out = "Workflow Simulation Result:\n" + json.dumps(json.dumps(RESULT)) + "\n"
    assert parse_result(out)["proposal_id"] == "p1"


def test_parse_result_on_the_marker_line_with_ansi_colour():
    out = "\x1b[32m✓ Workflow Simulation Result:\x1b[0m " + json.dumps(json.dumps(RESULT)) + "\n2026-10-07 done\n"
    assert parse_result(out) == RESULT


def test_parse_freeze_result():
    assert parse_result('Workflow Simulation Result:\n"{\\"ok\\":true,\\"report_tx\\":\\"0xab\\"}"\n')["ok"] is True


def test_log_lines_that_look_like_json_are_not_the_result():
    out = 'Workflow Simulation Result:\n' + json.dumps(RESULT) + '\n{"level":"info","msg":"bye"}\n'
    assert parse_result(out) == RESULT


@pytest.mark.parametrize("out", ["Error: secret POLICY_JSON not found\n", "", '{"level":"info"}\n', '"just a string"\n'])
def test_no_result_raises(out):
    with pytest.raises(SimulationError):
        parse_result(out)


def test_run_builds_the_cli_call(tmp_path):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        path = Path(cmd[cmd.index("--http-payload") + 1].lstrip("@"))
        seen["payload"] = json.loads(path.read_text())
        seen["path"] = path
        return subprocess.CompletedProcess(cmd, 0, "Workflow Simulation Result:\n" + json.dumps(RESULT), "")

    res, ms = run_simulation({"proposal_id": "p1"}, 0, True, run=run, cwd=tmp_path)
    assert res == RESULT and ms >= 0
    c = seen["cmd"]
    assert c[:4] == ["cre", "workflow", "simulate", "exo"] and "--broadcast" in c
    assert c[c.index("--trigger-index") + 1] == "0" and "--non-interactive" in c
    assert c[c.index("--target") + 1] == "mainnet"
    assert c[c.index("--http-payload") + 1].startswith("@")
    assert seen["payload"] == {"proposal_id": "p1"} and not seen["path"].exists()  # temp file removed
    assert seen["kw"]["cwd"] == str(tmp_path) and seen["kw"]["timeout"] > 0


def test_dry_run_has_no_broadcast_flag(tmp_path):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return ok('"{\\"ok\\":true}"')(cmd)

    run_simulation({"reason": "x"}, 1, False, run=run, cwd=tmp_path)
    assert "--broadcast" not in seen["cmd"] and seen["cmd"][seen["cmd"].index("--trigger-index") + 1] == "1"


def test_nonzero_exit_raises(tmp_path):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "Error: workflow failed to compile")
    with pytest.raises(SimulationError) as e:
        run_simulation({}, 0, False, run=run, cwd=tmp_path)
    assert e.value.kind == "simulator exited 1"


def test_timeout_raises_and_cleans_up(tmp_path):
    paths = []

    def run(cmd, **kw):
        paths.append(Path(cmd[cmd.index("--http-payload") + 1].lstrip("@")))
        raise subprocess.TimeoutExpired(cmd, 1)
    with pytest.raises(SimulationError) as e:
        run_simulation({}, 0, False, run=run, cwd=tmp_path)
    assert e.value.kind == "simulator timed out" and not paths[0].exists()


def test_missing_cli_raises(tmp_path):
    def run(cmd, **kw):
        raise FileNotFoundError("cre")
    with pytest.raises(SimulationError) as e:
        run_simulation({}, 0, False, run=run, cwd=tmp_path)
    assert e.value.kind == "simulator not runnable"


def test_unparseable_output_raises(tmp_path):
    with pytest.raises(SimulationError) as e:
        run_simulation({}, 0, False, run=ok("panic: something\n"), cwd=tmp_path)
    assert e.value.kind == "no workflow result"

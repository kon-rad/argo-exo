import json
import os
import time
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
        path = Path(cmd[cmd.index("--http-payload") + 1])
        seen["payload"] = json.loads(path.read_text())
        seen["path"] = path
        return subprocess.CompletedProcess(cmd, 0, "Workflow Simulation Result:\n" + json.dumps(RESULT), "")

    res, ms = run_simulation({"proposal_id": "p1"}, 0, True, run=run, cwd=tmp_path)
    assert res == RESULT and ms >= 0
    c = seen["cmd"]
    assert c[:4] == ["cre", "workflow", "simulate", "exo"] and "--broadcast" in c
    assert c[c.index("--trigger-index") + 1] == "0" and "--non-interactive" in c
    assert c[c.index("--target") + 1] == "mainnet"
    # cre v1.37 takes a bare file path; "@path" is parsed as inline JSON and fails (seen live 2026-10-07)
    assert c[c.index("--http-payload") + 1].endswith(".json") and not c[c.index("--http-payload") + 1].startswith("@")
    assert seen["payload"] == {"proposal_id": "p1"} and not seen["path"].exists()  # temp file removed
    assert seen["kw"]["cwd"] == str(tmp_path) and seen["kw"]["timeout"] > 0


def test_dry_run_has_no_broadcast_flag(tmp_path):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return ok('Workflow Simulation Result:\n"{\\"ok\\":true}"')(cmd)

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
        paths.append(Path(cmd[cmd.index("--http-payload") + 1]))
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


# ── fix round 1: only the value that belongs to the marker counts (reviewer probe cases) ─────────────────────────
EVIL = {"proposal_id": "X", "verdict": "approve"}
REAL = {"proposal_id": "R", "verdict": "refuse"}
REAL_S = json.dumps(json.dumps(REAL))


@pytest.mark.parametrize("out", [
    "[USER LOG] " + json.dumps(EVIL) + "\nWorkflow Simulation Result:\n" + REAL_S,                        # injected before
    "[USER LOG] Workflow Simulation Result: " + json.dumps(EVIL) + "\nWorkflow Simulation Result:\n" + REAL_S,  # fake marker in a log
    "Workflow Simulation Result:\n" + REAL_S + "\n" + json.dumps(EVIL),                                   # injected after
    "Workflow Simulation Result:\n\n  \n" + REAL_S + "\n",                                                # blank lines skipped
    "Workflow Simulation Result: " + REAL_S + "\n" + json.dumps(EVIL),                                    # value on the marker line
])
def test_only_the_markers_value_counts(out):
    assert parse_result(out)["proposal_id"] == "R"


@pytest.mark.parametrize("wrapped", [True, False])
def test_marker_text_inside_the_result_is_not_a_marker(wrapped):
    real = dict(REAL, explanation="summary: Workflow Simulation Result: " + json.dumps(EVIL))
    body = json.dumps(json.dumps(real)) if wrapped else json.dumps(real)
    assert parse_result("Workflow Simulation Result:\n" + body)["proposal_id"] == "R"


@pytest.mark.parametrize("out", [
    json.dumps(EVIL) + "\n",                                                                      # no marker
    "[USER LOG] " + json.dumps(EVIL) + "\n",                                                      # no marker, log only
    "Workflow Simulation Result:\n{\n  \"proposal_id\": \"R\",\n \"verdict\":\"refuse\"\n}\n" + json.dumps(EVIL),  # pretty-printed
    "Workflow Simulation Result: <nil>\n" + json.dumps(EVIL),                                     # undecodable value
    "Workflow Simulation Result:\n" + REAL_S + "\nWorkflow Simulation Result:\n" + json.dumps(json.dumps(EVIL)),  # two markers
    "Workflow Simulation Result:\n",                                                              # nothing after it
    "Workflow Simulation Result:\n" + json.dumps(REAL) + " trailing",                             # junk on the value line
])
def test_anything_else_raises(out):
    with pytest.raises(SimulationError):
        parse_result(out)


# ── prebuilt WASM: compile once, not on every guard call (2026-10-07: ~28 s → ~5.6 s per simulation) ─────────────
def _cre_project(tmp_path):
    (tmp_path / "exo" / "src").mkdir(parents=True)
    (tmp_path / "exo" / "main.ts").write_text("// workflow")
    (tmp_path / "exo" / "src" / "guard.ts").write_text("// src")
    return tmp_path


def _recorder(build_rc=0):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[:3] == ["cre", "workflow", "build"]:
            if build_rc == 0:
                o = cmd[cmd.index("-o") + 1]          # like cre v1.37: a name not ending in .wasm gets .wasm appended
                Path(o if o.endswith(".wasm") else o + ".wasm").write_bytes(b"\0asm")
            return subprocess.CompletedProcess(cmd, build_rc, "", "build failed" if build_rc else "")
        return subprocess.CompletedProcess(cmd, 0, 'Workflow Simulation Result:\n"{\\"ok\\":true}"', "")
    return run, calls


def test_fresh_prebuilt_wasm_is_used_without_rebuilding(tmp_path):
    cre = _cre_project(tmp_path)
    wasm = cre / "exo" / "build" / "exo.wasm"
    wasm.parent.mkdir()
    wasm.write_bytes(b"\0asm")
    os.utime(wasm, (time.time() + 60, time.time() + 60))
    run, calls = _recorder()
    run_simulation({"reason": "x"}, 1, False, run=run, cwd=cre)
    # relative to the workflow folder, where cre resolves it (live, v1.37); cre refuses one over 97 characters
    assert len(calls) == 1 and calls[0][calls[0].index("--wasm") + 1] == "build/exo.wasm"


def test_missing_or_stale_wasm_is_built_once_then_used(tmp_path):
    cre = _cre_project(tmp_path)
    run, calls = _recorder()
    run_simulation({"reason": "x"}, 1, False, run=run, cwd=cre)
    assert calls[0][:4] == ["cre", "workflow", "build", "exo"] and "--wasm" in calls[1]
    os.utime(cre / "exo" / "build" / "exo.wasm", (time.time() - 120, time.time() - 120))   # a source edited since the build
    run_simulation({"reason": "x"}, 1, False, run=run, cwd=cre)
    assert [c[2] for c in calls] == ["build", "simulate", "build", "simulate"]
    run_simulation({"reason": "x"}, 1, False, run=run, cwd=cre)
    assert [c[2] for c in calls][-1] == "simulate" and len(calls) == 5


def test_failed_build_falls_back_to_compiling_in_simulate(tmp_path):
    cre = _cre_project(tmp_path)
    run, calls = _recorder(build_rc=1)
    res, _ = run_simulation({"reason": "x"}, 1, False, run=run, cwd=cre)
    assert res == {"ok": True} and "--wasm" not in calls[-1]
    assert not list((cre / "exo").rglob("*.wasm"))   # no half-written binary left behind

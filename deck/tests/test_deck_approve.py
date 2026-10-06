import importlib.machinery, importlib.util, json, os
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin" / "deck-approve"


def load(tmp_path):
    os.environ["DECK_ROOT"] = str(tmp_path)
    loader = importlib.machinery.SourceFileLoader("deck_approve", str(BIN))
    spec = importlib.util.spec_from_loader("deck_approve", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_add_queues_oldest_first_and_rejects_non_json(tmp_path):
    mod = load(tmp_path)
    a, b, bad = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "bad.json"
    a.write_text(json.dumps({"summary": "first"}))
    b.write_text(json.dumps({"summary": "second"}))
    bad.write_text("not json")
    assert mod.main(["add", str(a)]) == 0
    assert mod.main(["add", str(b)]) == 0
    try:
        mod.main(["add", str(bad)])
        raise AssertionError("non-JSON was queued")
    except json.JSONDecodeError:
        pass
    queued = sorted((tmp_path / "state" / "tx-queue").glob("*.json"))
    assert [json.loads(p.read_text())["summary"] for p in queued] == ["first", "second"]


def test_mode_defaults_to_manual(tmp_path):
    assert load(tmp_path).mode() == "manual"

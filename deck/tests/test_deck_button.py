import importlib.machinery, importlib.util, json, os
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / "bin" / "deck-button"


def load(tmp_path):
    os.environ["DECK_ROOT"] = str(tmp_path)
    loader = importlib.machinery.SourceFileLoader("deck_button", str(BIN))
    spec = importlib.util.spec_from_loader("deck_button", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_defaults_without_a_file(tmp_path):
    conf = load(tmp_path).load()
    assert conf["p1"]["tap"].endswith("exo_deck.navcli next") and conf["p1"]["double"].endswith("deck-kiosk toggle")
    assert conf["p2"] == {"tap": "", "double": "", "hold": ""}


def test_set_clear_reset(tmp_path):
    mod = load(tmp_path)
    assert mod.main(["set", "p2", "tap", "deck-capture", "snap"]) == 0
    assert json.loads((tmp_path / "buttons.json").read_text())["p2"]["tap"] == "deck-capture snap"
    assert mod.main(["clear", "p2", "tap"]) == 0
    assert mod.load()["p2"]["tap"] == ""
    mod.main(["set", "p1", "hold", "deck-capture off"])
    assert mod.main(["reset"]) == 0
    assert mod.load()["p1"]["hold"] == ""


def test_rejects_unknown_button_or_gesture(tmp_path):
    mod = load(tmp_path)
    assert mod.main(["set", "p3", "tap", "x"]) == 1
    assert mod.main(["set", "p1", "triple", "x"]) == 1
    assert not (tmp_path / "buttons.json").exists()

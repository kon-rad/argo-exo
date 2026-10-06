import json
from exo_deck import state as st


def test_panel_by_name_number_and_cycle(tmp_path):
    assert st.get_panel(tmp_path) == "talk"
    assert st.set_panel(tmp_path, "approvals") == "approvals"
    assert st.set_panel(tmp_path, "5") == "wallets"
    assert st.set_panel(tmp_path, "next") == "cre"
    assert st.set_panel(tmp_path, "previous") == "wallets"
    st.set_panel(tmp_path, "talk")
    assert st.set_panel(tmp_path, "previous") == "media"      # wraps
    assert st.set_panel(tmp_path, "weather") is None and st.get_panel(tmp_path) == "media"


def test_switching_panel_resets_page(tmp_path):
    st.set_panel(tmp_path, "agents")
    assert st.page(tmp_path, +1) == 1 and st.page(tmp_path, +1) == 2
    assert st.page(tmp_path, -1) == 1
    st.set_panel(tmp_path, "media")
    assert st.page(tmp_path, None) == 0 and st.page(tmp_path, -1) == 0   # never negative


def test_conversation_log_is_capped(tmp_path):
    for i in range(12):
        st.append_turn(tmp_path, "you" if i % 2 == 0 else "hermes", f"line {i}", now=i, cap=10)
    turns = st.recent_turns(tmp_path, 7)
    assert [t["text"] for t in turns] == [f"line {i}" for i in range(5, 12)]
    assert len((tmp_path / "conversation.jsonl").read_text().splitlines()) == 10


def test_snapshot(tmp_path):
    (tmp_path / "tx-queue").mkdir()
    (tmp_path / "tx-queue" / "1_a.json").write_text(json.dumps({"summary": "x"}))
    (tmp_path / "listening").touch()
    (tmp_path / "approve-mode").write_text("auto\n")
    (tmp_path / "last-heard.txt").write_text("hi")
    s = st.snapshot(tmp_path)
    assert s["listening"] and s["mode"] == "auto" and s["pending"] == 1 and s["heard"] == "hi"
    assert s["frozen"] is False and s["panel"] == "talk" and s["page"] == 0

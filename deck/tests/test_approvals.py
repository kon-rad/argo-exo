import json
import os
import stat

from exo_deck import approvals as ap
from exo_deck import state as st

NOW = 1_800_000_000


def q(tmp_path, name, **item):
    d = tmp_path / "tx-queue"
    d.mkdir(exist_ok=True)
    base = {"id": name, "summary": f"send {name}", "explanation": "x", "risk": "low",
            "auto_eligible": False, "expires_at": NOW + 600}
    base.update(item)
    (d / f"{name}.json").write_text(json.dumps(base))


def test_mode_and_auto_needs_key_within_window(tmp_path):
    assert ap.mode(tmp_path) == "manual"
    ap.request_auto(tmp_path, NOW)
    assert ap.confirm_auto(tmp_path, NOW + 6) is False and ap.mode(tmp_path) == "manual"
    ap.request_auto(tmp_path, NOW)
    assert ap.confirm_auto(tmp_path, NOW + 3) is True and ap.mode(tmp_path) == "auto"
    assert not (tmp_path / "auto-request").exists()
    assert ap.confirm_auto(tmp_path, NOW + 4) is False         # one request, one confirmation


def test_key_without_request_never_turns_auto_on(tmp_path):
    assert ap.confirm_auto(tmp_path, NOW) is False and ap.mode(tmp_path) == "manual"
    (tmp_path / "auto-request").write_text("garbage")
    assert ap.confirm_auto(tmp_path, NOW) is False and ap.mode(tmp_path) == "manual"
    ap.request_auto(tmp_path, NOW + 100)                        # request from the future
    assert ap.confirm_auto(tmp_path, NOW) is False and ap.mode(tmp_path) == "manual"


def test_manual_switch_cancels_pending_request(tmp_path):
    ap.request_auto(tmp_path, NOW)
    ap.set_mode(tmp_path, "manual")
    assert ap.confirm_auto(tmp_path, NOW + 1) is False and ap.mode(tmp_path) == "manual"


def test_auto_only_takes_low_risk_eligible_items(tmp_path):
    q(tmp_path, "001_big", risk="high", auto_eligible=True)
    q(tmp_path, "002_new_recipient", risk="low", auto_eligible=False)
    q(tmp_path, "003_lunch", risk="low", auto_eligible=True)
    items = ap.pending(tmp_path, NOW)
    assert ap.next_auto(items, NOW)["id"] == "003_lunch"
    assert ap.next_manual(items)["id"] == "001_big"


def test_auto_rejects_loose_flags_and_bad_expiry(tmp_path):
    for n, kw in enumerate([{"auto_eligible": "true"}, {"auto_eligible": 1}, {"risk": "medium", "auto_eligible": True},
                            {"auto_eligible": True, "expires_at": "soon"}, {"auto_eligible": True, "expires_at": True}]):
        q(tmp_path, f"{n:03d}", **kw)
    q(tmp_path, "900_noexp", auto_eligible=True)
    (tmp_path / "tx-queue" / "900_noexp.json").write_text(json.dumps({"risk": "low", "auto_eligible": True}))
    assert ap.next_auto(ap.pending(tmp_path, NOW), NOW) is None


def test_next_auto_rechecks_expiry(tmp_path):
    q(tmp_path, "001", auto_eligible=True, expires_at=NOW + 1)
    items = ap.pending(tmp_path, NOW)
    assert ap.next_auto(items, NOW + 2) is None


def test_expired_items_leave_the_queue(tmp_path):
    q(tmp_path, "001_old", auto_eligible=True, expires_at=NOW - 1)
    q(tmp_path, "002_ok", auto_eligible=True)
    items = ap.pending(tmp_path, NOW)
    assert [i["id"] for i in items] == ["002_ok"]
    assert (tmp_path / "tx-expired" / "001_old.json").exists()
    assert not (tmp_path / "tx-queue" / "001_old.json").exists()


def test_unreadable_item_is_rejected_not_queued(tmp_path):
    (tmp_path / "tx-queue").mkdir()
    (tmp_path / "tx-queue" / "001_bad.json").write_text("{nope")
    (tmp_path / "tx-queue" / "002_list.json").write_text("[1]")
    q(tmp_path, "003_ok")
    items = ap.pending(tmp_path, NOW)
    assert [i["id"] for i in items] == ["003_ok"] and ap.next_auto(items, NOW) is None
    assert (tmp_path / "tx-rejected" / "001_bad.json").exists() and (tmp_path / "tx-rejected" / "002_list.json").exists()
    assert ap.next_manual(items)["file"] == "003_ok.json"


def test_double_press_after_confirm_does_not_approve(tmp_path):
    q(tmp_path, "001_big", risk="high")
    ap.request_auto(tmp_path, NOW)
    assert ap.on_key(tmp_path, NOW + 1, 0.0)[0] == "confirmed-auto"
    assert ap.on_key(tmp_path, NOW + 1.3, NOW + 1)[0] == "ignored"      # inside the gap
    assert (tmp_path / "tx-queue" / "001_big.json").exists()
    action, dest = ap.on_key(tmp_path, NOW + 3, NOW + 1)                # a deliberate later press
    assert action == "approved" and dest.exists() and not (tmp_path / "tx-queue" / "001_big.json").exists()


def test_key_with_empty_queue_is_ignored(tmp_path):
    assert ap.on_key(tmp_path, NOW, 0.0) == ("ignored", None)


def test_release_rechecks_expiry(tmp_path):
    q(tmp_path, "001", expires_at=NOW + 1)
    item = ap.pending(tmp_path, NOW)[0]
    assert ap.release(tmp_path, item, NOW + 2) is None                  # expired since the snapshot
    assert (tmp_path / "tx-expired" / "001.json").exists() and not (tmp_path / "tx-approved").exists()


def test_auto_tick_only_releases_eligible(tmp_path):
    q(tmp_path, "001_hi", risk="high", auto_eligible=True)
    q(tmp_path, "002_ok", auto_eligible=True)
    assert ap.on_tick_auto(tmp_path, NOW, 0.0) is None                   # manual mode
    ap.set_mode(tmp_path, "auto")
    dest = ap.on_tick_auto(tmp_path, NOW, 0.0)
    assert dest.name == "002_ok.json" and (tmp_path / "tx-queue" / "001_hi.json").exists()
    assert ap.on_tick_auto(tmp_path, NOW, 0.0) is None                   # nothing else eligible


def test_explanation_is_truncated_in_panel(tmp_path):
    q(tmp_path, "001", explanation="x" * 1000)
    assert len(ap.panel(tmp_path, NOW, 0)["pending"][0]["explanation"]) == 120


def test_panel_shape(tmp_path):
    for i in range(9):
        q(tmp_path, f"{i:03d}_t")
    (tmp_path / "tx-approved").mkdir()
    (tmp_path / "tx-approved" / "000_done.json").write_text(json.dumps({"summary": "done"}))
    p = ap.panel(tmp_path, NOW, 0)
    assert p["pending_total"] == 9 and len(p["pending"]) == 7 and p["mode"] == "manual"
    assert p["approved"][0]["summary"] == "done" and p["auto_request"] is False
    assert len(ap.panel(tmp_path, NOW, 1)["pending"]) == 2      # page 2


def test_panel_auto_request_goes_stale(tmp_path):
    ap.request_auto(tmp_path, NOW)
    assert ap.panel(tmp_path, NOW + 2, 0)["auto_request"] is True
    assert ap.panel(tmp_path, NOW + 6, 0)["auto_request"] is False


def test_snapshot_agrees_with_approvals(tmp_path):
    q(tmp_path, "001")
    q(tmp_path, "002_old", expires_at=1)
    ap.set_mode(tmp_path, "auto")
    s = st.snapshot(tmp_path)
    assert s["mode"] == "auto" and s["pending"] == 1


def test_state_files_are_world_readable(tmp_path):
    st._write(tmp_path / "panel", "talk")
    assert stat.S_IMODE(os.stat(tmp_path / "panel").st_mode) == 0o644
    ap.set_mode(tmp_path, "auto")
    assert stat.S_IMODE(os.stat(tmp_path / "approve-mode").st_mode) == 0o644

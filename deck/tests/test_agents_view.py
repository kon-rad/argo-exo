from exo_deck import agents_view as av
from exo_deck.bridge_client import BridgeError


def test_columns_group_and_sort():
    tasks = [{"id": f"t{i}", "title": f"task {i}", "assignee": "builder", "created_at": i,
              "status": ["triage", "todo", "ready", "running", "done", "blocked", "archived"][i % 7]} for i in range(21)]
    out = av.columns(tasks)
    assert set(out["cols"]) == {"queued", "ready", "running", "done"}
    assert all(t["status"] in ("triage", "todo", "blocked") for t in out["cols"]["queued"])
    assert out["cols"]["done"][0]["created_at"] > out["cols"]["done"][-1]["created_at"]
    assert all(len(v) <= 5 for v in out["cols"].values()) and "archived" not in out["counts"]


def test_offline():
    class B:
        def board(self):
            raise BridgeError("down")
    assert av.panel(B()) == {"online": False, "cols": {}, "counts": {}}


def test_garbage_rows_are_ignored_and_fields_trimmed():
    out = av.columns([None, "x", {"status": "done", "created_at": None, "title": "t" * 500, "result": "secret" * 99}])
    assert len(out["cols"]["done"]) == 1
    row = out["cols"]["done"][0]
    assert len(row["title"]) <= 120 and "result" not in row

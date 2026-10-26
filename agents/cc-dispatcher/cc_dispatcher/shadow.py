"""Shadow check for the board cutover (kanban-cutover-plan.md phase 2): read the ready cards from both boards and
log where they disagree. Claims nothing, writes nothing but its own log line and state file.

A card on one side only is normal for a few minutes (the reconcile timer runs every 5 minutes); one that stays on one
side across two runs is a real difference and is reported as `stable`. Three days with no stable difference on any
board is the exit check for the claim cutover.

    python -m cc_dispatcher shadow [--config PATH]
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .board import BoardError


def ids(rows: list[dict]) -> dict[str, str]:
    return {r["id"]: (r.get("assignee") or "") for r in rows if r.get("id")}


def compare(hermes: dict[str, str], engine: dict[str, str]) -> dict:
    """What each side lists as ready that the other does not, and cards both list under different assignees."""
    return {
        "only_hermes": sorted(set(hermes) - set(engine)),
        "only_engine": sorted(set(engine) - set(hermes)),
        "assignee_differs": sorted(i for i in set(hermes) & set(engine) if hermes[i] != engine[i]),
    }


def stable(now_diff: dict, before: dict | None) -> dict:
    """The differences present in this run and the previous one."""
    if not before:
        return {k: [] for k in now_diff}
    return {k: sorted(set(now_diff[k]) & set(before.get(k, []))) for k in now_diff}


def run(hermes_boards: dict, engine_boards: dict, state_path: Path, log, clock=time.time) -> dict:
    previous = {}
    try:
        previous = json.loads(state_path.read_text())
    except (OSError, ValueError):
        pass
    report, current = {}, {}
    for name in hermes_boards:
        try:
            diff = compare(ids(hermes_boards[name].ready_tasks()), ids(engine_boards[name].ready_tasks()))
        except BoardError as exc:
            report[name] = {"error": str(exc)[:200]}
            continue
        current[name] = diff
        st = stable(diff, previous.get(name))
        report[name] = {**diff, "stable": st, "ok": not any(st.values())}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(current))
    line = {"at": int(clock()), "shadow": report}
    log(json.dumps(line))
    return line

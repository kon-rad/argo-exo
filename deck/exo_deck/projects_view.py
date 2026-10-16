"""Projects panel: the Second Brain project tracker's summary, read-only (Konrad, 2026-10-14).

Reads GET <EXO_PROJECTS_URL> (the app's /api/feed/projects) with `Authorization: Bearer <EXO_PROJECTS_TOKEN>`.
Every good answer is cached to state/projects.json, so a phone-hotspot drop or a droplet restart shows the last
summary with its age instead of an error. "More" (page) steps through three views:
  0 projects   top five by priority: stage, feature mix, next step, a flag when one needs Konrad
  1 needs      everything waiting on Konrad
  2 attention  failing checks, overdue, stale, in testing with no check
Design: secondbrain Projects/secondbrain-app/project-tracker-design.md §6.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import requests

from .state import _write

TIMEOUT = (3, 5)
VIEWS = ("projects", "needs", "attention")


def _fetch(url: str, token: str, get: Callable) -> dict:
    r = get(url, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
    if r.status_code == 401:
        raise PermissionError("token refused (EXO_PROJECTS_TOKEN)")
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        raise ValueError("unexpected answer from the projects feed")
    return data


def panel(url: str, token: str, cache: Path, page: int = 0, get: Callable = requests.get,
          now: Callable[[], float] = time.time) -> dict:
    data, error, fetched_at = None, None, None
    if not url or not token:
        error = "not configured: set EXO_PROJECTS_URL and EXO_PROJECTS_TOKEN in /srv/deck/.env"
    else:
        try:
            data = _fetch(url, token, get)
            fetched_at = now()
            _write(cache, json.dumps({"fetched_at": fetched_at, "data": data}))
        except (requests.RequestException, ValueError, PermissionError) as exc:
            error = str(exc) if isinstance(exc, PermissionError) else f"feed unreachable ({type(exc).__name__})"
    if data is None:
        try:
            saved = json.loads(cache.read_text())
            data, fetched_at = saved["data"], saved["fetched_at"]
        except (OSError, ValueError, KeyError):
            return {"error": error or "no data"}
    view = VIEWS[page % len(VIEWS)]
    return {
        "view": view, "page": page % len(VIEWS), "pages": len(VIEWS),
        "totals": data.get("totals", {}),
        "projects": data.get("projects", [])[:5],
        "needs": data.get("needs_owner", [])[:7],
        "attention": data.get("attention", [])[:7],
        "gaps": data.get("gaps", {}),
        "shipped_7d": data.get("shipped_7d", 0),
        "fetched_at": fetched_at,
        "stale": error is not None,
        "error": error,
    }

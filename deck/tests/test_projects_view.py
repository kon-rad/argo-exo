import json

import requests

from exo_deck import projects_view
from exo_deck.router import Route, route

FEED = {
    "totals": {"features": {"idea": 13, "building": 7, "testing": 9, "live": 19}},
    "projects": [{"slug": f"p{i}", "title": f"P{i}", "stage": "building", "counts": {}, "next": "x", "needs_konrad": i == 0}
                 for i in range(7)],
    "needs_konrad": [{"project": "p0", "feature": None, "title": "P0", "detail": "Upload build 11"}],
    "attention": [{"project": "p1", "feature": "f", "title": "F", "detail": "testing, no activity for 9d"}],
    "gaps": {"goals_uncovered": ["W3"]},
    "shipped_7d": 2,
}


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_fetches_with_the_bearer_token_and_caches(tmp_path):
    seen = {}

    def get(url, headers, timeout):
        seen.update(url=url, auth=headers["Authorization"], timeout=timeout)
        return Resp(200, FEED)

    cache = tmp_path / "projects.json"
    j = projects_view.panel("https://sb.example/api/feed/projects", "tok", cache, 0, get=get, now=lambda: 1000.0)
    assert seen == {"url": "https://sb.example/api/feed/projects", "auth": "Bearer tok", "timeout": (3, 5)}
    assert j["view"] == "projects" and len(j["projects"]) == 5 and j["stale"] is False and j["fetched_at"] == 1000.0
    assert json.loads(cache.read_text())["data"]["shipped_7d"] == 2


def test_more_steps_through_projects_needs_and_attention(tmp_path):
    get = lambda url, headers, timeout: Resp(200, FEED)  # noqa: E731
    views = [projects_view.panel("u", "t", tmp_path / "c.json", p, get=get)["view"] for p in range(4)]
    assert views == ["projects", "needs", "attention", "projects"]
    j = projects_view.panel("u", "t", tmp_path / "c.json", 1, get=get)
    assert j["needs"] == FEED["needs_konrad"]


def test_offline_shows_the_cached_summary_marked_stale(tmp_path):
    cache = tmp_path / "projects.json"
    projects_view.panel("u", "t", cache, 0, get=lambda url, headers, timeout: Resp(200, FEED), now=lambda: 500.0)

    def down(url, headers, timeout):
        raise requests.ConnectionError("no route")

    j = projects_view.panel("u", "t", cache, 0, get=down)
    assert j["stale"] is True and j["fetched_at"] == 500.0 and j["error"] == "feed unreachable (ConnectionError)"
    assert j["projects"][0]["title"] == "P0"


def test_a_refused_token_says_so_and_nothing_cached_is_an_error(tmp_path):
    j = projects_view.panel("u", "bad", tmp_path / "none.json", 0, get=lambda url, headers, timeout: Resp(401, {}))
    assert j == {"error": "token refused (EXO_PROJECTS_TOKEN)"}
    assert "not configured" in projects_view.panel("", "", tmp_path / "none.json", 0)["error"]


def test_garbage_from_the_feed_is_not_cached(tmp_path):
    cache = tmp_path / "projects.json"
    j = projects_view.panel("u", "t", cache, 0, get=lambda url, headers, timeout: Resp(200, ["nope"]))
    assert "unexpected answer" not in str(j) or j.get("error") and not cache.exists()
    assert not cache.exists()


def test_voice_opens_the_panel():
    assert route("show projects") == Route("nav", "projects")
    assert route("open the roadmap") == Route("nav", "projects")

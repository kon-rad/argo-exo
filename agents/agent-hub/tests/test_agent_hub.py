import json
import subprocess
import time
from pathlib import Path

import pytest

from agent_hub.chats import ChatError, ChatManager, ChatSettings, chat_command, read_from
from agent_hub.hub import Hub
from cc_dispatcher.config import load

REPO_LANES = Path(__file__).resolve().parents[2] / "cc-lanes" / "lanes.toml"
TOKEN = "t" * 40
AUTH = f"Bearer {TOKEN}"

# A stand-in for `claude -p --input-format stream-json --output-format stream-json`: one init, then for each user
# line an assistant message and a result. "sleep" makes it hang (to test interrupt); argv is echoed in init.
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, sys, time, signal
signal.signal(signal.SIGINT, lambda *a: (print(json.dumps({"type": "result", "subtype": "interrupted", "result": ""}), flush=True), sys.exit(130)))
sid = "sess-resumed" if "--resume" in sys.argv else "sess-1"
print(json.dumps({"type": "system", "subtype": "init", "session_id": sid, "argv": sys.argv[1:]}), flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    text = msg["message"]["content"][0]["text"]
    if text == "sleep":
        time.sleep(30)
    print(json.dumps({"type": "stream_event", "event": {"delta": {"type": "input_json_delta"}}}), flush=True)
    print(json.dumps({"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": "ec"}}}), flush=True)
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "echo " + text}]}}), flush=True)
    print(json.dumps({"type": "result", "subtype": "success", "result": "echo " + text}), flush=True)
'''


@pytest.fixture
def fake_claude(tmp_path):
    p = tmp_path / "bin" / "claude"
    p.parent.mkdir()
    p.write_text(FAKE_CLAUDE)
    p.chmod(0o755)
    return str(p)


def settings(tmp_path, claude_bin, **kw):
    work = tmp_path / "vault"
    work.mkdir(exist_ok=True)
    return ChatSettings(root=tmp_path / "chats", claude_bin=claude_bin, cwd=str(work),
                        base_env={"HOME": str(tmp_path), "ANTHROPIC_API_KEY": "sk-nope"},
                        denied_tools=("Bash(sudo *)",), **kw)


def wait_for(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    raise AssertionError("timed out")


# -- chats ---------------------------------------------------------------------------------------------------------
def test_chat_command_uses_subscription_safe_flags(tmp_path):
    s = settings(tmp_path, "/opt/claude")
    m = ChatManager(s)
    meta = m.chats[m.create()["id"]].meta
    cmd = chat_command(s, meta)
    assert "--bare" not in cmd and "--dangerously-skip-permissions" not in cmd
    assert cmd[cmd.index("--input-format") + 1] == "stream-json"
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"
    assert cmd[cmd.index("--disallowedTools") + 1] == "Bash(sudo *)"
    assert "--resume" not in cmd
    meta.session_id = "abc"
    assert chat_command(s, meta)[-2:] == ["--resume", "abc"]


def test_chat_round_trip_and_replay(tmp_path, fake_claude):
    m = ChatManager(settings(tmp_path, fake_claude))
    cid = m.create()["id"]
    m.send(cid, "hello there")
    wait_for(lambda: m.get(cid).meta.status == "idle")
    feed = m.events(cid, 0)
    types = [e["type"] for e in feed["events"]]
    assert types[0] == "you" and "assistant" in types and types[-1] == "result"
    deltas = [e for e in feed["events"] if e["type"] == "stream_event"]
    assert len(deltas) == 1 and deltas[0]["event"]["delta"]["type"] == "text_delta"   # other deltas dropped
    chat = feed["chat"]
    assert chat["session_id"] == "sess-1" and chat["title"] == "hello there" and chat["last_result"] == "echo hello there"
    # Offsets: reading from the returned offset gives nothing new.
    assert m.events(cid, feed["offset"])["events"] == []
    # A second message goes to the same live process (no second init).
    m.send(cid, "again")
    wait_for(lambda: m.get(cid).meta.status == "idle" and m.events(cid, 0)["events"][-1].get("result") == "echo again")
    assert sum(1 for e in m.events(cid, 0)["events"] if e.get("subtype") == "init") == 1


def test_worker_env_drops_api_key(tmp_path, fake_claude):
    m = ChatManager(settings(tmp_path, fake_claude))
    cid = m.create()["id"]
    m.send(cid, "x")
    from agent_hub.chats import chat_env
    assert "ANTHROPIC_API_KEY" not in chat_env(m.s) and chat_env(m.s)["HOME"] == str(tmp_path)
    wait_for(lambda: m.get(cid).meta.status == "idle")


def test_closed_chat_resumes_with_session_id(tmp_path, fake_claude):
    m = ChatManager(settings(tmp_path, fake_claude))
    cid = m.create()["id"]
    m.send(cid, "one")
    wait_for(lambda: m.get(cid).meta.status == "idle")
    m.get(cid).close()
    wait_for(lambda: m.get(cid).meta.status == "closed")
    m.send(cid, "two")
    wait_for(lambda: m.get(cid).meta.status == "idle" and m.get(cid).meta.session_id == "sess-resumed")
    inits = [e for e in m.events(cid, 0)["events"] if e.get("subtype") == "init"]
    assert inits[-1]["argv"][-2:] == ["--resume", "sess-1"]


def test_busy_chat_refuses_and_interrupt_stops_it(tmp_path, fake_claude):
    m = ChatManager(settings(tmp_path, fake_claude))
    cid = m.create()["id"]
    m.send(cid, "sleep")
    with pytest.raises(ChatError) as e:
        m.send(cid, "more")
    assert e.value.status == 409
    m.get(cid).interrupt()
    wait_for(lambda: not m.get(cid).live())
    assert any(e.get("text") == "stopped by you" for e in m.events(cid, 0)["events"])


def test_live_cap_closes_the_oldest_idle_chat(tmp_path, fake_claude):
    m = ChatManager(settings(tmp_path, fake_claude, max_live=1))
    a, b = m.create()["id"], m.create()["id"]
    m.send(a, "hi")
    wait_for(lambda: m.get(a).meta.status == "idle")
    m.send(b, "hi")
    wait_for(lambda: not m.get(a).live())
    wait_for(lambda: m.get(b).meta.status == "idle")


def test_live_cap_refuses_when_all_are_working(tmp_path, fake_claude):
    m = ChatManager(settings(tmp_path, fake_claude, max_live=1))
    a, b = m.create()["id"], m.create()["id"]
    m.send(a, "sleep")
    with pytest.raises(ChatError) as e:
        m.send(b, "hi")
    assert e.value.status == 429
    m.get(a).interrupt()


def test_chats_survive_a_hub_restart(tmp_path, fake_claude):
    s = settings(tmp_path, fake_claude)
    m = ChatManager(s)
    cid = m.create()["id"]
    m.send(cid, "remember me")
    wait_for(lambda: m.get(cid).meta.status == "idle")
    again = ChatManager(s)
    assert again.list()[0]["id"] == cid and again.list()[0]["session_id"] == "sess-1"
    assert again.events(cid, 0)["events"][0]["text"] == "remember me"


def test_bad_inputs(tmp_path, fake_claude):
    m = ChatManager(settings(tmp_path, fake_claude))
    with pytest.raises(ChatError):
        m.get("../../etc")
    cid = m.create()["id"]
    with pytest.raises(ChatError):
        m.send(cid, "   ")
    with pytest.raises(ChatError):
        m.create(cwd=str(tmp_path / "nope"))
    with pytest.raises(ChatError):
        m.create(model="gpt-5")


def test_read_from_returns_only_complete_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text('{"a": 1}\n{"b": 2}\n{"c": ')
    r = read_from(p, 0)
    assert r["events"] == [{"a": 1}, {"b": 2}] and r["offset"] == len('{"a": 1}\n{"b": 2}\n')
    assert read_from(p, r["offset"])["events"] == []
    assert read_from(tmp_path / "missing", 0)["events"] == []


# -- hub routes ----------------------------------------------------------------------------------------------------
class FakeBoard:
    def __init__(self):
        self.created, self.comments = [], []

    def all_tasks(self):
        return [{"id": "t_abc123", "status": "ready", "assignee": "cc-writer"}]

    def show(self, task_id):
        return {"task": {"id": task_id}}

    def create(self, title, body, assignee, created_by):
        self.created.append((title, body, assignee, created_by))
        return {"id": "t_new000"}

    def comment(self, task_id, text, author):
        self.comments.append((task_id, text, author))


class Runner:
    def __init__(self, out="", code=0, err=""):
        self.calls, self.out, self.code, self.err = [], out, code, err

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        assert not kw.get("shell")
        return subprocess.CompletedProcess(cmd, self.code, self.out, self.err)


PROFILES = """ Profile   Model   Gateway
 ───────── ─────── ───────
 ◆default  x       running
  researcher x     stopped
  trader   —       stopped
"""


def hub(tmp_path, fake_claude, runner=None):
    board = FakeBoard()
    h = Hub(token=TOKEN, lanes=load(REPO_LANES), chats=ChatManager(settings(tmp_path, fake_claude)),
            telegram_script="/x/telegram-agent.sh", hermes_bin="hermes",
            run=runner or Runner(PROFILES), board_factory=lambda name: board)
    return h, board


def test_token_required(tmp_path, fake_claude):
    h, _ = hub(tmp_path, fake_claude)
    assert h.handle("GET", "/health", {}, None, "")[0] == 401
    assert h.handle("GET", "/health", {}, None, "Bearer " + "x" * 40)[0] == 401
    assert h.handle("GET", "/health", {}, None, AUTH) == (200, {"ok": True})


def test_agents_lists_lanes_and_profiles(tmp_path, fake_claude):
    h, _ = hub(tmp_path, fake_claude)
    _, out = h.handle("GET", "/agents", {}, None, AUTH)
    assert {l["name"] for l in out["lanes"]} == {"cc-coder", "cc-research", "cc-writer", "cc-reviewer"}
    assert [p["name"] for p in out["profiles"]] == ["default", "researcher", "trader"]


def test_create_task_validates_assignee(tmp_path, fake_claude):
    h, board = hub(tmp_path, fake_claude)
    ok = h.handle("POST", "/tasks", {}, {"title": "  write   it ", "body": "b", "assignee": "cc-writer"}, AUTH)
    assert ok == (200, {"id": "t_new000"}) and board.created[0] == ("write it", "b", "cc-writer", "secondbrain-app")
    assert h.handle("POST", "/tasks", {}, {"title": "x", "assignee": "nobody"}, AUTH)[0] == 400
    assert h.handle("POST", "/tasks", {}, {"title": "x", "assignee": "trader"}, AUTH)[0] == 400
    assert h.handle("POST", "/tasks", {}, {"title": "", "assignee": "cc-writer"}, AUTH)[0] == 400


def test_task_routes_validate_ids(tmp_path, fake_claude):
    h, board = hub(tmp_path, fake_claude)
    assert h.handle("GET", "/tasks/../../etc/log", {}, None, AUTH)[0] in (400, 404)
    assert h.handle("GET", "/board", {"board": "Bad Board"}, None, AUTH)[0] == 400
    assert h.handle("POST", "/tasks/t_abc123/comment", {}, {"text": "go"}, AUTH) == (200, {"ok": True})
    assert board.comments == [("t_abc123", "go", "konrad")]


def test_task_log_reads_the_dispatcher_runs_dir(tmp_path, fake_claude):
    h, _ = hub(tmp_path, fake_claude)
    h.lanes = h.lanes.__class__(**{**h.lanes.__dict__, "runs_dir": tmp_path})
    (tmp_path / "t_abc123.jsonl").write_text(json.dumps({"type": "assistant"}) + "\n")
    status, out = h.handle("GET", "/tasks/t_abc123/log", {"offset": "0"}, None, AUTH)
    assert status == 200 and out["events"] == [{"type": "assistant"}]


def test_telegram_routes(tmp_path, fake_claude):
    r = Runner('{"default":"claude","claude_running":true}\n')
    h, _ = hub(tmp_path, fake_claude, r)
    assert h.handle("GET", "/telegram", {}, None, AUTH) == (200, {"default": "claude", "claude_running": True})
    assert r.calls[-1] == ["/x/telegram-agent.sh", "status"]
    h.handle("POST", "/telegram", {}, {"default": "hermes"}, AUTH)
    assert r.calls[-1] == ["/x/telegram-agent.sh", "default", "hermes"]
    h.handle("POST", "/telegram", {}, {"claude": "off"}, AUTH)
    assert r.calls[-1] == ["/x/telegram-agent.sh", "claude", "off"]
    assert h.handle("POST", "/telegram", {}, {"default": "both"}, AUTH)[0] == 400
    r.code, r.err = 1, "telegram-agent: Claude's bot token equals Hermes's; two pollers on one bot break both"
    status, out = h.handle("POST", "/telegram", {}, {"claude": "on"}, AUTH)
    assert status == 409 and "two pollers" in out["error"]


def test_chat_routes(tmp_path, fake_claude):
    h, _ = hub(tmp_path, fake_claude)
    _, chat = h.handle("POST", "/chats", {}, {}, AUTH)
    status, _ = h.handle("POST", f"/chats/{chat['id']}/messages", {}, {"text": "hey"}, AUTH)
    assert status == 200
    wait_for(lambda: h.chats.get(chat["id"]).meta.status == "idle")
    _, feed = h.handle("GET", f"/chats/{chat['id']}/events", {"offset": "0"}, None, AUTH)
    assert feed["events"][-1]["result"] == "echo hey"
    assert h.handle("GET", "/chats/zzz/events", {}, None, AUTH)[0] == 404


# -- cards for bots (the work graph) --------------------------------------------------------------------------------
class OrgRunner(Runner):
    """`hermes profile list` answers with profiles; `cli-org task create` answers with the created card."""

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[0].endswith("cli-org"):
            assert kw["env"]["ORG_AGENT"] == "konrad" and kw["env"]["ORG_KANBAN_CMD"] == "hermes"
            if "fail-me" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "no feature \"secondbrain-app/nope\" in the tracker")
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"task_id": "t_bot0001", "link": {"task_id": "t_bot0001", "assignee": "rune", "project_slug": "secondbrain-app", "feature_slug": "org-page"}}) + "\n", "")
        return subprocess.CompletedProcess(cmd, 0, PROFILES, "")


def org_hub(tmp_path, fake_claude):
    import dataclasses
    agents = tmp_path / "org-agents.json"
    agents.write_text(json.dumps({"agents": {"konrad": {"runtime": "human", "role": "CEO"},
                                              "rune": {"role": "Platform engineer", "team": "platform", "model": "sonnet"},
                                              "lena": {"role": "Writer", "team": "media", "model": "sonnet"}}}))
    from cc_dispatcher.config import OrgSettings
    # On the Mac load() switches [org] off (the droplet paths are missing), so build the section by hand.
    lanes = dataclasses.replace(load(REPO_LANES), org=OrgSettings(vault=tmp_path, agents_file=agents, skill_file=tmp_path / "skill.md", cli_org="/x/cli-org"))
    board = FakeBoard()
    runner = OrgRunner(PROFILES)
    h = Hub(token=TOKEN, lanes=lanes, chats=ChatManager(settings(tmp_path, fake_claude)),
            telegram_script="/x/telegram-agent.sh", hermes_bin="hermes", run=runner, board_factory=lambda name: board)
    return h, board, runner


def test_agents_lists_the_bots(tmp_path, fake_claude):
    h, _, _ = org_hub(tmp_path, fake_claude)
    _, out = h.handle("GET", "/agents", {}, None, AUTH)
    assert [(b["name"], b["team"], b["family"]) for b in out["bots"]] == [("rune", "platform", "org"), ("lena", "media", "org")]


def test_create_task_for_a_bot_goes_through_cli_org(tmp_path, fake_claude):
    h, board, runner = org_hub(tmp_path, fake_claude)
    code, out = h.handle("POST", "/tasks", {}, {"title": "Add the tasks route", "body": "GET /api/org/tasks", "assignee": "rune",
                                                 "project": "secondbrain-app", "feature": "org-page"}, AUTH)
    assert code == 200, out
    assert out["id"] == "t_bot0001" and out["link"]["assignee"] == "rune"
    cmd = [c for c in runner.calls if c[0] == "/x/cli-org"][0]
    assert cmd[:5] == ["/x/cli-org", "task", "create", "rune", "Add the tasks route"]
    assert cmd[cmd.index("--project") + 1] == "secondbrain-app" and cmd[cmd.index("--feature") + 1] == "org-page"
    assert cmd[cmd.index("--body") + 1] == "GET /api/org/tasks" and cmd[cmd.index("--board") + 1] == "exo"
    assert board.created == []          # never the direct kanban path for a bot
    # A bot needs a project; cli-org's refusal comes back as a 400 with its words.
    assert h.handle("POST", "/tasks", {}, {"title": "x", "assignee": "rune"}, AUTH)[0] == 400
    code, out = h.handle("POST", "/tasks", {}, {"title": "fail-me", "assignee": "rune", "project": "secondbrain-app", "feature": "nope"}, AUTH)
    assert code == 400 and "no feature" in out["error"]
    # Lanes keep the direct path.
    assert h.handle("POST", "/tasks", {}, {"title": "lane work", "assignee": "cc-writer"}, AUTH)[0] == 200 and board.created

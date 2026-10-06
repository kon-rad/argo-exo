"""Decide from the first words whether a transcript is a question (talk) or a job (delegate).

Rules, in order:
  nav / control phrases ("show approvals", "panel 3", "more", "auto approve off", "open 4")  → handled locally
  "have|ask|tell|get [the] <agent> [agent] [to] …"  → delegate to <agent>, if it's a known agent
  "note|journal|remember …"                         → delegate to the librarian
  "build me …"                                       → delegate to the builder (whole sentence kept)
  anything else                                      → talk (answered at once by the default profile)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

AGENTS: tuple[str, ...] = ("librarian", "trader", "portfolio", "wallet", "builder", "researcher")

_ADDRESS = re.compile(r"^(?:have|ask|tell|get)\s+(?:the\s+)?(?P<agent>\w+)(?:\s+agent)?\s+(?:to\s+)?(?P<rest>.+)$", re.I)
_NOTE = re.compile(r"^(?:note|journal|remember)\b[\s,:;.-]*(?P<rest>.+)$", re.I)
_BUILD = re.compile(r"^build\s+me\b", re.I)


@dataclass(frozen=True)
class Route:
    kind: str
    text: str
    agent: str | None = None


PANEL_WORDS = {
    "talk": "talk", "chat": "talk", "terminal": "talk", "agents": "agents", "board": "agents", "kanban": "agents",
    "approvals": "approvals", "queue": "approvals", "pending": "approvals", "transactions": "transactions",
    "history": "transactions", "wallets": "wallets", "wallet": "wallets", "assets": "wallets", "portfolio": "wallets",
    "cre": "cre", "guardian": "cre", "workflows": "cre", "body": "body", "ring": "body", "sleep": "body",
    "sensors": "sensors", "deck": "sensors", "media": "media", "camera": "media", "photos": "media",
    "videos": "media", "library": "media",
}
NUMBERS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
_SHOW = re.compile(r"^(?:show|open|go to|switch to)\s+(?:me\s+)?(?:the\s+)?(?P<what>\w+)(?:\s+(?:panel|view|page))?$", re.I)
_PANEL_N = re.compile(r"^panel\s+(?P<n>\w+)$", re.I)
_OPEN_N = re.compile(r"^open\s+(?:number\s+)?(?P<n>\d|one|two|three|four|five|six|seven|eight|nine)$", re.I)
_AUTO = re.compile(r"^(?:(?:turn|switch)\s+)?auto[\s-]?approve\s+(?P<state>on|off)(?:[\s,]+please)?$", re.I)


def _local(text: str) -> Route | None:
    """Navigation and control phrases, handled on the deck. Whole-sentence matches only."""
    t = text.strip(" .!?,…").lower()
    if t in ("next", "next panel"):
        return Route("nav", "next")
    if t in ("previous", "previous panel", "last panel"):
        return Route("nav", "previous")
    if t in ("more", "back"):
        return Route("nav", t)
    if t == "close":
        return Route("control", "close")
    m = _AUTO.match(t)
    if m:
        return Route("control", f"auto-{m['state'].lower()}")
    m = _OPEN_N.match(t)
    if m:
        return Route("media-open", NUMBERS.get(m["n"].lower(), m["n"]))
    m = _PANEL_N.match(t)
    if m:
        n = NUMBERS.get(m["n"].lower(), m["n"])
        if n.isdigit() and 1 <= int(n) <= 9:
            return Route("nav", n)
    m = _SHOW.match(t)
    if m and m["what"].lower() in PANEL_WORDS:
        return Route("nav", PANEL_WORDS[m["what"].lower()])
    return None


def route(transcript: str, agents: Sequence[str] = AGENTS) -> Route:
    text = " ".join(transcript.split())
    if not text:
        return Route("empty", "")
    local = _local(text)
    if local:
        return local
    m = _ADDRESS.match(text)
    if m and m["agent"].lower() in agents:
        return Route("delegate", m["rest"].strip(), m["agent"].lower())
    m = _NOTE.match(text)
    if m:
        return Route("delegate", m["rest"].strip(), "librarian")
    if _BUILD.match(text):
        return Route("delegate", text, "builder")
    return Route("talk", text)

"""Decide from the first words whether a transcript is a question (talk) or a job (delegate).

Rules, in order:
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


def route(transcript: str, agents: Sequence[str] = AGENTS) -> Route:
    text = " ".join(transcript.split())
    if not text:
        return Route("empty", "")
    m = _ADDRESS.match(text)
    if m and m["agent"].lower() in agents:
        return Route("delegate", m["rest"].strip(), m["agent"].lower())
    m = _NOTE.match(text)
    if m:
        return Route("delegate", m["rest"].strip(), "librarian")
    if _BUILD.match(text):
        return Route("delegate", text, "builder")
    return Route("talk", text)

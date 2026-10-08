"""The waitlist: one Markdown file on the server, one email per line. Emails are never logged or echoed back."""
from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path

# Stricter than the claims EMAIL_RE: nothing that could read as Markdown or break a line in the file.
WAITLIST_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}$")
HEADER = "# Exo waitlist\n\nOne line per email, oldest first. Times are UTC.\n\n"
_LINE_RE = re.compile(r"^- (\S+) · ")


class Waitlist:
    def __init__(self, path, clock=None):
        self.path = Path(path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()

    def emails(self) -> set[str]:
        if not self.path.exists():
            return set()
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return {m[1].lower() for m in map(_LINE_RE.match, lines) if m}

    def add(self, email: str) -> bool:
        """Append the email unless it's already there (case-insensitive). True if it was new."""
        if not WAITLIST_EMAIL_RE.match(email):
            raise ValueError("not an email")
        with self._lock:
            if email.lower() in self.emails():
                return False
            new = not self.path.exists()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                if new:
                    f.write(HEADER)
                f.write(f"- {email} · {self.clock().strftime('%Y-%m-%d %H:%M')}\n")
            return True

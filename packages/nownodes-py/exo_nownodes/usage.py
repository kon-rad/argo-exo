import fcntl
import json
import logging
import time
from pathlib import Path

log = logging.getLogger("exo-nownodes")


class Usage:
    """Counts requests per calendar month in a small JSON file shared by all processes on a host."""

    def __init__(self, path: Path, monthly_limit: int = 100_000):
        self.path, self.limit = Path(path), monthly_limit

    def _month(self) -> str:
        return time.strftime("%Y-%m")

    def add(self, n: int = 1) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                data = json.loads(f.read() or "{}")
            except json.JSONDecodeError:
                data = {}
            month = self._month()
            before = data.get(month, 0)
            data[month] = before + n
            f.seek(0); f.truncate(); f.write(json.dumps(data))
        if before < 0.8 * self.limit <= data[month]:
            log.warning("NOWNodes usage passed 80%% of the monthly limit: %d of %d", data[month], self.limit)
        return data[month]

    def month_total(self) -> int:
        try:
            return json.loads(self.path.read_text()).get(self._month(), 0)
        except (OSError, json.JSONDecodeError):
            return 0


def safe_add(counter, n: int = 1) -> None:
    """Count a request without ever losing a good result to a counter I/O failure."""
    if counter is None:
        return
    try:
        counter.add(n)
    except OSError as exc:
        log.warning("NOWNodes usage counter not updated: %s", exc)

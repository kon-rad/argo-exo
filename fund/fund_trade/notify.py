"""Telegram notices for the fund. Best effort: a failed notice never blocks or fails a trade.

FUND_TELEGRAM_TOKEN and FUND_TELEGRAM_CHAT go in ~/.argo-fund/env (or the environment). No token: silently does nothing."""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from .config import file_env


def send(text: str, env=None) -> bool:
    env = {**file_env(Path((env or os.environ).get("FUND_ENV_FILE", "~/.argo-fund/env")).expanduser()), **(env or os.environ)}
    token, chat = env.get("FUND_TELEGRAM_TOKEN", ""), env.get("FUND_TELEGRAM_CHAT", "")
    if not token or not chat:
        return False
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat, "text": text[:3500], "disable_web_page_preview": True}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=8).read()
        return True
    except Exception:  # noqa: BLE001  never include the URL (it holds the token) in anything raised or logged
        return False

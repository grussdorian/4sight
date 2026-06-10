from __future__ import annotations
import os
import threading
import urllib.parse
import urllib.request

TELEGRAM_API = "https://api.telegram.org"
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def severity_increased(prev: str | None, new: str | None) -> bool:
    """True only when `new` is a strictly higher severity than `prev`. A missing
    baseline (prev is None) is never an increase, so we don't alert on the first
    assessment after boot."""
    if prev is None or new is None:
        return False
    return SEVERITY_ORDER.get(new, 0) > SEVERITY_ORDER.get(prev, 0)


class TelegramNotifier:
    """Best-effort Telegram push.

    Reads TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from the environment when not
    given explicitly. Sending is fire-and-forget on a daemon thread with a short
    timeout, so a slow or unreachable Telegram never blocks or fails an
    assessment request. Disabled (a silent no-op) when either the token or the
    chat id is missing, which keeps tests and unconfigured deployments offline.
    """

    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        self.token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        threading.Thread(target=self._post, args=(text,), daemon=True).start()

    def _post(self, text: str) -> None:
        try:
            url = f"{TELEGRAM_API}/bot{self.token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text}).encode()
            urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=5)
        except Exception:
            pass   # alerts are best-effort; a failed push must never break a request

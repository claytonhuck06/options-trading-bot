"""Push alerts to the owner's phone through ntfy (free: https://ntfy.sh, phone app "ntfy").

The topic name works like a private address: anyone who knows it can read the alerts, so it
lives in the git-ignored .env file (OPTIONS_QUANT_NTFY_TOPIC), never in the repository.
Optional: OPTIONS_QUANT_NTFY_SERVER for a self-hosted server (default https://ntfy.sh).

Sending never raises and never blocks trading for long: on any failure the alert is written to
logs/alerts_unsent.jsonl and the program carries on. Urgent alerts (kill switch, assignment, a
missed day, a failed demo) go out immediately; routine trades arrive in the daily summary.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.request
from datetime import datetime, timezone

from core.config import PROJECT_ROOT
from core.logging_setup import get_logger, redact
from core.secrets import _read_dotenv

log = get_logger("alerts")
TOPIC_VAR, SERVER_VAR = "OPTIONS_QUANT_NTFY_TOPIC", "OPTIONS_QUANT_NTFY_SERVER"
DEFAULT_SERVER = "https://ntfy.sh"
UNSENT = PROJECT_ROOT / "logs" / "alerts_unsent.jsonl"
PRIORITIES = {"min": "1", "low": "2", "default": "3", "high": "4", "urgent": "5"}


def _setting(name: str) -> str | None:
    return os.environ.get(name) or _read_dotenv(PROJECT_ROOT / ".env").get(name) or None


def configured() -> bool:
    return _setting(TOPIC_VAR) is not None


def _tls() -> ssl.SSLContext:
    # The python.org build of Python ships without system certificates; use certifi's bundle when present.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> None:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout, context=_tls()) as resp:
        if resp.status >= 300:
            raise OSError(f"ntfy answered {resp.status}")


def send(title: str, message: str, priority: str = "default", tags: list[str] | None = None, timeout: float = 10.0) -> bool:
    """Send one push notification. Returns True if ntfy accepted it."""
    topic = _setting(TOPIC_VAR)
    message = redact(message)
    if not topic:
        log.info("alert not sent (no ntfy topic configured): %s — %s", title, message)
        return False
    server = (_setting(SERVER_VAR) or DEFAULT_SERVER).rstrip("/")
    # HTTP headers must be plain ASCII; the message body can be any text.
    headers = {"Title": title.encode("ascii", "replace").decode(), "Priority": PRIORITIES.get(priority, "3")}
    if tags:
        headers["Tags"] = ",".join(tags)
    try:
        _post(f"{server}/{topic}", message.encode(), headers, timeout)
        log.info("alert sent: %s", title)
        return True
    except Exception as exc:
        log.warning("alert could not be sent (%s): %s", type(exc).__name__, title)
        try:
            UNSENT.parent.mkdir(parents=True, exist_ok=True)
            with UNSENT.open("a") as f:
                f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "title": title, "message": message,
                                    "priority": priority, "error": type(exc).__name__}) + "\n")
        except OSError:
            pass
        return False


def urgent(title: str, message: str) -> bool:
    return send(title, message, "urgent", ["rotating_light"])
